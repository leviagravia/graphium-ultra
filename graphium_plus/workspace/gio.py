"""Filesystem commit boundary for already-planned Workspace mutations."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
import secrets
import stat

try:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
except Exception:  # Keep pure planning/tests importable without PyGObject.
    Gio = None
    GLib = None

from .operations import (
    WorkspaceCreationPlan,
    WorkspaceDuplicatePlan,
    WorkspaceManagedCompanionPlan,
    WorkspaceMovePlan,
    WorkspacePathToken,
    WorkspaceRenamePlan,
    WorkspaceTrashPlan,
    workspace_path_token,
)


@dataclass(frozen=True)
class WorkspaceCreationResult:
    success: bool
    path: str
    committed: bool = False
    message: str = ""


@dataclass(frozen=True)
class WorkspaceRenameResult:
    success: bool
    path: str
    committed: bool = False
    message: str = ""
    rollback_failed: bool = False
    companion_committed: bool = False
    object_token: WorkspacePathToken | None = None


@dataclass(frozen=True)
class WorkspaceDuplicateResult:
    success: bool
    path: str
    committed: bool = False
    message: str = ""
    rollback_failed: bool = False
    companion_committed: bool = False
    object_token: WorkspacePathToken | None = None


@dataclass(frozen=True)
class WorkspaceMoveResult:
    success: bool
    path: str
    committed: bool = False
    message: str = ""
    companion_committed: bool = False
    rollback_failed: bool = False
    object_token: WorkspacePathToken | None = None


@dataclass(frozen=True)
class WorkspaceTrashResult:
    success: bool
    parent_path: str
    source_path: str
    accepted: bool = False
    message: str = ""
    committed: bool = False
    companion_committed: bool = False


class WorkspaceGioAdapter:
    """Execute bounded local mutations after commit-time revalidation."""

    @staticmethod
    def _validated_parent(plan: WorkspaceCreationPlan | WorkspaceRenamePlan | WorkspaceDuplicatePlan) -> str | None:
        root = os.path.abspath(plan.root)
        parent = os.path.abspath(plan.parent_path)
        target_parent = os.path.abspath(os.path.dirname(plan.target_path))
        try:
            safe = (
                target_parent == parent
                and os.path.isdir(root)
                and not os.path.islink(root)
                and os.path.isdir(parent)
                and not os.path.islink(parent)
                and os.path.commonpath((os.path.realpath(root), os.path.realpath(parent)))
                == os.path.realpath(root)
            )
        except (OSError, TypeError, ValueError):
            safe = False
        return root if safe else None

    @staticmethod
    def _target_within_root(root: str, target_path: str) -> bool:
        try:
            return os.path.commonpath((os.path.realpath(root), os.path.realpath(target_path))) == os.path.realpath(root)
        except (OSError, TypeError, ValueError):
            return False

    def create(self, plan: WorkspaceCreationPlan) -> WorkspaceCreationResult:
        if not isinstance(plan, WorkspaceCreationPlan):
            raise TypeError("plan must be WorkspaceCreationPlan")
        root = self._validated_parent(plan)
        if root is None:
            return WorkspaceCreationResult(False, plan.target_path, message="The destination folder changed or resolves outside the Workspace.")
        if Gio is None or GLib is None:
            return WorkspaceCreationResult(False, plan.target_path, message="GIO is unavailable; nothing was created.")
        if plan.kind == "new-text-file":
            return self._create_file(plan, root)
        if plan.kind == "new-folder":
            return self._create_folder(plan, root)
        raise ValueError(f"unsupported Workspace creation operation: {plan.kind}")

    def _create_file(self, plan: WorkspaceCreationPlan, root: str) -> WorkspaceCreationResult:
        target = Gio.File.new_for_path(plan.target_path)
        stream = None
        committed = False
        try:
            stream = target.create(Gio.FileCreateFlags.NONE, None)
            stream.close(None)
            stream = None
            committed = True
            info = target.query_info(
                "standard::type,standard::is-symlink",
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                None,
            )
            if (
                info.get_is_symlink()
                or info.get_file_type() != Gio.FileType.REGULAR
                or not self._target_within_root(root, plan.target_path)
            ):
                return WorkspaceCreationResult(False, plan.target_path, True, "The file was created, but its confined regular-file identity could not be verified.")
            return WorkspaceCreationResult(True, plan.target_path, True)
        except GLib.Error as exc:
            return WorkspaceCreationResult(
                False,
                plan.target_path,
                committed,
                f"The file was created, but final verification failed: {exc.message}" if committed else f"The text file could not be created: {exc.message}",
            )
        finally:
            if stream is not None:
                try:
                    stream.close(None)
                except GLib.Error:
                    pass

    def _create_folder(self, plan: WorkspaceCreationPlan, root: str) -> WorkspaceCreationResult:
        target = Gio.File.new_for_path(plan.target_path)
        committed = False
        try:
            target.make_directory(None)
            committed = True
            info = target.query_info(
                "standard::type,standard::is-symlink",
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                None,
            )
            if (
                info.get_is_symlink()
                or info.get_file_type() != Gio.FileType.DIRECTORY
                or not self._target_within_root(root, plan.target_path)
            ):
                return WorkspaceCreationResult(False, plan.target_path, True, "The folder was created, but its confined directory identity could not be verified.")
            return WorkspaceCreationResult(True, plan.target_path, True)
        except GLib.Error as exc:
            return WorkspaceCreationResult(
                False,
                plan.target_path,
                committed,
                f"The folder was created, but final verification failed: {exc.message}" if committed else f"The folder could not be created: {exc.message}",
            )

    @staticmethod
    def _current_token(path: str) -> WorkspacePathToken | None:
        try:
            value = os.lstat(path)
        except OSError:
            return None
        return workspace_path_token(value)

    @staticmethod
    def _object_id(value: os.stat_result) -> tuple[int, int]:
        return int(value.st_dev), int(value.st_ino)

    @classmethod
    def _path_matches_descriptor(cls, path: str, fd: int) -> bool:
        try:
            named = os.lstat(path)
            pinned = os.fstat(fd)
        except OSError:
            return False
        return cls._object_id(named) == cls._object_id(pinned)

    @classmethod
    def _open_pinned_source(cls, plan: WorkspaceRenamePlan | WorkspaceMovePlan | WorkspaceTrashPlan) -> int | None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        if plan.source_is_directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(plan.source_path, flags)
        except OSError:
            return None
        try:
            pinned = os.fstat(fd)
            if workspace_path_token(pinned) != plan.source_token:
                raise ValueError("planned source signature is stale")
            if plan.source_is_directory:
                if not stat.S_ISDIR(pinned.st_mode):
                    raise ValueError("planned directory type changed")
            elif not stat.S_ISREG(pinned.st_mode):
                raise ValueError("planned file type changed")
            # Revalidate the pathname against the same strong signature after opening it.
            # Keeping this descriptor open pins the original inode across the GIO rename,
            # so a replacement lifetime cannot impersonate it through inode reuse.
            if cls._current_token(plan.source_path) != plan.source_token:
                raise ValueError("planned source pathname changed")
            if not cls._path_matches_descriptor(plan.source_path, fd):
                raise ValueError("planned source pathname no longer binds the pinned object")
            return fd
        except (OSError, ValueError):
            os.close(fd)
            return None

    @staticmethod
    def _collision_exists(_source_path: str, target_path: str) -> bool:
        return os.path.lexists(target_path)

    def _rename_single(self, plan: WorkspaceRenamePlan) -> WorkspaceRenameResult:
        if not isinstance(plan, WorkspaceRenamePlan):
            raise TypeError("plan must be WorkspaceRenamePlan")
        root = self._validated_parent(plan)
        if root is None:
            return WorkspaceRenameResult(False, plan.source_path, message="The parent folder changed or resolves outside the Workspace.")
        if Gio is None or GLib is None:
            return WorkspaceRenameResult(False, plan.source_path, message="GIO is unavailable; nothing was renamed.")
        if os.path.islink(plan.source_path) or not self._target_within_root(root, plan.source_path):
            return WorkspaceRenameResult(False, plan.source_path, message="The selected item changed before it could be renamed.")

        source_fd = self._open_pinned_source(plan)
        if source_fd is None:
            return WorkspaceRenameResult(False, plan.source_path, message="The selected item changed before it could be renamed.")
        try:
            if self._collision_exists(plan.source_path, plan.target_path):
                return WorkspaceRenameResult(False, plan.source_path, message="A file or folder with that name already exists.")

            # One final pathname/descriptor binding check immediately before the single GIO
            # namespace mutation. The descriptor stays open until verification/rollback ends.
            if (
                self._current_token(plan.source_path) != plan.source_token
                or not self._path_matches_descriptor(plan.source_path, source_fd)
            ):
                return WorkspaceRenameResult(False, plan.source_path, message="The selected item changed before it could be renamed.")

            renamed = None
            failure_message = "Rename verification failed."
            try:
                renamed = Gio.File.new_for_path(plan.source_path).set_display_name(plan.display_name, None)
            except GLib.Error as exc:
                failure_message = f"The selected item could not be renamed: {exc.message}"

            # Post-rename ctime is allowed to change. Ownership is therefore proven by the
            # object pinned by source_fd, not by replaying the pre-commit metadata signature.
            committed = self._path_matches_descriptor(plan.target_path, source_fd)
            if renamed is not None:
                try:
                    returned = os.path.abspath(renamed.get_path() or "")
                    expected_type = Gio.FileType.DIRECTORY if plan.source_is_directory else Gio.FileType.REGULAR
                    info = renamed.query_info(
                        "standard::type,standard::is-symlink",
                        Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                        None,
                    )
                    if (
                        returned == plan.target_path
                        and committed
                        and not os.path.lexists(plan.source_path)
                        and not info.get_is_symlink()
                        and info.get_file_type() == expected_type
                        and self._target_within_root(root, plan.target_path)
                    ):
                        return WorkspaceRenameResult(
                            True,
                            plan.target_path,
                            committed=True,
                            object_token=workspace_path_token(os.fstat(source_fd)),
                        )
                except GLib.Error as exc:
                    failure_message = f"Rename committed, but final verification failed: {exc.message}"

            # Roll back only while the target pathname still binds the descriptor-pinned
            # original object. A substituted target can never acquire rollback authority.
            if committed and self._path_matches_descriptor(plan.target_path, source_fd):
                try:
                    Gio.File.new_for_path(plan.target_path).set_display_name(plan.source_name, None)
                except GLib.Error:
                    return WorkspaceRenameResult(
                        False, plan.target_path, committed=True,
                        message=f"{failure_message} Rollback failed.",
                        rollback_failed=True,
                        object_token=workspace_path_token(os.fstat(source_fd)),
                    )
                if self._path_matches_descriptor(plan.source_path, source_fd):
                    return WorkspaceRenameResult(
                        False, plan.source_path,
                        message=f"{failure_message} The original name was restored.",
                    )
                return WorkspaceRenameResult(
                    False, plan.target_path, committed=True,
                    message=f"{failure_message} Rollback identity could not be verified.",
                    rollback_failed=True,
                    object_token=workspace_path_token(os.fstat(source_fd)),
                )
            return WorkspaceRenameResult(False, plan.source_path, message=failure_message)
        finally:
            try:
                os.close(source_fd)
            except OSError:
                pass

    @staticmethod
    def _companion_source_matches(companion: WorkspaceManagedCompanionPlan) -> bool:
        if companion.source_token is None:
            return not os.path.lexists(companion.source_path)
        try:
            observed = os.lstat(companion.source_path)
        except OSError:
            return False
        return (
            not stat.S_ISLNK(observed.st_mode)
            and stat.S_ISREG(observed.st_mode)
            and workspace_path_token(observed) == companion.source_token
        )

    @staticmethod
    def _companion_target_free(companion: WorkspaceManagedCompanionPlan) -> bool:
        return companion.target_path is None or not os.path.lexists(companion.target_path)

    @staticmethod
    def _companion_rename_plan(
        primary: WorkspaceRenamePlan,
        *,
        source_path: str,
        target_path: str,
        source_token: WorkspacePathToken,
    ) -> WorkspaceRenamePlan:
        return WorkspaceRenamePlan(
            kind="rename",
            root=primary.root,
            parent_path=os.path.dirname(source_path),
            source_path=source_path,
            target_path=target_path,
            source_name=os.path.basename(source_path),
            display_name=os.path.basename(target_path),
            source_is_directory=False,
            source_token=source_token,
            companion=None,
        )

    def rename(self, plan: WorkspaceRenamePlan) -> WorkspaceRenameResult:
        """Rename one Workspace object and its exact managed Scratchpad companion."""
        if not isinstance(plan, WorkspaceRenamePlan):
            raise TypeError("plan must be WorkspaceRenamePlan")
        companion = plan.companion
        primary_plan = replace(plan, companion=None)
        if companion is None:
            return self._rename_single(primary_plan)
        if companion.target_path is None:
            return WorkspaceRenameResult(False, plan.source_path, message="The Scratchpad rename target is missing.")
        if not self._companion_source_matches(companion):
            return WorkspaceRenameResult(False, plan.source_path, message="The managed Scratchpad companion changed before rename commit.")
        if not self._companion_target_free(companion):
            return WorkspaceRenameResult(False, plan.source_path, message="The managed Scratchpad destination already exists.")

        if companion.source_token is None:
            primary = self._rename_single(primary_plan)
            if not primary.success:
                return primary
            if not os.path.lexists(companion.target_path):
                return primary
            # A reserved sidecar appeared during the primary commit. Restore the primary
            # name only while the exact moved object is still provably ours.
            if primary.object_token is not None:
                rollback_plan = self._companion_rename_plan(
                    primary_plan,
                    source_path=plan.target_path,
                    target_path=plan.source_path,
                    source_token=primary.object_token,
                )
                rollback = self._rename_single(rollback_plan)
                if rollback.success:
                    return WorkspaceRenameResult(
                        False,
                        plan.source_path,
                        message="A managed Scratchpad destination appeared during rename; the original document name was restored.",
                    )
            return WorkspaceRenameResult(
                False,
                plan.target_path,
                committed=True,
                message="The document was renamed, but a conflicting managed Scratchpad destination appeared during commit.",
                rollback_failed=True,
                object_token=primary.object_token,
            )

        companion_plan = self._companion_rename_plan(
            primary_plan,
            source_path=companion.source_path,
            target_path=companion.target_path,
            source_token=companion.source_token,
        )
        companion_result = self._rename_single(companion_plan)
        if not companion_result.success:
            return WorkspaceRenameResult(
                False,
                plan.source_path,
                committed=False,
                message=f"The managed Scratchpad could not be renamed safely. {companion_result.message}".strip(),
                rollback_failed=companion_result.rollback_failed,
                companion_committed=companion_result.committed,
            )

        primary = self._rename_single(primary_plan)
        if primary.success:
            if (
                companion_result.object_token is not None
                and self._current_token(companion.target_path) == companion_result.object_token
            ):
                return WorkspaceRenameResult(
                    True,
                    primary.path,
                    committed=True,
                    companion_committed=True,
                    object_token=primary.object_token,
                )
            return WorkspaceRenameResult(
                False,
                primary.path,
                committed=True,
                message="The document was renamed, but final Scratchpad companion identity could not be verified.",
                companion_committed=True,
                rollback_failed=True,
                object_token=primary.object_token,
            )
        if primary.committed:
            return WorkspaceRenameResult(
                False,
                primary.path,
                committed=True,
                message=f"{primary.message} The Scratchpad companion remains at the managed target path.".strip(),
                rollback_failed=primary.rollback_failed,
                companion_committed=True,
                object_token=primary.object_token,
            )

        if companion_result.object_token is not None:
            rollback_plan = self._companion_rename_plan(
                primary_plan,
                source_path=companion.target_path,
                target_path=companion.source_path,
                source_token=companion_result.object_token,
            )
            rollback = self._rename_single(rollback_plan)
            if rollback.success:
                return WorkspaceRenameResult(
                    False,
                    plan.source_path,
                    message=f"{primary.message} The Scratchpad companion was restored.".strip(),
                )
        return WorkspaceRenameResult(
            False,
            plan.source_path,
            message=f"{primary.message} Scratchpad rollback failed or could not be proven safe.".strip(),
            rollback_failed=True,
            companion_committed=True,
        )

    @staticmethod
    def _validated_move_root(plan: WorkspaceMovePlan) -> str | None:
        root = os.path.abspath(plan.root)
        source = os.path.abspath(plan.source_path)
        destination = os.path.abspath(plan.destination_path)
        target = os.path.abspath(plan.target_path)
        try:
            safe = (
                source != root
                and os.path.dirname(target) == destination
                and os.path.basename(target) == plan.source_name
                and os.path.isdir(root)
                and not os.path.islink(root)
                and os.path.lexists(source)
                and not os.path.islink(source)
                and os.path.isdir(destination)
                and not os.path.islink(destination)
                and WorkspaceGioAdapter._target_within_root(root, source)
                and WorkspaceGioAdapter._target_within_root(root, destination)
                and WorkspaceGioAdapter._target_within_root(root, target)
            )
            if safe and plan.source_is_directory:
                safe = os.path.commonpath((os.path.realpath(source), os.path.realpath(destination))) != os.path.realpath(source)
        except (OSError, TypeError, ValueError):
            safe = False
        return root if safe else None

    @classmethod
    def _open_pinned_destination(cls, plan: WorkspaceMovePlan) -> int | None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            fd = os.open(plan.destination_path, flags)
        except OSError:
            return None
        try:
            pinned = os.fstat(fd)
            if not stat.S_ISDIR(pinned.st_mode):
                raise ValueError("planned destination is not a directory")
            if workspace_path_token(pinned) != plan.destination_token:
                raise ValueError("planned destination signature is stale")
            if cls._current_token(plan.destination_path) != plan.destination_token:
                raise ValueError("planned destination pathname changed")
            if not cls._path_matches_descriptor(plan.destination_path, fd):
                raise ValueError("planned destination pathname no longer binds the pinned object")
            return fd
        except (OSError, ValueError):
            os.close(fd)
            return None

    def _move_single(self, plan: WorkspaceMovePlan) -> WorkspaceMoveResult:
        """Commit one verified native Workspace namespace move with no copy fallback."""
        if not isinstance(plan, WorkspaceMovePlan):
            raise TypeError("plan must be WorkspaceMovePlan")
        if plan.kind != "move":
            raise ValueError(f"unsupported Workspace move operation: {plan.kind}")
        root = self._validated_move_root(plan)
        if root is None:
            return WorkspaceMoveResult(
                False, plan.source_path, message="The move paths changed or resolve outside the Workspace."
            )
        if Gio is None or GLib is None:
            return WorkspaceMoveResult(False, plan.source_path, message="GIO is unavailable; nothing was moved.")
        if plan.source_token.device != plan.destination_token.device:
            return WorkspaceMoveResult(False, plan.source_path, message="Cross-filesystem Workspace moves are not supported.")

        source_fd = self._open_pinned_source(plan)
        if source_fd is None:
            return WorkspaceMoveResult(False, plan.source_path, message="The selected item changed before it could be moved.")
        destination_fd = self._open_pinned_destination(plan)
        if destination_fd is None:
            os.close(source_fd)
            return WorkspaceMoveResult(False, plan.source_path, message="The destination changed before the item could be moved.")
        try:
            if os.fstat(source_fd).st_dev != os.fstat(destination_fd).st_dev:
                return WorkspaceMoveResult(False, plan.source_path, message="Cross-filesystem Workspace moves are not supported.")
            if os.path.lexists(plan.target_path):
                return WorkspaceMoveResult(False, plan.source_path, message="A file or folder with that name already exists in the destination.")
            if (
                self._current_token(plan.source_path) != plan.source_token
                or self._current_token(plan.destination_path) != plan.destination_token
                or not self._path_matches_descriptor(plan.source_path, source_fd)
                or not self._path_matches_descriptor(plan.destination_path, destination_fd)
                or os.path.lexists(plan.target_path)
            ):
                return WorkspaceMoveResult(False, plan.source_path, message="The source or destination changed before the move could commit.")

            returned = False
            error_message = "The selected item could not be moved."
            try:
                source_file = Gio.File.new_for_path(plan.source_path)
                target_file = Gio.File.new_for_path(plan.target_path)
                returned = bool(source_file.move(
                    target_file, Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE, None, None, None
                ))
            except GLib.Error as exc:
                error_message = f"The selected item could not be moved: {exc.message}"

            target_matches = self._path_matches_descriptor(plan.target_path, source_fd)
            committed = bool(returned or target_matches)
            try:
                target_state = os.lstat(plan.target_path)
                expected_type = stat.S_ISDIR(target_state.st_mode) if plan.source_is_directory else stat.S_ISREG(target_state.st_mode)
                verified = (
                    returned
                    and target_matches
                    and not os.path.lexists(plan.source_path)
                    and not stat.S_ISLNK(target_state.st_mode)
                    and expected_type
                    and self._target_within_root(root, plan.target_path)
                    and self._path_matches_descriptor(plan.destination_path, destination_fd)
                )
            except OSError:
                verified = False
            if verified:
                return WorkspaceMoveResult(
                    True,
                    plan.target_path,
                    committed=True,
                    object_token=workspace_path_token(os.fstat(source_fd)),
                )
            if committed:
                return WorkspaceMoveResult(
                    False,
                    plan.target_path,
                    committed=True,
                    message=(
                        "The filesystem move occurred, but final verification could not prove the "
                        "expected target identity. Workspace was refreshed; no automatic rollback was attempted."
                    ),
                    object_token=(workspace_path_token(os.fstat(source_fd)) if target_matches else None),
                )
            return WorkspaceMoveResult(False, plan.source_path, message=error_message)
        finally:
            for fd in (destination_fd, source_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass

    @staticmethod
    def _open_directory_descriptor(path: str) -> int | None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            fd = os.open(path, flags)
        except OSError:
            return None
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ValueError("not a directory")
            if not WorkspaceGioAdapter._path_matches_descriptor(path, fd):
                raise ValueError("directory pathname changed")
            return fd
        except (OSError, ValueError):
            os.close(fd)
            return None

    @staticmethod
    def _companion_move_plan(
        primary: WorkspaceMovePlan,
        *,
        source_path: str,
        destination_path: str,
        target_path: str,
        source_token: WorkspacePathToken,
        destination_token: WorkspacePathToken,
    ) -> WorkspaceMovePlan:
        return WorkspaceMovePlan(
            kind="move",
            root=primary.root,
            source_path=source_path,
            destination_path=destination_path,
            target_path=target_path,
            source_name=os.path.basename(source_path),
            source_is_directory=False,
            source_token=source_token,
            destination_token=destination_token,
            source_parent_path=os.path.dirname(source_path),
            companion=None,
        )

    def _rollback_moved_object(
        self,
        primary: WorkspaceMovePlan,
        *,
        moved_path: str,
        original_path: str,
        object_token: WorkspacePathToken | None,
        original_parent_fd: int | None,
    ) -> WorkspaceMoveResult | None:
        if object_token is None or original_parent_fd is None:
            return None
        original_parent = os.path.dirname(original_path)
        if not self._path_matches_descriptor(original_parent, original_parent_fd):
            return None
        if os.path.lexists(original_path) or self._current_token(moved_path) != object_token:
            return None
        try:
            destination_token = workspace_path_token(os.fstat(original_parent_fd))
        except OSError:
            return None
        rollback_plan = self._companion_move_plan(
            primary,
            source_path=moved_path,
            destination_path=original_parent,
            target_path=original_path,
            source_token=object_token,
            destination_token=destination_token,
        )
        return self._move_single(rollback_plan)

    def move(self, plan: WorkspaceMovePlan) -> WorkspaceMoveResult:
        """Move one Workspace object and its managed Scratchpad as one logical transaction."""
        if not isinstance(plan, WorkspaceMovePlan):
            raise TypeError("plan must be WorkspaceMovePlan")
        companion = plan.companion
        primary_plan = replace(plan, companion=None)
        if companion is None:
            return self._move_single(primary_plan)
        if companion.target_path is None:
            return WorkspaceMoveResult(False, plan.source_path, message="The Scratchpad move target is missing.")
        if not self._companion_source_matches(companion):
            return WorkspaceMoveResult(False, plan.source_path, message="The managed Scratchpad companion changed before move commit.")
        if not self._companion_target_free(companion):
            return WorkspaceMoveResult(False, plan.source_path, message="The managed Scratchpad destination already exists.")

        source_parent_fd = self._open_directory_descriptor(plan.source_parent_path or os.path.dirname(plan.source_path))
        if source_parent_fd is None:
            return WorkspaceMoveResult(False, plan.source_path, message="The source parent changed before the move could commit.")
        destination_fd = self._open_pinned_destination(primary_plan)
        if destination_fd is None:
            try:
                os.close(source_parent_fd)
            except OSError:
                pass
            return WorkspaceMoveResult(False, plan.source_path, message="The destination changed before the move could commit.")
        try:
            if companion.source_token is None:
                primary = self._move_single(primary_plan)
                if not primary.success:
                    return primary
                if not os.path.lexists(companion.target_path):
                    return primary
                rollback = self._rollback_moved_object(
                    primary_plan,
                    moved_path=plan.target_path,
                    original_path=plan.source_path,
                    object_token=primary.object_token,
                    original_parent_fd=source_parent_fd,
                )
                if rollback is not None and rollback.success:
                    return WorkspaceMoveResult(
                        False,
                        plan.source_path,
                        message="A managed Scratchpad destination appeared during move; the document was restored to its original folder.",
                    )
                return WorkspaceMoveResult(
                    False,
                    plan.target_path,
                    committed=True,
                    message="The document moved, but a conflicting managed Scratchpad destination appeared during commit.",
                    rollback_failed=True,
                    object_token=primary.object_token,
                )

            companion_plan = self._companion_move_plan(
                primary_plan,
                source_path=companion.source_path,
                destination_path=plan.destination_path,
                target_path=companion.target_path,
                source_token=companion.source_token,
                destination_token=plan.destination_token,
            )
            companion_result = self._move_single(companion_plan)
            if not companion_result.success:
                return WorkspaceMoveResult(
                    False,
                    plan.source_path,
                    message=f"The managed Scratchpad could not be moved safely. {companion_result.message}".strip(),
                    companion_committed=companion_result.committed,
                    rollback_failed=companion_result.rollback_failed,
                )

            # Moving the companion necessarily changes destination directory mtime/ctime.
            # Preserve directory identity through the descriptor pinned before either
            # mutation, then refresh the metadata token from that same object for the
            # primary move. A stale full token here would reject our own first commit.
            if not self._path_matches_descriptor(plan.destination_path, destination_fd):
                primary = WorkspaceMoveResult(
                    False,
                    plan.source_path,
                    message="The destination directory identity changed after the Scratchpad move.",
                )
            else:
                try:
                    refreshed_destination_token = workspace_path_token(os.fstat(destination_fd))
                except OSError:
                    refreshed_destination_token = None
                if refreshed_destination_token is None:
                    primary = WorkspaceMoveResult(
                        False,
                        plan.source_path,
                        message="The destination directory could not be revalidated after the Scratchpad move.",
                    )
                else:
                    primary = self._move_single(
                        replace(primary_plan, destination_token=refreshed_destination_token)
                    )
            if primary.success:
                if (
                    companion_result.object_token is not None
                    and self._current_token(companion.target_path) == companion_result.object_token
                ):
                    return WorkspaceMoveResult(
                        True,
                        primary.path,
                        committed=True,
                        companion_committed=True,
                        object_token=primary.object_token,
                    )
                return WorkspaceMoveResult(
                    False,
                    primary.path,
                    committed=True,
                    message="The document moved, but final Scratchpad companion identity could not be verified.",
                    companion_committed=True,
                    rollback_failed=True,
                    object_token=primary.object_token,
                )
            if primary.committed:
                return WorkspaceMoveResult(
                    False,
                    primary.path,
                    committed=True,
                    message=f"{primary.message} The Scratchpad companion remains at the managed target path.".strip(),
                    companion_committed=True,
                    rollback_failed=primary.rollback_failed,
                    object_token=primary.object_token,
                )

            rollback = self._rollback_moved_object(
                primary_plan,
                moved_path=companion.target_path,
                original_path=companion.source_path,
                object_token=companion_result.object_token,
                original_parent_fd=source_parent_fd,
            )
            if rollback is not None and rollback.success:
                return WorkspaceMoveResult(
                    False,
                    plan.source_path,
                    message=f"{primary.message} The Scratchpad companion was restored.".strip(),
                )
            return WorkspaceMoveResult(
                False,
                plan.source_path,
                message=f"{primary.message} Scratchpad rollback failed or could not be proven safe.".strip(),
                companion_committed=True,
                rollback_failed=True,
            )
        finally:
            for fd in (destination_fd, source_parent_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass

    @staticmethod
    def _validated_trash_parent(plan: WorkspaceTrashPlan) -> str | None:
        root = os.path.abspath(plan.root)
        parent = os.path.abspath(plan.parent_path)
        source = os.path.abspath(plan.source_path)
        try:
            safe = (
                source != root
                and os.path.dirname(source) == parent
                and os.path.isdir(root)
                and not os.path.islink(root)
                and os.path.isdir(parent)
                and not os.path.islink(parent)
                and os.path.commonpath((os.path.realpath(root), os.path.realpath(parent)))
                == os.path.realpath(root)
                and os.path.commonpath((root, source)) == root
            )
        except (OSError, TypeError, ValueError):
            safe = False
        return root if safe else None

    @staticmethod
    def _trash_capability(file_obj, expected_type) -> tuple[bool, str]:
        try:
            info = file_obj.query_info(
                "standard::type,standard::is-symlink,access::can-trash",
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                None,
            )
        except GLib.Error as exc:
            return False, f"Trash capability could not be verified: {exc.message}"
        if info.get_is_symlink() or info.get_file_type() != expected_type:
            return False, "The selected item changed type or became a symbolic link."
        if info.has_attribute("access::can-trash") and not info.get_attribute_boolean("access::can-trash"):
            return False, "The filesystem reports that this item cannot be moved to Trash."
        return True, ""

    def _prepare_trash(self, plan: WorkspaceTrashPlan):
        if not isinstance(plan, WorkspaceTrashPlan):
            raise TypeError("plan must be WorkspaceTrashPlan")
        if plan.kind != "move-to-trash":
            raise ValueError(f"unsupported Workspace Trash operation: {plan.kind}")
        root = self._validated_trash_parent(plan)
        if root is None:
            return None, WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="The selected item parent changed or resolves outside the Workspace.",
            )
        if Gio is None or GLib is None:
            return None, WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="GIO is unavailable; nothing was moved to Trash.",
            )
        if os.path.islink(plan.source_path) or not self._target_within_root(root, plan.source_path):
            return None, WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="The selected item changed before it could be moved to Trash.",
            )

        source_fd = self._open_pinned_source(plan)
        if source_fd is None:
            return None, WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="The selected item changed before it could be moved to Trash.",
            )
        source = Gio.File.new_for_path(plan.source_path)
        expected_type = Gio.FileType.DIRECTORY if plan.source_is_directory else Gio.FileType.REGULAR
        trashable, message = self._trash_capability(source, expected_type)
        if not trashable:
            try:
                os.close(source_fd)
            except OSError:
                pass
            return None, WorkspaceTrashResult(False, plan.parent_path, plan.source_path, message=message)
        if (
            self._current_token(plan.source_path) != plan.source_token
            or not self._path_matches_descriptor(plan.source_path, source_fd)
        ):
            try:
                os.close(source_fd)
            except OSError:
                pass
            return None, WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="The selected item changed before it could be moved to Trash.",
            )
        return (source_fd, source), None

    def _commit_prepared_trash(self, plan: WorkspaceTrashPlan, prepared) -> WorkspaceTrashResult:
        source_fd, source = prepared
        if (
            self._current_token(plan.source_path) != plan.source_token
            or not self._path_matches_descriptor(plan.source_path, source_fd)
        ):
            return WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="The selected item changed before it could be moved to Trash.",
            )
        try:
            accepted = bool(source.trash(None))
        except GLib.Error as exc:
            return WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message=f"The selected item could not be moved to Trash: {exc.message}",
            )
        if not accepted:
            return WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="The system Trash operation was not accepted.",
            )
        if os.path.lexists(plan.source_path):
            return WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path, accepted=True,
                message=(
                    "The system accepted the Trash operation, but absence of the original "
                    "pathname could not be verified. Workspace was refreshed."
                ),
            )
        return WorkspaceTrashResult(
            True, plan.parent_path, plan.source_path, accepted=True, committed=True
        )

    @staticmethod
    def _companion_trash_plan(
        primary: WorkspaceTrashPlan,
        companion: WorkspaceManagedCompanionPlan,
    ) -> WorkspaceTrashPlan:
        if companion.source_token is None:
            raise ValueError("companion Trash plan requires a source token")
        return WorkspaceTrashPlan(
            kind="move-to-trash",
            root=primary.root,
            parent_path=os.path.dirname(companion.source_path),
            source_path=companion.source_path,
            source_name=os.path.basename(companion.source_path),
            source_is_directory=False,
            source_token=companion.source_token,
            companion=None,
        )

    def trash(self, plan: WorkspaceTrashPlan) -> WorkspaceTrashResult:
        """Trash primary and managed Scratchpad with explicit committed-partial outcomes."""
        if not isinstance(plan, WorkspaceTrashPlan):
            raise TypeError("plan must be WorkspaceTrashPlan")
        companion = plan.companion
        primary_plan = replace(plan, companion=None)
        if companion is not None and not self._companion_source_matches(companion):
            return WorkspaceTrashResult(
                False, plan.parent_path, plan.source_path,
                message="The managed Scratchpad companion changed before Trash commit.",
            )

        primary_prepared, primary_error = self._prepare_trash(primary_plan)
        if primary_error is not None:
            return primary_error
        companion_prepared = None
        companion_plan = None
        try:
            if companion is not None and companion.source_token is not None:
                companion_plan = self._companion_trash_plan(primary_plan, companion)
                companion_prepared, companion_error = self._prepare_trash(companion_plan)
                if companion_error is not None:
                    return WorkspaceTrashResult(
                        False,
                        plan.parent_path,
                        plan.source_path,
                        message=f"The managed Scratchpad cannot be moved to Trash safely. {companion_error.message}".strip(),
                    )

            primary_result = self._commit_prepared_trash(primary_plan, primary_prepared)
            if not primary_result.success:
                return primary_result

            if companion is None:
                return primary_result
            if companion.source_token is None:
                if os.path.lexists(companion.source_path):
                    return WorkspaceTrashResult(
                        False,
                        plan.parent_path,
                        plan.source_path,
                        accepted=True,
                        committed=True,
                        message=(
                            "The document was moved to Trash, but a managed Scratchpad companion appeared during commit and was not touched."
                        ),
                    )
                return primary_result

            companion_result = self._commit_prepared_trash(companion_plan, companion_prepared)
            if companion_result.success:
                return WorkspaceTrashResult(
                    True,
                    plan.parent_path,
                    plan.source_path,
                    accepted=True,
                    committed=True,
                    companion_committed=True,
                )
            return WorkspaceTrashResult(
                False,
                plan.parent_path,
                plan.source_path,
                accepted=True,
                committed=True,
                companion_committed=companion_result.committed,
                message=(
                    "The document was moved to Trash, but its managed Scratchpad companion was not fully moved to Trash. "
                    + (companion_result.message or "")
                ).strip(),
            )
        finally:
            for prepared in (companion_prepared, primary_prepared):
                if prepared is None:
                    continue
                try:
                    os.close(prepared[0])
                except OSError:
                    pass

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(fd, view[offset:])
            if written <= 0:
                raise OSError("short/zero duplicate write")
            offset += written

    @classmethod
    def _hash_fd(cls, fd: int) -> tuple[int, str]:
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            digest.update(block)
        return total, digest.hexdigest()

    @classmethod
    def _open_pinned_regular_source(cls, plan: WorkspaceDuplicatePlan) -> int | None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(plan.source_path, flags)
        except OSError:
            return None
        try:
            pinned = os.fstat(fd)
            if not stat.S_ISREG(pinned.st_mode):
                raise ValueError("planned source is not a regular file")
            if workspace_path_token(pinned) != plan.source_token:
                raise ValueError("planned source signature is stale")
            if cls._current_token(plan.source_path) != plan.source_token:
                raise ValueError("planned source pathname changed")
            if not cls._path_matches_descriptor(plan.source_path, fd):
                raise ValueError("planned source pathname no longer binds the pinned object")
            return fd
        except (OSError, ValueError):
            os.close(fd)
            return None

    @classmethod
    def _name_matches_descriptor(cls, name: str, directory_fd: int, fd: int) -> bool:
        try:
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            pinned = os.fstat(fd)
        except OSError:
            return False
        return cls._object_id(named) == cls._object_id(pinned)

    @classmethod
    def _unlink_owned_name(cls, name: str, directory_fd: int, fd: int) -> bool:
        if not cls._name_matches_descriptor(name, directory_fd, fd):
            return False
        try:
            os.unlink(name, dir_fd=directory_fd)
        except OSError:
            return False
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False

    def _duplicate_single(self, plan: WorkspaceDuplicatePlan) -> WorkspaceDuplicateResult:
        """Create one verified sibling copy from saved disk bytes only.

        The source object is descriptor-pinned. Bytes are copied into an exclusive hidden
        sibling stage, read back and SHA-256 verified, then committed without overwrite via
        a same-directory hard link. This follows Graphium Core's guarded-write ownership
        model rather than relying on path-only postconditions from older Workspace code.
        """
        if not isinstance(plan, WorkspaceDuplicatePlan):
            raise TypeError("plan must be WorkspaceDuplicatePlan")
        if plan.kind != "duplicate-file":
            raise ValueError(f"unsupported Workspace duplicate operation: {plan.kind}")
        root = self._validated_parent(plan)
        if root is None:
            return WorkspaceDuplicateResult(False, plan.source_path, message="The containing folder changed or resolves outside the Workspace.")
        if os.path.islink(plan.source_path) or not self._target_within_root(root, plan.source_path):
            return WorkspaceDuplicateResult(False, plan.source_path, message="The selected file changed before it could be duplicated.")

        source_fd = self._open_pinned_regular_source(plan)
        if source_fd is None:
            return WorkspaceDuplicateResult(False, plan.source_path, message="The selected file changed before it could be duplicated.")

        directory_fd = None
        stage_fd = None
        stage_name = f".graphium-plus-duplicate-{os.getpid()}-{secrets.token_hex(16)}.tmp"
        target_name = os.path.basename(plan.target_path)
        stage_identity = None
        committed = False
        try:
            dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                directory_fd = os.open(plan.parent_path, dir_flags)
            except OSError as exc:
                return WorkspaceDuplicateResult(False, plan.source_path, message=f"The containing folder cannot be opened safely: {exc}")
            if not self._path_matches_descriptor(plan.parent_path, directory_fd):
                return WorkspaceDuplicateResult(False, plan.source_path, message="The containing folder changed before duplication.")
            if self._current_token(plan.source_path) != plan.source_token or not self._path_matches_descriptor(plan.source_path, source_fd):
                return WorkspaceDuplicateResult(False, plan.source_path, message="The selected file changed before it could be duplicated.")
            try:
                os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                return WorkspaceDuplicateResult(False, plan.source_path, message=f"The duplicate destination cannot be inspected safely: {exc}")
            else:
                return WorkspaceDuplicateResult(False, plan.source_path, message="The duplicate destination already exists.")

            stage_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                stage_fd = os.open(stage_name, stage_flags, 0o600, dir_fd=directory_fd)
            except OSError as exc:
                return WorkspaceDuplicateResult(False, plan.source_path, message=f"A safe duplicate stage could not be created: {exc}")
            stage_identity = self._object_id(os.fstat(stage_fd))

            source_before = os.fstat(source_fd)
            os.lseek(source_fd, 0, os.SEEK_SET)
            copied_digest = hashlib.sha256()
            copied_size = 0
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                self._write_all(stage_fd, block)
                copied_digest.update(block)
                copied_size += len(block)
            source_after = os.fstat(source_fd)
            if (
                workspace_path_token(source_before) != plan.source_token
                or workspace_path_token(source_after) != plan.source_token
                or copied_size != plan.source_token.size
                or self._current_token(plan.source_path) != plan.source_token
                or not self._path_matches_descriptor(plan.source_path, source_fd)
            ):
                return WorkspaceDuplicateResult(False, plan.source_path, message="The selected file changed while it was being duplicated.")

            os.fchmod(stage_fd, stat.S_IMODE(source_before.st_mode))
            os.fsync(stage_fd)
            verified_size, verified_digest = self._hash_fd(stage_fd)
            if verified_size != copied_size or verified_digest != copied_digest.hexdigest():
                return WorkspaceDuplicateResult(False, plan.source_path, message="The staged duplicate bytes could not be verified.")

            # Late revalidation immediately before the no-overwrite namespace commit.
            if (
                not self._path_matches_descriptor(plan.parent_path, directory_fd)
                or self._current_token(plan.source_path) != plan.source_token
                or not self._path_matches_descriptor(plan.source_path, source_fd)
            ):
                return WorkspaceDuplicateResult(False, plan.source_path, message="The source or containing folder changed before duplicate commit.")
            try:
                os.link(
                    stage_name,
                    target_name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                return WorkspaceDuplicateResult(False, plan.source_path, message="The duplicate destination appeared before commit.")
            except OSError as exc:
                return WorkspaceDuplicateResult(False, plan.source_path, message=f"The duplicate could not be committed safely: {exc}")
            committed = True
            try:
                os.unlink(stage_name, dir_fd=directory_fd)
            except OSError:
                # The final target is already committed; verification/rollback below still owns it via stage_fd.
                pass

            target_matches = self._name_matches_descriptor(target_name, directory_fd, stage_fd)
            try:
                target_stat = os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError:
                target_stat = None
            source_still_same = (
                self._current_token(plan.source_path) == plan.source_token
                and self._path_matches_descriptor(plan.source_path, source_fd)
            )
            if (
                target_matches
                and target_stat is not None
                and stat.S_ISREG(target_stat.st_mode)
                and source_still_same
                and self._target_within_root(root, plan.target_path)
            ):
                warning = ""
                try:
                    os.fsync(directory_fd)
                except OSError as exc:
                    warning = f"Duplicate created; parent-directory durability sync failed: {exc}"
                return WorkspaceDuplicateResult(
                    True,
                    plan.target_path,
                    committed=True,
                    message=warning,
                    object_token=workspace_path_token(os.fstat(stage_fd)),
                )

            if target_matches and self._unlink_owned_name(target_name, directory_fd, stage_fd):
                committed = False
                return WorkspaceDuplicateResult(False, plan.source_path, message="Duplicate verification failed; the created target was removed.")
            return WorkspaceDuplicateResult(
                False,
                plan.target_path,
                committed=True,
                message="Duplicate verification failed and the created target could not be removed safely.",
                rollback_failed=True,
            )
        except OSError as exc:
            if committed and directory_fd is not None and stage_fd is not None and self._name_matches_descriptor(target_name, directory_fd, stage_fd):
                if self._unlink_owned_name(target_name, directory_fd, stage_fd):
                    committed = False
            return WorkspaceDuplicateResult(
                False,
                plan.target_path if committed else plan.source_path,
                committed=committed,
                message=f"The selected file could not be duplicated safely: {exc}",
                rollback_failed=committed,
            )
        finally:
            if directory_fd is not None and stage_fd is not None and stage_identity is not None:
                try:
                    if self._name_matches_descriptor(stage_name, directory_fd, stage_fd):
                        os.unlink(stage_name, dir_fd=directory_fd)
                except OSError:
                    pass
            if stage_fd is not None:
                try:
                    os.close(stage_fd)
                except OSError:
                    pass
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass
            try:
                os.close(source_fd)
            except OSError:
                pass

    @classmethod
    def _unlink_owned_path_token(cls, path: str, token: WorkspacePathToken | None) -> bool:
        if token is None:
            return False
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(path, flags)
        except OSError:
            return False
        try:
            try:
                observed = os.fstat(fd)
            except OSError:
                return False
            if not stat.S_ISREG(observed.st_mode):
                return False
            if workspace_path_token(observed) != token:
                return False
            if cls._current_token(path) != token or not cls._path_matches_descriptor(path, fd):
                return False
            try:
                os.unlink(path)
            except OSError:
                return False
            return not os.path.lexists(path)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    @staticmethod
    def _companion_duplicate_plan(
        primary: WorkspaceDuplicatePlan,
        companion: WorkspaceManagedCompanionPlan,
    ) -> WorkspaceDuplicatePlan:
        if companion.target_path is None or companion.source_token is None:
            raise ValueError("companion duplicate plan requires source and target")
        return WorkspaceDuplicatePlan(
            kind="duplicate-file",
            root=primary.root,
            parent_path=primary.parent_path,
            source_path=companion.source_path,
            target_path=companion.target_path,
            source_name=os.path.basename(companion.source_path),
            display_name=os.path.basename(companion.target_path),
            source_token=companion.source_token,
            companion=None,
        )

    def duplicate(self, plan: WorkspaceDuplicatePlan) -> WorkspaceDuplicateResult:
        """Duplicate saved primary bytes and, when present, saved Scratchpad bytes."""
        if not isinstance(plan, WorkspaceDuplicatePlan):
            raise TypeError("plan must be WorkspaceDuplicatePlan")
        companion = plan.companion
        primary_plan = replace(plan, companion=None)
        if companion is None:
            return self._duplicate_single(primary_plan)
        if companion.target_path is None:
            return WorkspaceDuplicateResult(False, plan.source_path, message="The Scratchpad duplicate target is missing.")
        if not self._companion_source_matches(companion):
            return WorkspaceDuplicateResult(False, plan.source_path, message="The managed Scratchpad companion changed before duplicate commit.")
        if not self._companion_target_free(companion):
            return WorkspaceDuplicateResult(False, plan.source_path, message="The managed Scratchpad duplicate destination already exists.")

        if companion.source_token is None:
            primary = self._duplicate_single(primary_plan)
            if not primary.success:
                return primary
            if not os.path.lexists(companion.target_path):
                return primary
            removed = self._unlink_owned_path_token(primary.path, primary.object_token)
            if removed:
                return WorkspaceDuplicateResult(
                    False,
                    plan.source_path,
                    message="A managed Scratchpad destination appeared during duplicate commit; the duplicate document was removed safely.",
                )
            return WorkspaceDuplicateResult(
                False,
                primary.path,
                committed=True,
                message="A managed Scratchpad destination appeared during duplicate commit and the duplicate document could not be removed safely.",
                rollback_failed=True,
                object_token=primary.object_token,
            )

        companion_plan = self._companion_duplicate_plan(primary_plan, companion)
        companion_result = self._duplicate_single(companion_plan)
        if not companion_result.success:
            return WorkspaceDuplicateResult(
                False,
                plan.source_path,
                message=f"The managed Scratchpad could not be duplicated safely. {companion_result.message}".strip(),
                companion_committed=companion_result.committed,
                rollback_failed=companion_result.rollback_failed,
            )

        primary = self._duplicate_single(primary_plan)
        if primary.success:
            if (
                companion_result.object_token is not None
                and self._current_token(companion.target_path) == companion_result.object_token
            ):
                return WorkspaceDuplicateResult(
                    True,
                    primary.path,
                    committed=True,
                    message=primary.message,
                    companion_committed=True,
                    object_token=primary.object_token,
                )
            primary_removed = self._unlink_owned_path_token(primary.path, primary.object_token)
            return WorkspaceDuplicateResult(
                False,
                plan.source_path if primary_removed else primary.path,
                committed=not primary_removed,
                message=(
                    "Final Scratchpad duplicate identity could not be verified; the duplicate document was removed safely."
                    if primary_removed
                    else "Final Scratchpad duplicate identity could not be verified and the duplicate document could not be removed safely."
                ),
                rollback_failed=not primary_removed,
                companion_committed=True,
                object_token=None if primary_removed else primary.object_token,
            )
        if primary.committed:
            return WorkspaceDuplicateResult(
                False,
                primary.path,
                committed=True,
                message=f"{primary.message} The duplicated Scratchpad remains at the managed target path.".strip(),
                rollback_failed=primary.rollback_failed,
                companion_committed=True,
                object_token=primary.object_token,
            )

        companion_removed = self._unlink_owned_path_token(
            companion.target_path,
            companion_result.object_token,
        )
        if companion_removed:
            return WorkspaceDuplicateResult(
                False,
                plan.source_path,
                message=f"{primary.message} The staged Scratchpad duplicate was removed safely.".strip(),
            )
        return WorkspaceDuplicateResult(
            False,
            plan.source_path,
            message=f"{primary.message} Scratchpad duplicate rollback failed or could not be proven safe.".strip(),
            rollback_failed=True,
            companion_committed=True,
        )

