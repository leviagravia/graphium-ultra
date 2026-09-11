"""Tracked shell-free external Pandoc process and derived-artifact builder.

The builder writes only private temporary inputs/output and returns immutable
bytes.  It never publishes the user's final destination; A5B must perform stale
checks and use Graphium Core's single physical writer for that commit.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Iterable

from graphium_plus.pandoc import PandocExportPlan, PandocIdentity


_MINIMUM_VERSION = (2, 11, 0)
_VERSION_RE = re.compile(r"^pandoc\s+(?P<version>\d+(?:\.\d+)+)", re.IGNORECASE)
_CAPTURE_LIMIT = 64 * 1024
_MAX_OUTPUT_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PandocProcessResult:
    status: str
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_seconds: float

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


@dataclass(frozen=True, slots=True)
class PandocArtifact:
    status: str
    data: bytes = b""
    process: PandocProcessResult | None = None
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "built"


class PandocProcessRunner:
    """Own exactly one active Pandoc child and its cancellation boundary."""

    __slots__ = ("_executable_name", "_lock", "_active", "_cancel_requested")

    def __init__(self, *, executable_name: str = "pandoc") -> None:
        if not isinstance(executable_name, str) or not executable_name.strip():
            raise ValueError("executable_name is required")
        self._executable_name = executable_name.strip()
        self._lock = threading.RLock()
        self._active: subprocess.Popen | None = None
        self._cancel_requested = threading.Event()

    @property
    def active_pid(self) -> int | None:
        with self._lock:
            process = self._active
            return process.pid if process is not None and process.poll() is None else None

    def locate(self) -> str:
        found = shutil.which(self._executable_name)
        if not found:
            raise ValueError("Pandoc is not installed or is not available on PATH.")
        path = os.path.realpath(os.path.abspath(found))
        try:
            state = os.stat(path)
        except OSError as exc:
            raise ValueError(f"The detected Pandoc executable is unavailable: {exc}") from exc
        if not stat.S_ISREG(state.st_mode) or not os.access(path, os.X_OK):
            raise ValueError("The detected Pandoc executable is not a runnable regular file.")
        return path

    def detect(self, *, timeout_seconds: float = 5.0) -> PandocIdentity:
        path = self.locate()
        result = self.run((path, "--version"), timeout_seconds=timeout_seconds)
        if not result.succeeded:
            detail = result.stderr.strip() or result.stdout.strip() or result.status
            raise ValueError(f"Pandoc could not be started: {detail}")
        first = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        match = _VERSION_RE.match(first)
        if not match:
            raise ValueError("Pandoc returned an unrecognized version string.")
        version = match.group("version")
        parts = tuple(int(part) for part in version.split("."))
        padded = parts + (0,) * max(0, len(_MINIMUM_VERSION) - len(parts))
        if padded[: len(_MINIMUM_VERSION)] < _MINIMUM_VERSION:
            required = ".".join(str(part) for part in _MINIMUM_VERSION)
            raise ValueError(f"Pandoc {required} or newer is required; detected {version}.")
        return PandocIdentity(path, version, parts)

    def run(
        self,
        argv: Iterable[str],
        *,
        cwd: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> PandocProcessResult:
        args = tuple(argv)
        if not args or any(not isinstance(item, str) or not item for item in args):
            raise ValueError("Pandoc argv must contain non-empty strings.")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        executable = os.path.realpath(os.path.abspath(args[0]))
        args = (executable, *args[1:])
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            raise ValueError("Pandoc executable is not a runnable regular file.")
        working_directory = None
        if cwd:
            working_directory = os.path.abspath(cwd)
            if not os.path.isdir(working_directory):
                raise ValueError("Pandoc working directory is unavailable.")

        with self._lock:
            if self._active is not None and self._active.poll() is None:
                raise RuntimeError("Another Pandoc process is already active.")
            self._cancel_requested.clear()

        creationflags = 0
        extra: dict[str, object] = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            extra["start_new_session"] = True

        started = time.monotonic()
        process: subprocess.Popen | None = None
        status = "error"
        returncode: int | None = None
        with tempfile.TemporaryFile(prefix="graphium-pandoc-stdout-") as stdout_file, tempfile.TemporaryFile(
            prefix="graphium-pandoc-stderr-"
        ) as stderr_file:
            try:
                process = subprocess.Popen(
                    args,
                    cwd=working_directory,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    creationflags=creationflags,
                    **extra,
                )
                with self._lock:
                    if self._active is not None and self._active.poll() is None:
                        self._terminate(process)
                        raise RuntimeError("Another Pandoc process is already active.")
                    self._active = process

                while True:
                    if self._cancel_requested.is_set():
                        if not self._terminate(process):
                            raise RuntimeError("Pandoc process did not terminate after cancellation.")
                        returncode = process.returncode
                        status = "cancelled"
                        break
                    elapsed = time.monotonic() - started
                    if elapsed >= float(timeout_seconds):
                        if not self._terminate(process):
                            raise RuntimeError("Pandoc process did not terminate after timeout.")
                        returncode = process.returncode
                        status = "timeout"
                        break
                    try:
                        returncode = process.wait(timeout=min(0.10, max(0.01, float(timeout_seconds) - elapsed)))
                        status = "cancelled" if self._cancel_requested.is_set() else "success" if returncode == 0 else "error"
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process is not None and process.poll() is not None:
                    with self._lock:
                        if self._active is process:
                            self._active = None
                    self._cancel_requested.clear()

            stdout = self._read_tail(stdout_file, _CAPTURE_LIMIT)
            stderr = self._read_tail(stderr_file, _CAPTURE_LIMIT)

        return PandocProcessResult(
            status,
            args,
            returncode,
            stdout,
            stderr,
            time.monotonic() - started,
        )

    @staticmethod
    def _read_tail(handle, limit: int) -> str:
        handle.flush()
        size = os.fstat(handle.fileno()).st_size
        handle.seek(max(0, size - limit))
        return handle.read(limit).decode("utf-8", errors="replace")

    def cancel_active(self) -> bool:
        with self._lock:
            process = self._active
            if process is None or process.poll() is not None:
                return False
            self._cancel_requested.set()
        return self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> bool:
        if process.poll() is not None:
            return True
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=1.5)
            return True
        except subprocess.TimeoutExpired:
            pass
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            return False
        return process.poll() is not None


class PandocArtifactBuilder:
    """Build one derived artifact privately; never publish a user destination."""

    __slots__ = ("runner", "timeout_seconds", "max_output_bytes")

    def __init__(
        self,
        runner: PandocProcessRunner,
        *,
        timeout_seconds: float = 120.0,
        max_output_bytes: int = _MAX_OUTPUT_BYTES,
    ) -> None:
        if not isinstance(runner, PandocProcessRunner):
            raise TypeError("runner must be PandocProcessRunner")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self.runner = runner
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes

    def build(self, plan: PandocExportPlan) -> PandocArtifact:
        if not isinstance(plan, PandocExportPlan):
            raise TypeError("plan must be PandocExportPlan")
        with tempfile.TemporaryDirectory(prefix="graphium-pandoc-") as workspace:
            root = Path(workspace)
            input_path = root / "document.md"
            output_path = root / ("output" + plan.format.extension)
            self._write_private_text(input_path, plan.document_text)
            bibliography_path: Path | None = None
            if plan.bibliography_text:
                bibliography_path = root / "references.bib"
                self._write_private_text(bibliography_path, plan.bibliography_text)

            argv: list[str] = [
                plan.identity.path,
                str(input_path),
                "--from",
                "markdown",
                "--to",
                plan.format.writer,
                "--standalone",
            ]
            if bibliography_path is not None:
                argv.extend(("--citeproc", "--bibliography", str(bibliography_path)))
            argv.extend(("--output", str(output_path)))
            process = self.runner.run(
                tuple(argv),
                cwd=plan.document_directory or None,
                timeout_seconds=self.timeout_seconds,
            )
            if not process.succeeded:
                detail = self._summary(process.stderr) or self._summary(process.stdout)
                if process.status == "timeout":
                    return PandocArtifact("timeout", process=process, message="Pandoc output exceeded the time limit.")
                if process.status == "cancelled":
                    return PandocArtifact("cancelled", process=process, message="Pandoc output was cancelled.")
                return PandocArtifact("error", process=process, message=f"Pandoc output failed: {detail or process.returncode}")
            try:
                data = self._read_stage(output_path)
            except (OSError, ValueError) as exc:
                return PandocArtifact("error", process=process, message=str(exc))
            return PandocArtifact("built", data=data, process=process, message="Pandoc artifact built.")

    @staticmethod
    def _write_private_text(path: Path, text: str) -> None:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise

    def _read_stage(self, path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(path), flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Pandoc did not produce a regular output file.")
            if before.st_size <= 0:
                raise ValueError("Pandoc produced an empty output file.")
            if before.st_size > self.max_output_bytes:
                raise ValueError("Pandoc output exceeds the 128 MiB safety limit.")
            chunks: list[bytes] = []
            total = 0
            while True:
                block = os.read(fd, min(1024 * 1024, self.max_output_bytes - total + 1))
                if not block:
                    break
                total += len(block)
                if total > self.max_output_bytes:
                    raise ValueError("Pandoc output exceeds the 128 MiB safety limit.")
                chunks.append(block)
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("Pandoc output changed while Graphium was reading it.")
            return b"".join(chunks)
        finally:
            os.close(fd)

    @staticmethod
    def _summary(text: str) -> str:
        lines = [" ".join(line.split()) for line in (text or "").splitlines() if line.strip()]
        return " | ".join(lines)[:1200]


class PandocOutputWorker:
    """Own one background Pandoc detection/build task while GTK polls completion.

    Thread ownership stays beside the single Pandoc process owner.  This class
    has no GTK dependency and never publishes a user destination.
    """

    __slots__ = ("builder", "_lock", "_thread", "_mode", "_result", "_error")

    def __init__(self, builder: PandocArtifactBuilder) -> None:
        if not isinstance(builder, PandocArtifactBuilder):
            raise TypeError("builder must be PandocArtifactBuilder")
        self.builder = builder
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._mode = ""
        self._result: PandocIdentity | PandocArtifact | None = None
        self._error: BaseException | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            thread = self._thread
            return thread is not None and thread.is_alive()

    def _start(self, *, mode: str, target, args: tuple = ()) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Another Pandoc output task is already active.")
            self._mode = mode
            self._result = None
            self._error = None
            thread = threading.Thread(
                target=self._run,
                args=(target, args),
                name="graphium-pandoc-output",
                daemon=False,
            )
            self._thread = thread
            thread.start()

    def start_detect(self) -> None:
        self._start(mode="detect", target=self.builder.runner.detect)

    def start_build(self, plan: PandocExportPlan) -> None:
        if not isinstance(plan, PandocExportPlan):
            raise TypeError("plan must be PandocExportPlan")
        self._start(mode="build", target=self.builder.build, args=(plan,))

    def _run(self, target, args: tuple) -> None:
        try:
            result = target(*args)
        except BaseException as exc:
            with self._lock:
                self._error = exc
            return
        with self._lock:
            self._result = result

    def _poll(self, expected_mode: str, expected_type):
        with self._lock:
            if self._mode != expected_mode:
                raise RuntimeError(f"Pandoc worker is not running a {expected_mode} task.")
            thread = self._thread
            if thread is None or thread.is_alive():
                return None
            error = self._error
            result = self._result
        if error is not None:
            raise RuntimeError(str(error)) from error
        if not isinstance(result, expected_type):
            raise RuntimeError(f"Pandoc {expected_mode} task finished without a valid result.")
        return result

    def poll_identity(self) -> PandocIdentity | None:
        return self._poll("detect", PandocIdentity)

    def poll_artifact(self) -> PandocArtifact | None:
        return self._poll("build", PandocArtifact)

    def cancel_and_join(self, *, timeout_seconds: float = 5.0) -> bool:
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.builder.runner.cancel_active()
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(float(timeout_seconds))
        return not thread.is_alive()

    def close(self) -> None:
        if not self.cancel_and_join(timeout_seconds=5.0):
            raise RuntimeError("Pandoc output worker did not stop during close.")
