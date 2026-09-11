"""Optional, bounded Hunspell pipe boundary used only by explicit spell checking."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import threading
import time

MAX_PROTOCOL_LINE_BYTES = 16 * 1024
MAX_RESPONSE_GROUP_LINES = 64
MAX_RESPONSE_GROUP_BYTES = 64 * 1024
MAX_SUGGESTIONS = 16
MAX_SUGGESTION_CODEPOINTS = 256
MAX_TOKEN_UTF8_BYTES = 4096
DEFAULT_IO_TIMEOUT_SECONDS = 2.0
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 2.0
MAX_DISCOVERY_OUTPUT_BYTES = 256 * 1024
MAX_AVAILABLE_DICTIONARIES = 128
MAX_DICTIONARY_BASE_PATH_BYTES = 4096
_CANCEL_POLL_SECONDS = 0.05


class HunspellError(RuntimeError): pass
class HunspellProcessError(HunspellError): pass
class HunspellProtocolError(HunspellError): pass
class HunspellTimeoutError(HunspellError): pass
class HunspellClosedError(HunspellError): pass


@dataclass(frozen=True)
class HunspellDictionary:
    dictionary_id: str
    base_path: str
    display_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.dictionary_id, str) or not self.dictionary_id or self.dictionary_id != Path(self.dictionary_id).name:
            raise ValueError("dictionary_id must be one safe basename")
        if any(ch in self.dictionary_id for ch in "\r\n\x00,/\\"):
            raise ValueError("dictionary_id contains unsafe characters")
        _validate_dictionary_base(self.base_path, require_files=False)
        if not isinstance(self.display_name, str) or not self.display_name or any(ch in self.display_name for ch in "\r\n\x00"):
            raise ValueError("display_name must be safe non-empty text")


def _validate_dictionary_base(base_path: str, *, require_files: bool) -> str:
    if not isinstance(base_path, str) or not base_path or any(ch in base_path for ch in "\r\n\x00,"):
        raise ValueError("dictionary base path is unsafe")
    path = Path(base_path)
    if not path.is_absolute():
        raise ValueError("dictionary base path must be absolute")
    if len(os.fsencode(base_path)) > MAX_DICTIONARY_BASE_PATH_BYTES:
        raise ValueError("dictionary base path exceeds the bounded size")
    if require_files:
        aff = Path(base_path + ".aff")
        dic = Path(base_path + ".dic")
        if not aff.is_file() or not dic.is_file():
            raise HunspellProcessError("selected Hunspell dictionary is no longer available")
    return str(path)


def _terminate_reap(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)
    else:
        proc.wait()


def _parse_discovered_dictionaries(raw: bytes) -> tuple[HunspellDictionary, ...]:
    if b"\x00" in raw:
        raise HunspellProtocolError("Hunspell dictionary discovery returned NUL data")
    bases: set[str] = set()
    for line in raw.splitlines():
        candidate_raw = line.strip()
        if not candidate_raw:
            continue
        candidate = os.fsdecode(candidate_raw)
        try:
            base = _validate_dictionary_base(candidate, require_files=True)
        except (ValueError, HunspellProcessError, OSError):
            continue
        bases.add(base)
    ordered = sorted(bases, key=lambda x: (Path(x).name.casefold(), x))[:MAX_AVAILABLE_DICTIONARIES]
    counts: dict[str, int] = {}
    for base in ordered:
        ident = Path(base).name
        counts[ident] = counts.get(ident, 0) + 1
    result = []
    for base in ordered:
        ident = Path(base).name
        label = ident if counts[ident] == 1 else f"{ident} — {Path(base).parent}"
        result.append(HunspellDictionary(ident, base, label))
    return tuple(result)


def discover_hunspell_dictionaries(
    executable: str,
    *,
    timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
) -> tuple[HunspellDictionary, ...]:
    """Discover verified Hunspell base dictionaries only on explicit request."""
    if not isinstance(executable, str) or not executable or not Path(executable).is_absolute():
        raise ValueError("executable must be an absolute resolved path")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        proc = subprocess.Popen(
            [executable, "-D"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            bufsize=0,
            close_fds=True,
            env=env,
        )
    except (OSError, ValueError) as exc:
        raise HunspellProcessError("Hunspell dictionary discovery could not be started") from exc
    output = bytearray()
    deadline = time.monotonic() + float(timeout_seconds)
    try:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        selector = selectors.DefaultSelector()
        try:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise HunspellClosedError("Hunspell dictionary discovery was cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HunspellTimeoutError("Hunspell dictionary discovery timed out")
                if not selector.select(min(remaining, _CANCEL_POLL_SECONDS)):
                    if proc.poll() is not None:
                        chunk = os.read(fd, 8192)
                        if chunk:
                            output.extend(chunk)
                        break
                    continue
                chunk = os.read(fd, 8192)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_DISCOVERY_OUTPUT_BYTES:
                    raise HunspellProtocolError("Hunspell dictionary discovery output exceeds the bounded size")
        finally:
            selector.close()
        rc = proc.wait(timeout=max(0.05, min(0.5, deadline - time.monotonic())))
        if rc != 0:
            raise HunspellProcessError("Hunspell dictionary discovery failed")
        return _parse_discovered_dictionaries(bytes(output))
    except HunspellError:
        _terminate_reap(proc)
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        _terminate_reap(proc)
        raise HunspellProcessError("Hunspell dictionary discovery failed") from exc
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass


@dataclass(frozen=True)
class HunspellResult:
    word: str
    correct: bool
    suggestions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.word, str) or not self.word:
            raise ValueError("word must be non-empty")
        if not isinstance(self.correct, bool):
            raise TypeError("correct must be bool")
        if self.correct and self.suggestions:
            raise ValueError("correct words cannot carry suggestions")


def resolve_hunspell_executable() -> str | None:
    """Resolve capability on demand; importing this module performs no discovery."""
    return shutil.which("hunspell")


def _safe_token(word: str) -> bytes:
    if not isinstance(word, str) or not word or any(ch in word for ch in "\r\n\x00"):
        raise ValueError("word must be a single non-empty protocol-safe token")
    encoded = word.encode("utf-8", errors="strict")
    if len(encoded) > MAX_TOKEN_UTF8_BYTES:
        raise ValueError("word exceeds the Hunspell token budget")
    return encoded


def _suggestions(raw: str, expected: int) -> tuple[str, ...]:
    items = () if not raw else tuple(x.strip() for x in raw.split(","))
    if len(items) != expected or any(not x for x in items):
        raise HunspellProtocolError("Hunspell suggestion count is malformed")
    for item in items:
        if len(item) > MAX_SUGGESTION_CODEPOINTS or len(item.encode("utf-8")) > MAX_SUGGESTION_CODEPOINTS * 4:
            raise HunspellProtocolError("Hunspell suggestion exceeds the bounded size")
        if any(ch in item for ch in "\r\n\x00"):
            raise HunspellProtocolError("Hunspell suggestion contains invalid control data")
    return items[:MAX_SUGGESTIONS]


def _parse_hunspell_record(word: str, line: str, *, require_word_match: bool) -> HunspellResult:
    _safe_token(word)
    if not isinstance(line, str) or not line or "\r" in line or "\n" in line or "\x00" in line:
        raise HunspellProtocolError("Hunspell returned a malformed response line")
    if len(line.encode("utf-8")) > MAX_PROTOCOL_LINE_BYTES:
        raise HunspellProtocolError("Hunspell response line exceeds the bounded size")
    if line == "*" or line == "-" or line.startswith("+ "):
        return HunspellResult(word, True)
    marker = line[:1]
    if marker == "#":
        parts = line.split()
        if len(parts) != 3 or not parts[2].isdigit():
            raise HunspellProtocolError("Hunspell no-suggestion response is malformed")
        _safe_token(parts[1])
        if require_word_match and parts[1] != word:
            raise HunspellProtocolError("Hunspell no-suggestion response word does not match the request")
        return HunspellResult(word, False)
    if marker in {"&", "?"}:
        head, sep, tail = line.partition(": ")
        parts = head.split()
        if not sep or len(parts) != 4 or parts[0] != marker or not parts[2].isdigit() or not parts[3].isdigit():
            raise HunspellProtocolError("Hunspell suggestion response is malformed")
        _safe_token(parts[1])
        if require_word_match and parts[1] != word:
            raise HunspellProtocolError("Hunspell suggestion response word does not match the request")
        count = int(parts[2])
        return HunspellResult(word, False, _suggestions(tail, count))
    raise HunspellProtocolError("Hunspell returned an unsupported protocol response")


def parse_hunspell_response(word: str, line: str) -> HunspellResult:
    """Parse one strict `hunspell -a` result line for one Graphium-owned token."""
    return _parse_hunspell_record(word, line, require_word_match=True)


def _parse_hunspell_group(word: str, lines: tuple[str, ...]) -> HunspellResult:
    if not lines:
        raise HunspellProtocolError("Hunspell returned an empty response group")
    if len(lines) == 1:
        return parse_hunspell_response(word, lines[0])
    parsed = tuple(
        _parse_hunspell_record(word, line, require_word_match=False) for line in lines
    )
    if all(result.correct for result in parsed):
        return HunspellResult(word, True)
    # Suggestions from component records cannot safely replace the entire Graphium span.
    return HunspellResult(word, False, ())


class HunspellPipeSession:
    """One explicitly-owned Hunspell child; synchronous I/O must be called off GTK main thread."""
    __slots__ = ("_exe", "_timeout", "_dictionary_base", "_proc", "_buffer", "_lock", "_request_lock", "_closed")

    def __init__(
        self,
        executable: str,
        *,
        timeout_seconds: float = DEFAULT_IO_TIMEOUT_SECONDS,
        dictionary_base: str | None = None,
    ) -> None:
        if not isinstance(executable, str) or not executable:
            raise ValueError("executable must be a resolved non-empty path")
        path = Path(executable)
        if not path.is_absolute():
            raise ValueError("executable must be an absolute resolved path")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._exe = str(path); self._timeout = float(timeout_seconds)
        self._dictionary_base = None if dictionary_base is None else _validate_dictionary_base(dictionary_base, require_files=False)
        self._proc: subprocess.Popen[bytes] | None = None; self._buffer = bytearray()
        self._lock = threading.Lock(); self._request_lock = threading.Lock(); self._closed = False

    @property
    def pid(self) -> int | None:
        return None if self._proc is None else self._proc.pid

    @property
    def dictionary_base(self) -> str | None:
        return self._dictionary_base

    def start(self) -> None:
        with self._lock:
            if self._closed: raise HunspellClosedError("Hunspell session is closed")
            if self._proc is not None: return
            try:
                argv = [self._exe, "-a", "-i", "UTF-8", "--check-apostrophe"]
                if self._dictionary_base is not None:
                    _validate_dictionary_base(self._dictionary_base, require_files=True)
                    argv.extend(["-d", self._dictionary_base])
                self._proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    shell=False, bufsize=0, close_fds=True,
                )
            except (OSError, ValueError) as exc:
                self._proc = None
                raise HunspellProcessError("Hunspell could not be started") from exc
        try:
            banner = self._readline(self._timeout)
            if not banner.startswith("@(#)"):
                raise HunspellProtocolError("Hunspell banner is malformed")
        except BaseException:
            self.close(); raise

    def check(self, word: str) -> HunspellResult:
        data = _safe_token(word)
        with self._request_lock:
            try:
                self.start()
                with self._lock:
                    proc = self._proc
                    if self._closed or proc is None:
                        raise HunspellClosedError("Hunspell session is closed")
                    if proc.poll() is not None:
                        raise HunspellProcessError("Hunspell exited before the request")
                    try:
                        assert proc.stdin is not None
                        proc.stdin.write(b"^" + data + b"\n"); proc.stdin.flush()
                    except (BrokenPipeError, OSError) as exc:
                        raise HunspellProcessError("Hunspell input pipe failed") from exc
                response_group = self._read_response_group(self._timeout)
                return _parse_hunspell_group(word, response_group)
            except HunspellError:
                self.close(); raise

    def _read_response_group(self, timeout: float) -> tuple[str, ...]:
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        total_bytes = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HunspellTimeoutError("Hunspell response group timed out")
            line = self._readline(remaining)
            if line == "":
                if not lines:
                    raise HunspellProtocolError("Hunspell returned an empty response group")
                return tuple(lines)
            lines.append(line)
            total_bytes += len(line.encode("utf-8")) + 1
            if len(lines) > MAX_RESPONSE_GROUP_LINES:
                raise HunspellProtocolError("Hunspell response group exceeds the bounded line count")
            if total_bytes > MAX_RESPONSE_GROUP_BYTES:
                raise HunspellProtocolError("Hunspell response group exceeds the bounded byte size")

    def _readline(self, timeout: float) -> str:
        proc = self._proc
        if proc is None or proc.stdout is None: raise HunspellClosedError("Hunspell session is not started")
        deadline = time.monotonic() + timeout
        try: fd = proc.stdout.fileno()
        except ValueError as exc: raise HunspellClosedError("Hunspell session was cancelled") from exc
        while True:
            pos = self._buffer.find(b"\n")
            if pos >= 0:
                if pos > MAX_PROTOCOL_LINE_BYTES:
                    raise HunspellProtocolError("Hunspell response line exceeds the bounded size")
                raw = bytes(self._buffer[:pos]); del self._buffer[:pos + 1]
                if raw.endswith(b"\r"): raw = raw[:-1]
                try: return raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc: raise HunspellProtocolError("Hunspell output is not strict UTF-8") from exc
            if len(self._buffer) > MAX_PROTOCOL_LINE_BYTES:
                raise HunspellProtocolError("Hunspell response line exceeds the bounded size")
            if self._closed: raise HunspellClosedError("Hunspell session was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise HunspellTimeoutError("Hunspell response timed out")
            selector = selectors.DefaultSelector()
            try:
                try: selector.register(fd, selectors.EVENT_READ)
                except (OSError, ValueError) as exc:
                    if self._closed: raise HunspellClosedError("Hunspell session was cancelled") from exc
                    raise HunspellProcessError("Hunspell output pipe failed") from exc
                if not selector.select(min(remaining, _CANCEL_POLL_SECONDS)):
                    continue
            finally: selector.close()
            try: chunk = os.read(fd, 4096)
            except OSError as exc:
                if self._closed: raise HunspellClosedError("Hunspell session was cancelled") from exc
                raise HunspellProcessError("Hunspell output pipe failed") from exc
            if not chunk:
                raise HunspellProcessError("Hunspell exited before completing a response")
            self._buffer.extend(chunk)

    def close(self) -> None:
        with self._lock:
            if self._closed: return
            self._closed = True; proc, self._proc = self._proc, None
        if proc is None: return
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None: stream.close()
            except OSError: pass
        if proc.poll() is None:
            try: proc.terminate(); proc.wait(timeout=min(self._timeout, 1.0))
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=1.0)
        else:
            proc.wait()

    cancel = close

    def __enter__(self):
        self.start(); return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
