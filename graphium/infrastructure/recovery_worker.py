"""One dedicated worker for Graphium recovery persistence only."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Protocol, TypeVar


T = TypeVar("T")


class RecoveryDispatchPort(Protocol):
    def dispatch(self, callback: Callable[[], None]) -> None: ...


class DedicatedRecoveryWorker:
    """Single-thread executor confined to recovery I/O; not a generic job framework."""

    __slots__ = ("_dispatch", "_executor", "_closed")

    def __init__(self, dispatch: Callable[[Callable[[], None]], None]) -> None:
        if not callable(dispatch):
            raise TypeError("dispatch must be callable")
        self._dispatch = dispatch
        self._executor: ThreadPoolExecutor | None = None
        self._closed = False

    def submit(
        self,
        job: Callable[[], T],
        done: Callable[[T | None, BaseException | None], None],
    ) -> None:
        if self._closed:
            raise RuntimeError("recovery worker is closed")
        if not callable(job) or not callable(done):
            raise TypeError("job and done must be callable")
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graphium-recovery")
        future: Future[T] = self._executor.submit(job)

        def completed(fut: Future[T]) -> None:
            try:
                result = fut.result()
                error: BaseException | None = None
            except BaseException as exc:  # completion must report, never lose worker failure
                result = None
                error = exc
            self._dispatch(lambda: done(result, error))

        future.add_done_callback(completed)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # The executor itself is lazy: a no-recovery/clean-start process starts no
        # recovery thread.  If work existed, clean exit drains it before shutdown.
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
