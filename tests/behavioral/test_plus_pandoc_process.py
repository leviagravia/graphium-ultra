from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from graphium_plus.pandoc import PandocIdentity, prepare_pandoc_export_plan
from graphium_plus.pandoc_process import PandocArtifactBuilder, PandocOutputWorker, PandocProcessRunner
from graphium_plus.references import ReferenceFileToken, ReferenceRecord


_FAKE = r'''#!/usr/bin/env python3
import pathlib
import sys
import time
if "--version" in sys.argv:
    print("pandoc 3.1.11")
    print("Features: fake")
    raise SystemExit(0)
if "--sleep" in sys.argv:
    time.sleep(30)
if "--fail" in sys.argv:
    print("synthetic pandoc failure", file=sys.stderr)
    raise SystemExit(7)
output = None
writer = None
input_path = None
bibliography = None
for index, item in enumerate(sys.argv):
    if index == 1 and not item.startswith("-"):
        input_path = item
    if item == "--output": output = sys.argv[index + 1]
    if item == "--to": writer = sys.argv[index + 1]
    if item == "--bibliography": bibliography = sys.argv[index + 1]
if output:
    source = pathlib.Path(input_path).read_text(encoding="utf-8") if input_path else ""
    if "SLOW-PANDOC" in source:
        time.sleep(30)
    bib = pathlib.Path(bibliography).read_text(encoding="utf-8") if bibliography else ""
    payload = ("WRITER=" + str(writer) + "\nSOURCE=" + source + "\nBIB=" + bib).encode("utf-8")
    pathlib.Path(output).write_bytes(payload)
print("ok")
'''


class PlusPandocProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.executable = self.root / "pandoc-fake"
        self.executable.write_text(_FAKE, encoding="utf-8")
        self.executable.chmod(0o755)
        self.runner = PandocProcessRunner(executable_name=str(self.executable))

    def tearDown(self):
        self.runner.cancel_active()
        self.temp.cleanup()

    def _plan(self, *, format_id="html", text="Claim [@alpha2020]."):
        record = ReferenceRecord(key="alpha2020", title="Alpha", type="article", authors=("Alpha, A.",), year="2020")
        identity = self.runner.detect()
        return prepare_pandoc_export_plan(
            identity=identity,
            format_id=format_id,
            destination=self.root / ("paper" + {"html":".html","docx":".docx","odt":".odt","latex":".tex"}[format_id]),
            document_text=text,
            source_state_id=4,
            document_directory=self.root,
            reference_records=(record,),
            reference_token=ReferenceFileToken(False),
        )


    def test_artifact_builder_never_owns_final_publication(self):
        source = (Path(__file__).parents[2] / "graphium_plus/pandoc_process.py").read_text(encoding="utf-8")
        for forbidden in ("GuardedFileWriter", "observe_target(", ".commit(", "os.replace("):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("plan.destination", source)

    def test_detects_supported_external_pandoc(self):
        identity = self.runner.detect()
        self.assertEqual(identity.path, str(self.executable.resolve()))
        self.assertEqual(identity.version, "3.1.11")
        self.assertEqual(identity.version_parts, (3, 1, 11))

    def test_builder_uses_shell_free_typed_argv_and_returns_bytes_without_publishing_destination(self):
        plan = self._plan(format_id="docx")
        destination = Path(plan.destination)
        self.assertFalse(destination.exists())
        artifact = PandocArtifactBuilder(self.runner).build(plan)
        self.assertTrue(artifact.succeeded, artifact.message)
        self.assertFalse(destination.exists())
        self.assertIn(b"WRITER=docx", artifact.data)
        self.assertIn(b"SOURCE=Claim [@alpha2020].", artifact.data)
        self.assertIn(b"@article{alpha2020,", artifact.data)
        self.assertIsNotNone(artifact.process)
        argv = artifact.process.argv
        self.assertIn("--citeproc", argv)
        self.assertIn("--bibliography", argv)
        self.assertEqual(argv[0], str(self.executable.resolve()))

    def test_builder_omits_bibliography_args_when_document_has_no_citations(self):
        plan = self._plan(text="No citations.")
        artifact = PandocArtifactBuilder(self.runner).build(plan)
        self.assertTrue(artifact.succeeded)
        self.assertNotIn("--citeproc", artifact.process.argv)
        self.assertNotIn("--bibliography", artifact.process.argv)
        self.assertIn(b"BIB=", artifact.data)

    def test_nonzero_and_timeout_never_create_user_destination(self):
        failed = self.runner.run((str(self.executable.resolve()), "--fail"), timeout_seconds=2)
        self.assertEqual(failed.status, "error")
        self.assertEqual(failed.returncode, 7)
        self.assertIn("synthetic pandoc failure", failed.stderr)
        timed = self.runner.run((str(self.executable.resolve()), "--sleep"), timeout_seconds=0.2)
        self.assertEqual(timed.status, "timeout")
        self.assertIsNone(self.runner.active_pid)

    def test_external_cancel_terminates_exact_child(self):
        holder = {}
        thread = threading.Thread(
            target=lambda: holder.setdefault(
                "result", self.runner.run((str(self.executable.resolve()), "--sleep"), timeout_seconds=20)
            )
        )
        thread.start()
        deadline = time.monotonic() + 3
        while self.runner.active_pid is None and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = self.runner.active_pid
        self.assertIsNotNone(pid)
        self.assertTrue(self.runner.cancel_active())
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(holder["result"].status, "cancelled")
        self.assertIsNone(self.runner.active_pid)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_output_worker_runs_detection_and_build_off_caller_thread(self):
        worker = PandocOutputWorker(PandocArtifactBuilder(self.runner))
        worker.start_detect()
        deadline = time.monotonic() + 3
        identity = None
        while identity is None and time.monotonic() < deadline:
            identity = worker.poll_identity()
            if identity is None:
                time.sleep(0.01)
        self.assertIsNotNone(identity)
        self.assertEqual(identity.version, "3.1.11")

        plan = self._plan(format_id="html")
        worker.start_build(plan)
        deadline = time.monotonic() + 3
        artifact = None
        while artifact is None and time.monotonic() < deadline:
            artifact = worker.poll_artifact()
            if artifact is None:
                time.sleep(0.01)
        self.assertIsNotNone(artifact)
        self.assertTrue(artifact.succeeded)
        self.assertIn(b"WRITER=html5", artifact.data)
        self.assertTrue(worker.cancel_and_join())

    def test_output_worker_cancel_and_join_terminates_exact_build_child(self):
        worker = PandocOutputWorker(PandocArtifactBuilder(self.runner, timeout_seconds=20))
        plan = self._plan(format_id="html", text="SLOW-PANDOC")
        worker.start_build(plan)
        deadline = time.monotonic() + 3
        while self.runner.active_pid is None and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = self.runner.active_pid
        self.assertIsNotNone(pid)
        self.assertTrue(worker.cancel_and_join(timeout_seconds=4))
        self.assertFalse(worker.active)
        self.assertIsNone(self.runner.active_pid)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        artifact = worker.poll_artifact()
        self.assertEqual(artifact.status, "cancelled")

    def test_one_active_process_gate(self):
        holder = {}
        thread = threading.Thread(
            target=lambda: holder.setdefault(
                "result", self.runner.run((str(self.executable.resolve()), "--sleep"), timeout_seconds=20)
            )
        )
        thread.start()
        deadline = time.monotonic() + 3
        while self.runner.active_pid is None and time.monotonic() < deadline:
            time.sleep(0.01)
        with self.assertRaisesRegex(RuntimeError, "already active"):
            self.runner.run((str(self.executable.resolve()), "--version"), timeout_seconds=1)
        self.runner.cancel_active()
        thread.join(4)


if __name__ == "__main__":
    unittest.main()
