"""Explicit on-demand Workspace text search with no index or watcher.

The Workspace remains a filesystem projection. Search reuses Graphium Core's
Unicode literal-search semantics and stable document loader, while optionally
using the current authoritative unsaved editor buffer for the one active file.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from graphium.domain.text_search import SearchInputError, SearchScaleError, find_all, is_exact_match, validate_query
from graphium.infrastructure.document_loader import load_document

from ..scratchpad import is_managed_scratchpad_path
from .model import TEXT_SUFFIXES, WorkspaceError, normalize_root, path_is_within_root
from .operations import WorkspacePathToken, workspace_path_token

MAX_WORKSPACE_SEARCH_FILES = 10_000
MAX_WORKSPACE_SEARCH_RESULTS = 2_000
MAX_WORKSPACE_SEARCH_MATCHES_PER_FILE = 5_000
MAX_WORKSPACE_SEARCH_FILE_BYTES = 32 * 1024 * 1024
MAX_WORKSPACE_SEARCH_CONTEXT_CHARS = 240


class WorkspaceSearchError(WorkspaceError):
    """A Workspace search request or result cannot be used safely."""


@dataclass(frozen=True)
class WorkspaceSearchBufferOverride:
    path: str
    text: str
    state_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("override path must be a non-empty string")
        if not isinstance(self.text, str):
            raise TypeError("override text must be a string")
        object.__setattr__(self, "path", os.path.abspath(self.path))
        object.__setattr__(self, "state_id", int(self.state_id))


@dataclass(frozen=True)
class WorkspaceSearchDiagnostic:
    relative_path: str
    message: str


@dataclass(frozen=True)
class WorkspaceSearchResult:
    root: str
    path: str
    relative_path: str
    line: int
    context: str
    start: int
    end: int
    text_sha256: str
    file_token: WorkspacePathToken
    source_kind: str = "disk"
    source_state_id: int | None = None

    def __post_init__(self) -> None:
        if self.source_kind not in {"disk", "active-buffer"}:
            raise ValueError("source_kind must be disk or active-buffer")
        if self.source_kind == "active-buffer" and self.source_state_id is None:
            raise ValueError("active-buffer results require source_state_id")
        if int(self.line) <= 0 or int(self.start) < 0 or int(self.end) <= int(self.start):
            raise ValueError("search result target must be a non-empty source range")


@dataclass(frozen=True)
class WorkspaceSearchReport:
    root: str
    query: str
    match_case: bool
    results: tuple[WorkspaceSearchResult, ...]
    diagnostics: tuple[WorkspaceSearchDiagnostic, ...]
    files_considered: int
    truncated: bool = False

    def contains(self, result: WorkspaceSearchResult) -> bool:
        return isinstance(result, WorkspaceSearchResult) and result in self.results


@dataclass(frozen=True)
class WorkspaceSearchRevalidation:
    current: bool
    text: str | None = None
    reason: str = ""


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _token_matches_file_state(token: WorkspacePathToken, result) -> bool:
    state = result.file_state
    obj = state.binding.object_id
    disk = state.disk
    return (
        obj is not None
        and token.device == int(obj.device)
        and token.inode == int(obj.inode)
        and token.mode == int(disk.mode)
        and token.size == int(disk.size)
        and token.mtime_ns == int(disk.mtime_ns)
        and token.ctime_ns == int(disk.ctime_ns or 0)
        and token.uid == int(disk.uid or 0)
        and token.gid == int(disk.gid or 0)
        and token.nlink == int(disk.nlink or 1)
    )


def _context_for(text: str, start: int, end: int) -> tuple[int, str]:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line_number = text.count("\n", 0, line_start) + 1
    line = text[line_start:line_end].replace("\t", "    ")
    if len(line) <= MAX_WORKSPACE_SEARCH_CONTEXT_CHARS:
        return line_number, line

    local_start = start - line_start
    local_end = end - line_start
    half = MAX_WORKSPACE_SEARCH_CONTEXT_CHARS // 2
    window_start = max(0, local_start - half)
    window_end = min(len(line), max(local_end + half, window_start + MAX_WORKSPACE_SEARCH_CONTEXT_CHARS))
    window_start = max(0, window_end - MAX_WORKSPACE_SEARCH_CONTEXT_CHARS)
    prefix = "…" if window_start > 0 else ""
    suffix = "…" if window_end < len(line) else ""
    return line_number, f"{prefix}{line[window_start:window_end]}{suffix}"


def _visible_text_files(root: str):
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            yield None, WorkspaceSearchDiagnostic(
                os.path.relpath(directory, root) if directory != root else "",
                f"Cannot read folder: {exc}",
            )
            continue

        directories: list[str] = []
        files: list[str] = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    directories.append(os.path.abspath(entry.path))
                    continue
                if (
                    entry.is_file(follow_symlinks=False)
                    and Path(entry.name).suffix.casefold() in TEXT_SUFFIXES
                    and not is_managed_scratchpad_path(entry.name)
                ):
                    files.append(os.path.abspath(entry.path))
            except OSError:
                continue
        for child in sorted(directories, key=lambda value: value.casefold(), reverse=True):
            if path_is_within_root(root, child):
                stack.append(child)
        for file_path in sorted(files, key=lambda value: value.casefold()):
            yield file_path, None


def _read_disk_text(root: str, path: str) -> tuple[str, WorkspacePathToken, str]:
    if is_managed_scratchpad_path(path):
        raise WorkspaceSearchError("Managed Scratchpad sidecars are excluded from Workspace search.")
    if os.path.islink(path) or not path_is_within_root(root, path):
        raise WorkspaceSearchError("Workspace search refused a symbolic-link or escaped file.")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise WorkspaceSearchError(f"Cannot inspect file: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise WorkspaceSearchError("Workspace search accepts only regular text files.")
    if int(before.st_size) > MAX_WORKSPACE_SEARCH_FILE_BYTES:
        raise WorkspaceSearchError(
            f"File exceeds the bounded Workspace-search size ({MAX_WORKSPACE_SEARCH_FILE_BYTES} bytes)."
        )
    token = workspace_path_token(before)
    loaded = load_document(path, retries=0)
    if not path_is_within_root(root, loaded.file_state.binding.canonical_path or path):
        raise WorkspaceSearchError("Workspace file resolved outside the bound root while being read.")
    if not _token_matches_file_state(token, loaded):
        raise WorkspaceSearchError("Workspace file changed before search could accept it.")
    return loaded.text, token, loaded.file_state.content_fingerprint.hex_digest


def search_workspace(
    root: str,
    query: str,
    *,
    match_case: bool = False,
    active_buffer: WorkspaceSearchBufferOverride | None = None,
) -> WorkspaceSearchReport:
    canonical_root = normalize_root(root)
    try:
        query = validate_query(query)
    except SearchInputError as exc:
        raise WorkspaceSearchError(str(exc)) from exc

    override = active_buffer
    if override is not None:
        if not path_is_within_root(canonical_root, override.path):
            override = None
        elif Path(override.path).suffix.casefold() not in TEXT_SUFFIXES or is_managed_scratchpad_path(override.path):
            override = None

    results: list[WorkspaceSearchResult] = []
    diagnostics: list[WorkspaceSearchDiagnostic] = []
    files_considered = 0
    truncated = False

    for path, scan_diagnostic in _visible_text_files(canonical_root):
        if scan_diagnostic is not None:
            diagnostics.append(scan_diagnostic)
            continue
        assert path is not None
        files_considered += 1
        relative = os.path.relpath(path, canonical_root)
        if files_considered > MAX_WORKSPACE_SEARCH_FILES:
            diagnostics.append(WorkspaceSearchDiagnostic("", "Workspace search stopped at its bounded file budget; refine the Workspace or query."))
            truncated = True
            break

        try:
            if override is not None and os.path.abspath(path) == override.path:
                observed = os.lstat(path)
                if not stat.S_ISREG(observed.st_mode) or os.path.islink(path):
                    raise WorkspaceSearchError("The active Workspace document is no longer a regular file.")
                token = workspace_path_token(observed)
                text = override.text
                text_digest = _text_sha256(text)
                source_kind = "active-buffer"
                source_state_id = override.state_id
            else:
                text, token, text_digest = _read_disk_text(canonical_root, path)
                source_kind = "disk"
                source_state_id = None
            matches = find_all(
                text,
                query,
                match_case=bool(match_case),
                max_matches=MAX_WORKSPACE_SEARCH_MATCHES_PER_FILE,
            )
        except SearchScaleError:
            diagnostics.append(WorkspaceSearchDiagnostic(relative, "Too many matches in this file; refine the query."))
            continue
        except Exception as exc:
            diagnostics.append(WorkspaceSearchDiagnostic(relative, str(exc)))
            continue

        for match in matches:
            if len(results) >= MAX_WORKSPACE_SEARCH_RESULTS:
                diagnostics.append(WorkspaceSearchDiagnostic("", "Workspace search result budget reached; refine the query."))
                truncated = True
                break
            line_number, context = _context_for(text, match.start, match.end)
            results.append(
                WorkspaceSearchResult(
                    root=canonical_root,
                    path=path,
                    relative_path=relative,
                    line=line_number,
                    context=context,
                    start=match.start,
                    end=match.end,
                    text_sha256=text_digest,
                    file_token=token,
                    source_kind=source_kind,
                    source_state_id=source_state_id,
                )
            )
        if truncated:
            break

    results.sort(key=lambda item: (item.relative_path.casefold(), item.relative_path, item.start))
    return WorkspaceSearchReport(
        root=canonical_root,
        query=query,
        match_case=bool(match_case),
        results=tuple(results),
        diagnostics=tuple(diagnostics),
        files_considered=files_considered,
        truncated=truncated,
    )


def revalidate_disk_result(report: WorkspaceSearchReport, result: WorkspaceSearchResult) -> WorkspaceSearchRevalidation:
    if not isinstance(report, WorkspaceSearchReport) or not report.contains(result):
        return WorkspaceSearchRevalidation(False, reason="Search result does not belong to this report.")
    if result.source_kind != "disk":
        return WorkspaceSearchRevalidation(False, reason="Active-buffer search results require editor-state validation.")
    if result.root != report.root or not path_is_within_root(report.root, result.path):
        return WorkspaceSearchRevalidation(False, reason="Search result is outside the current Workspace.")
    try:
        text, token, digest = _read_disk_text(report.root, result.path)
    except Exception as exc:
        return WorkspaceSearchRevalidation(False, reason=str(exc))
    if token != result.file_token or digest != result.text_sha256:
        return WorkspaceSearchRevalidation(False, reason="The file changed after Workspace search; run the search again.")
    if result.end > len(text) or not is_exact_match(
        text,
        report.query,
        result.start,
        result.end,
        match_case=report.match_case,
    ):
        return WorkspaceSearchRevalidation(False, reason="The match changed after Workspace search; run the search again.")
    return WorkspaceSearchRevalidation(True, text=text)
