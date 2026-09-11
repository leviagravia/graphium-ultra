from __future__ import annotations
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from graphium.infrastructure.hunspell_session import (
    MAX_AVAILABLE_DICTIONARIES,
    HunspellClosedError,
    HunspellDictionary,
    HunspellPipeSession,
    HunspellProcessError,
    HunspellProtocolError,
    HunspellTimeoutError,
    discover_hunspell_dictionaries,
)

DISCOVERY_SCRIPT = '''#!%s
import os,pathlib,sys,time
LOG=pathlib.Path(%r)
MODE=%r
LOG.write_text(repr(sys.argv)+'\\nLC_ALL='+os.environ.get('LC_ALL',''))
if '-D' in sys.argv:
    if MODE == 'hang': time.sleep(10)
    elif MODE == 'huge': print('x'*300000)
    elif MODE == 'fail': sys.exit(3)
    else:
        print('SEARCH PATH:')
        print('/not/a/dictionary')
        for line in %r: print(line)
    sys.exit(0)
print('@(#) Fake Hunspell 1.0', flush=True)
for raw in sys.stdin:
    word=raw.rstrip('\\n')[1:]
    print('*' if word == 'good' else '& '+word+' 1 0: fixed', flush=True)
    print('', flush=True)
'''


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class DictionarySelectionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def pair(self, parent: Path, name: str) -> Path:
        parent.mkdir(parents=True, exist_ok=True)
        base = parent / name
        base.with_suffix('.aff').write_text('SET UTF-8\n', encoding='utf-8')
        base.with_suffix('.dic').write_text('1\nword\n', encoding='utf-8')
        return base

    def fake(self, lines=(), mode='ok'):
        log = self.root / ('discover-' + mode + '.log')
        exe = self.root / 'hunspell'
        exe.write_text(DISCOVERY_SCRIPT % ('/usr/bin/python3', str(log), mode, tuple(map(str, lines))))
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        return exe, log

    def test_01_discovery_accepts_only_real_pairs_and_forces_c_locale(self):
        it = self.pair(self.root / 'one', 'it_IT')
        en = self.pair(self.root / 'two', 'en_GB')
        orphan = self.root / 'one' / 'orphan'
        orphan.with_suffix('.dic').write_text('1\nx\n')
        exe, log = self.fake((it, orphan, en))
        found = discover_hunspell_dictionaries(str(exe))
        self.assertEqual([x.dictionary_id for x in found], ['en_GB', 'it_IT'])
        self.assertEqual({x.base_path for x in found}, {str(en), str(it)})
        self.assertIn('LC_ALL=C', log.read_text())
        self.assertNotIn('/not/a/dictionary', {x.base_path for x in found})

    def test_02_duplicate_ids_are_stably_disambiguated_not_collapsed(self):
        a = self.pair(self.root / 'a', 'en_US')
        b = self.pair(self.root / 'b', 'en_US')
        exe, _ = self.fake((b, a, a))
        found = discover_hunspell_dictionaries(str(exe))
        self.assertEqual(len(found), 2)
        self.assertEqual([x.base_path for x in found], sorted((str(a), str(b))))
        self.assertEqual(len({x.display_name for x in found}), 2)
        self.assertTrue(all(x.display_name.startswith('en_US — ') for x in found))

    def test_03_discovery_failure_timeout_and_oversize_fail_closed(self):
        for mode, error in (('fail', HunspellProcessError), ('hang', HunspellTimeoutError), ('huge', HunspellProtocolError)):
            exe, _ = self.fake(mode=mode)
            start = time.monotonic()
            with self.assertRaises(error):
                discover_hunspell_dictionaries(str(exe), timeout_seconds=.35)
            self.assertLess(time.monotonic() - start, 1.5)

    def test_04_discovery_cancel_is_bounded_and_reaps_child(self):
        exe, _ = self.fake(mode='hang')
        stop = threading.Event()
        errors = []
        th = threading.Thread(target=lambda: self._capture(errors, lambda: discover_hunspell_dictionaries(str(exe), timeout_seconds=5, cancel_event=stop)))
        th.start(); time.sleep(.12); stop.set(); th.join(1.5)
        self.assertFalse(th.is_alive())
        self.assertTrue(errors and isinstance(errors[0], HunspellClosedError))

    def test_05_dictionary_count_is_bounded(self):
        lines = [self.pair(self.root / 'many', f'd{i:03d}') for i in range(MAX_AVAILABLE_DICTIONARIES + 5)]
        exe, _ = self.fake(lines)
        found = discover_hunspell_dictionaries(str(exe))
        self.assertEqual(len(found), MAX_AVAILABLE_DICTIONARIES)

    def test_06_system_default_argv_has_no_dictionary_option(self):
        exe, log = self.fake()
        s = HunspellPipeSession(str(exe)); self.assertTrue(s.check('good').correct); s.close()
        argv = log.read_text().splitlines()[0]
        self.assertNotIn("'-d'", argv)

    def test_07_explicit_dictionary_adds_exact_separate_d_pair(self):
        base = self.pair(self.root / 'dicts', 'it_IT')
        exe, log = self.fake()
        s = HunspellPipeSession(str(exe), dictionary_base=str(base)); self.assertTrue(s.check('good').correct); s.close()
        argv = log.read_text().splitlines()[0]
        self.assertIn("'-d', %r" % str(base), argv)

    def test_08_explicit_dictionary_is_revalidated_and_injection_rejected(self):
        base = self.pair(self.root / 'dicts', 'en_GB')
        exe, _ = self.fake()
        base.with_suffix('.aff').unlink()
        with self.assertRaises(HunspellProcessError): HunspellPipeSession(str(exe), dictionary_base=str(base)).start()
        with self.assertRaises(ValueError): HunspellPipeSession(str(exe), dictionary_base=str(base) + ',evil')
        with self.assertRaises(ValueError): HunspellPipeSession(str(exe), dictionary_base='relative/en_GB')

    @staticmethod
    def _capture(out, call):
        try: call()
        except BaseException as exc: out.append(exc)


class DictionaryModelTests(unittest.TestCase):
    def test_dataclass_rejects_unsafe_identity(self):
        with self.assertRaises(ValueError): HunspellDictionary('bad/name', '/tmp/x', 'x')
        with self.assertRaises(ValueError): HunspellDictionary('en_US', '/tmp/x,evil', 'x')


if __name__ == '__main__':
    unittest.main()
