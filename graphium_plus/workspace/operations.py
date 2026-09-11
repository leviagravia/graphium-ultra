"""Pure planning primitives for bounded Workspace mutations."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from ..scratchpad import SCRATCHPAD_SUFFIX, is_managed_scratchpad_path, scratchpad_path
from .model import TEXT_SUFFIXES, WorkspaceError, normalize_root, path_is_within_root


MAX_BASENAME_BYTES = 255


@dataclass(frozen=True)
class WorkspaceCreationPlan:
    kind: str
    root: str
    parent_path: str
    target_path: str
    display_name: str


@dataclass(frozen=True)
class WorkspacePathToken:
    """Strong pre-commit filesystem observation for one Workspace object."""

    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    uid: int
    gid: int
    nlink: int


def workspace_path_token(value: os.stat_result) -> WorkspacePathToken:
    return WorkspacePathToken(
        device=int(value.st_dev),
        inode=int(value.st_ino),
        mode=int(value.st_mode),
        size=int(value.st_size),
        mtime_ns=int(value.st_mtime_ns),
        ctime_ns=int(getattr(value, "st_ctime_ns", 0)),
        uid=int(getattr(value, "st_uid", 0)),
        gid=int(getattr(value, "st_gid", 0)),
        nlink=int(getattr(value, "st_nlink", 1)),
    )


@dataclass(frozen=True)
class WorkspaceManagedCompanionPlan:
    """Exact managed Scratchpad companion bound to one primary Workspace file."""

    source_path: str
    target_path: str | None
    source_token: WorkspacePathToken | None




@dataclass(frozen=True)
class WorkspaceDuplicatePlan:
    kind: str
    root: str
    parent_path: str
    source_path: str
    target_path: str
    source_name: str
    display_name: str
    source_token: WorkspacePathToken
    companion: WorkspaceManagedCompanionPlan | None = None


@dataclass(frozen=True)
class WorkspaceRenamePlan:
    kind: str
    root: str
    parent_path: str
    source_path: str
    target_path: str
    source_name: str
    display_name: str
    source_is_directory: bool
    source_token: WorkspacePathToken
    companion: WorkspaceManagedCompanionPlan | None = None


@dataclass(frozen=True)
class WorkspaceTrashPlan:
    kind: str
    root: str
    parent_path: str
    source_path: str
    source_name: str
    source_is_directory: bool
    source_token: WorkspacePathToken
    companion: WorkspaceManagedCompanionPlan | None = None


@dataclass(frozen=True)
class WorkspaceMovePlan:
    kind: str
    root: str
    source_path: str
    destination_path: str
    target_path: str
    source_name: str
    source_is_directory: bool
    source_token: WorkspacePathToken
    destination_token: WorkspacePathToken
    source_parent_path: str = ""
    companion: WorkspaceManagedCompanionPlan | None = None


def managed_companion_plan(
    source_path: str,
    target_path: str | None,
    *,
    source_token: WorkspacePathToken | None,
) -> WorkspaceManagedCompanionPlan:
    source = scratchpad_path(source_path)
    target = scratchpad_path(target_path) if target_path else None
    if source is None:
        raise TypeError("source_path must identify a document")
    return WorkspaceManagedCompanionPlan(
        source_path=os.path.abspath(str(source)),
        target_path=os.path.abspath(str(target)) if target is not None else None,
        source_token=source_token,
    )


def _reject_managed_sidecar_primary(path: str) -> None:
    if is_managed_scratchpad_path(path):
        raise WorkspaceError(
            "Scratchpad sidecars are managed with their document and cannot be mutated directly from Workspace."
        )


def _validated_basename(raw_name: str, *, label: str) -> str:
    if not isinstance(raw_name, str):
        raise TypeError(f"{label} must be a string")
    name = raw_name.strip()
    if not name:
        raise WorkspaceError(f"Enter a {label}.")
    if "\x00" in name:
        raise WorkspaceError(f"The {label} contains an invalid character.")
    separators = {os.sep, "/", "\\"}
    if os.altsep:
        separators.add(os.altsep)
    if any(separator and separator in name for separator in separators):
        raise WorkspaceError(f"Enter one {label}, not a path.")
    if name in {".", ".."} or name.startswith("."):
        raise WorkspaceError(f"Hidden or reserved {label}s are not allowed here.")
    if len(os.fsencode(name)) > MAX_BASENAME_BYTES:
        raise WorkspaceError(f"The {label} is too long for the filesystem.")
    return name


def normalize_text_name(raw_name: str, *, suffix: str = ".txt") -> str:
    name = _validated_basename(raw_name, label="file name")
    normalized_suffix = suffix.strip().casefold() if isinstance(suffix, str) else ""
    if normalized_suffix and not normalized_suffix.startswith("."):
        normalized_suffix = f".{normalized_suffix}"
    if normalized_suffix not in TEXT_SUFFIXES:
        raise WorkspaceError("New Workspace files must use .txt or .md.")
    requested = Path(name).suffix.casefold()
    if requested:
        if requested not in TEXT_SUFFIXES:
            raise WorkspaceError("New Workspace files must use .txt or .md.")
        final_name = name
    else:
        final_name = f"{name}{normalized_suffix}"
    if len(os.fsencode(final_name)) > MAX_BASENAME_BYTES:
        raise WorkspaceError("The file name is too long for the filesystem.")
    if is_managed_scratchpad_path(final_name):
        raise WorkspaceError(f"The {SCRATCHPAD_SUFFIX} suffix is reserved for managed Scratchpad companions.")
    return final_name


def normalize_folder_name(raw_name: str) -> str:
    return _validated_basename(raw_name, label="folder name")


def _plan(root: str, parent_path: str, display_name: str, *, kind: str) -> WorkspaceCreationPlan:
    canonical_root = normalize_root(root)
    parent = os.path.abspath(parent_path)
    if os.path.islink(parent) or not os.path.isdir(parent):
        raise WorkspaceError("The destination folder no longer exists or is a symbolic link.")
    if not path_is_within_root(canonical_root, parent):
        raise WorkspaceError("The destination resolves outside the Workspace.")
    target = os.path.abspath(os.path.join(parent, display_name))
    if os.path.dirname(target) != parent or not path_is_within_root(canonical_root, target):
        raise WorkspaceError("The new item would escape the Workspace.")
    return WorkspaceCreationPlan(kind, canonical_root, parent, target, display_name)


def plan_new_text_file(root: str, parent_path: str, raw_name: str, *, suffix: str = ".txt") -> WorkspaceCreationPlan:
    return _plan(
        root,
        parent_path,
        normalize_text_name(raw_name, suffix=suffix),
        kind="new-text-file",
    )


def plan_new_folder(root: str, parent_path: str, raw_name: str) -> WorkspaceCreationPlan:
    return _plan(
        root,
        parent_path,
        normalize_folder_name(raw_name),
        kind="new-folder",
    )


def normalize_rename_name(raw_name: str) -> str:
    return _validated_basename(raw_name, label="new name")


def plan_workspace_rename(
    root: str,
    source_path: str,
    raw_name: str,
    *,
    source_is_directory: bool,
    source_token: WorkspacePathToken,
    companion_source_token: WorkspacePathToken | None = None,
) -> WorkspaceRenamePlan:
    if not isinstance(source_path, str) or not source_path.strip():
        raise WorkspaceError("Select one Workspace file or folder to rename.")
    if not isinstance(source_is_directory, bool):
        raise TypeError("source_is_directory must be boolean")
    if not isinstance(source_token, WorkspacePathToken):
        raise TypeError("source_token must be WorkspacePathToken")

    canonical_root = normalize_root(root)
    source = os.path.abspath(source_path)
    if not source_is_directory:
        _reject_managed_sidecar_primary(source)
    parent = os.path.dirname(source)
    if source == canonical_root:
        raise WorkspaceError("The Workspace root itself cannot be renamed here.")
    if not path_is_within_root(canonical_root, source) or not path_is_within_root(canonical_root, parent):
        raise WorkspaceError("The selected item resolves outside the Workspace.")

    source_name = os.path.basename(source)
    display_name = normalize_rename_name(raw_name)
    if display_name == source_name:
        raise WorkspaceError("The new name is unchanged.")
    target = os.path.abspath(os.path.join(parent, display_name))
    if os.path.dirname(target) != parent or not path_is_within_root(canonical_root, target):
        raise WorkspaceError("The renamed item would escape the Workspace.")
    if not source_is_directory:
        _reject_managed_sidecar_primary(target)
    return WorkspaceRenamePlan(
        kind="rename",
        root=canonical_root,
        parent_path=parent,
        source_path=source,
        target_path=target,
        source_name=source_name,
        display_name=display_name,
        source_is_directory=source_is_directory,
        source_token=source_token,
        companion=(
            None
            if source_is_directory
            else managed_companion_plan(source, target, source_token=companion_source_token)
        ),
    )


def _truncate_utf8_component(value: str, max_bytes: int) -> str:
    if max_bytes < 1:
        raise WorkspaceError("The duplicate file name cannot fit on this filesystem.")
    candidate = value
    while candidate and len(os.fsencode(candidate)) > max_bytes:
        candidate = candidate[:-1]
    if not candidate:
        raise WorkspaceError("The duplicate file name cannot fit on this filesystem.")
    return candidate


def next_duplicate_name(source_name: str, occupied_names: tuple[str, ...] | list[str]) -> str:
    """Return a deterministic, same-parent duplicate basename without overwriting."""
    source_name = _validated_basename(source_name, label="file name")
    if not isinstance(occupied_names, (tuple, list)) or not all(isinstance(name, str) for name in occupied_names):
        raise TypeError("occupied_names must be a tuple or list of strings")
    suffix = Path(source_name).suffix
    stem = source_name[:-len(suffix)] if suffix else source_name
    occupied = {name.casefold() for name in occupied_names}
    for index in range(1, 10001):
        marker = " copy" if index == 1 else f" copy {index}"
        tail = marker + suffix
        fitted_stem = _truncate_utf8_component(stem, MAX_BASENAME_BYTES - len(os.fsencode(tail)))
        candidate = f"{fitted_stem}{tail}"
        if candidate.casefold() not in occupied:
            return candidate
    raise WorkspaceError("No safe duplicate name is available in this folder.")


def plan_workspace_duplicate(
    root: str,
    source_path: str,
    occupied_names: tuple[str, ...] | list[str],
    *,
    source_token: WorkspacePathToken,
    companion_source_token: WorkspacePathToken | None = None,
) -> WorkspaceDuplicatePlan:
    if not isinstance(source_path, str) or not source_path.strip():
        raise WorkspaceError("Select one regular Workspace file to duplicate.")
    if not isinstance(source_token, WorkspacePathToken):
        raise TypeError("source_token must be WorkspacePathToken")
    canonical_root = normalize_root(root)
    source = os.path.abspath(source_path)
    _reject_managed_sidecar_primary(source)
    parent = os.path.dirname(source)
    if source == canonical_root:
        raise WorkspaceError("The Workspace root cannot be duplicated.")
    if not path_is_within_root(canonical_root, source) or not path_is_within_root(canonical_root, parent):
        raise WorkspaceError("The selected file resolves outside the Workspace.")
    source_name = os.path.basename(source)
    occupied = list(occupied_names)
    occupied_folded = {name.casefold() for name in occupied_names}
    while True:
        display_name = next_duplicate_name(source_name, occupied)
        candidate = os.path.abspath(os.path.join(parent, display_name))
        candidate_companion = scratchpad_path(candidate)
        if candidate_companion is None or candidate_companion.name.casefold() not in occupied_folded:
            break
        occupied.append(display_name)
    target = os.path.abspath(os.path.join(parent, display_name))
    if os.path.dirname(target) != parent or not path_is_within_root(canonical_root, target):
        raise WorkspaceError("The duplicate would escape the Workspace.")
    return WorkspaceDuplicatePlan(
        kind="duplicate-file",
        root=canonical_root,
        parent_path=parent,
        source_path=source,
        target_path=target,
        source_name=source_name,
        display_name=display_name,
        source_token=source_token,
        companion=managed_companion_plan(source, target, source_token=companion_source_token),
    )

def plan_workspace_trash(
    root: str,
    source_path: str,
    *,
    source_is_directory: bool,
    source_token: WorkspacePathToken,
    companion_source_token: WorkspacePathToken | None = None,
) -> WorkspaceTrashPlan:
    """Build one root-confined system-Trash plan with no delete fallback."""
    if not isinstance(source_path, str) or not source_path.strip():
        raise WorkspaceError("Select one Workspace file or folder to move to Trash.")
    if not isinstance(source_is_directory, bool):
        raise TypeError("source_is_directory must be boolean")
    if not isinstance(source_token, WorkspacePathToken):
        raise TypeError("source_token must be WorkspacePathToken")

    canonical_root = normalize_root(root)
    source = os.path.abspath(source_path)
    if not source_is_directory:
        _reject_managed_sidecar_primary(source)
    parent = os.path.dirname(source)
    if source == canonical_root:
        raise WorkspaceError("The Workspace root itself cannot be moved to Trash here.")
    if not path_is_within_root(canonical_root, source) or not path_is_within_root(canonical_root, parent):
        raise WorkspaceError("The selected item resolves outside the Workspace.")
    return WorkspaceTrashPlan(
        kind="move-to-trash",
        root=canonical_root,
        parent_path=parent,
        source_path=source,
        source_name=os.path.basename(source),
        source_is_directory=source_is_directory,
        source_token=source_token,
        companion=(
            None
            if source_is_directory
            else managed_companion_plan(source, None, source_token=companion_source_token)
        ),
    )

def plan_workspace_move(
    root: str,
    source_path: str,
    destination_path: str,
    *,
    source_is_directory: bool,
    source_token: WorkspacePathToken,
    destination_token: WorkspacePathToken,
    target_exists: bool = False,
    companion_source_token: WorkspacePathToken | None = None,
) -> WorkspaceMovePlan:
    """Build one bounded same-filesystem namespace move plan without mutating disk."""
    if not isinstance(source_path, str) or not source_path.strip():
        raise WorkspaceError("Select one Workspace file or folder to move.")
    if not isinstance(destination_path, str) or not destination_path.strip():
        raise WorkspaceError("Choose an existing Workspace destination folder.")
    if not isinstance(source_is_directory, bool):
        raise TypeError("source_is_directory must be boolean")
    if not isinstance(source_token, WorkspacePathToken):
        raise TypeError("source_token must be WorkspacePathToken")
    if not isinstance(destination_token, WorkspacePathToken):
        raise TypeError("destination_token must be WorkspacePathToken")
    if not isinstance(target_exists, bool):
        raise TypeError("target_exists must be boolean")

    canonical_root = normalize_root(root)
    source = os.path.abspath(source_path)
    if not source_is_directory:
        _reject_managed_sidecar_primary(source)
    destination = os.path.abspath(destination_path)
    if source == canonical_root:
        raise WorkspaceError("The Workspace root itself cannot be moved.")
    if not path_is_within_root(canonical_root, source):
        raise WorkspaceError("The selected item resolves outside the Workspace.")
    if not path_is_within_root(canonical_root, destination):
        raise WorkspaceError("The destination resolves outside the Workspace.")
    if os.path.dirname(source) == destination:
        raise WorkspaceError("The selected item is already in that folder.")
    if source_is_directory:
        try:
            if os.path.commonpath((source, destination)) == source:
                raise WorkspaceError("A folder cannot be moved into itself or one of its descendants.")
        except ValueError as exc:
            raise WorkspaceError("The move destination is incompatible with the selected folder.") from exc

    source_name = os.path.basename(source)
    target = os.path.abspath(os.path.join(destination, source_name))
    if os.path.dirname(target) != destination or not path_is_within_root(canonical_root, target):
        raise WorkspaceError("The moved item would escape the Workspace.")
    if target == source:
        raise WorkspaceError("The move would not change the selected item path.")
    if not source_is_directory:
        _reject_managed_sidecar_primary(target)
    if target_exists:
        raise WorkspaceError("A file or folder with that name already exists in the destination.")
    if source_token.device != destination_token.device:
        raise WorkspaceError("Cross-filesystem Workspace moves are not supported.")

    return WorkspaceMovePlan(
        kind="move",
        root=canonical_root,
        source_path=source,
        destination_path=destination,
        target_path=target,
        source_name=source_name,
        source_is_directory=source_is_directory,
        source_token=source_token,
        destination_token=destination_token,
        source_parent_path=os.path.dirname(source),
        companion=(
            None
            if source_is_directory
            else managed_companion_plan(source, target, source_token=companion_source_token)
        ),
    )


def remap_workspace_relative_path(value: str, source_relative: str, target_relative: str) -> str:
    """Retarget one relative projection path by path components, never string prefix."""
    def parts(raw: str, label: str) -> tuple[str, ...]:
        if not isinstance(raw, str) or not raw or os.path.isabs(raw):
            raise ValueError(f"{label} must be a non-empty relative path")
        normalized = os.path.normpath(raw)
        values = tuple(part for part in normalized.split(os.sep) if part not in ("", "."))
        if not values or any(part == ".." for part in values):
            raise ValueError(f"{label} must stay inside the Workspace")
        return values

    value_parts = parts(value, "value")
    source_parts = parts(source_relative, "source_relative")
    target_parts = parts(target_relative, "target_relative")
    if value_parts[: len(source_parts)] != source_parts:
        return os.path.join(*value_parts)
    return os.path.join(*(target_parts + value_parts[len(source_parts):]))


def rewrite_workspace_relative_paths(
    values: tuple[str, ...], source_relative: str, target_relative: str
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not all(isinstance(value, str) for value in values):
        raise TypeError("values must be a tuple of relative path strings")
    remapped = [
        remap_workspace_relative_path(value, source_relative, target_relative)
        for value in values
    ]
    return tuple(dict.fromkeys(remapped))

