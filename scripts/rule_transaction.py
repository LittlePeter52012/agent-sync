"""Atomic multi-file publication for synchronized rule outputs."""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class OutputTransactionError(RuntimeError):
    """Raised when a rule-output transaction cannot be published."""

    def __init__(self, *, recovered: bool):
        super().__init__("rule output transaction failed")
        self.recovered = recovered


class _IdentityConflict(OSError):
    """Raised when an output path no longer names its snapshotted object."""


def _lstat(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _identity(metadata: os.stat_result | None):
    if metadata is None:
        return None
    return metadata.st_dev, metadata.st_ino


def _validate_directory(path: Path) -> os.stat_result:
    metadata = _lstat(path)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode):
        raise OSError("invalid output parent")
    mode = stat.S_IMODE(metadata.st_mode)
    if not mode & 0o222 or not mode & 0o111:
        raise OSError("unwritable output parent")
    if not os.access(path, os.W_OK | os.X_OK):
        raise OSError("inaccessible output parent")
    return metadata


def _validate_destination(path: Path) -> os.stat_result | None:
    metadata = _lstat(path)
    if metadata is None:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("invalid output destination")
    if metadata.st_nlink != 1:
        raise OSError("hardlinked output destination")
    if not stat.S_IMODE(metadata.st_mode) & 0o222:
        raise OSError("unwritable output destination")
    return metadata


def _missing_directories(parent: Path) -> list[Path]:
    missing = []
    current = parent
    while _lstat(current) is None:
        missing.append(current)
        if current == current.parent:
            raise OSError("no existing output ancestor")
        current = current.parent
    _validate_directory(current)
    return list(reversed(missing))


def _default_file_mode() -> int:
    mask = os.umask(0)
    try:
        return 0o666 & ~mask
    finally:
        os.umask(mask)


@dataclass
class _Parent:
    path: Path
    descriptor: int
    identity: tuple[int, int]


@dataclass
class _Artifact:
    parent: _Parent
    name: str
    identity: tuple[int, int]
    kind: str

    @property
    def path(self) -> Path:
        return self.parent.path / self.name


@dataclass
class _Entry:
    destination: Path
    parent: _Parent
    name: str
    data: bytes
    mode: int
    metadata: os.stat_result | None
    original_data: bytes | None
    stage: _Artifact
    backup: _Artifact | None
    published: bool = False

    @property
    def original_identity(self):
        return _identity(self.metadata)


def _open_parent(path: Path) -> _Parent:
    metadata = _validate_directory(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(metadata):
            raise _IdentityConflict("output parent changed")
    except BaseException:
        os.close(descriptor)
        raise
    return _Parent(path, descriptor, _identity(opened))


def _stat_at(parent: _Parent, name: str):
    try:
        return os.stat(
            name,
            dir_fd=parent.descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _check_parent(parent: _Parent) -> None:
    if _identity(_lstat(parent.path)) != parent.identity:
        raise _IdentityConflict("output parent path changed")


def _read_at(parent: _Parent, name: str, expected_identity) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent.descriptor)
    try:
        if _identity(os.fstat(descriptor)) != expected_identity:
            raise _IdentityConflict("output file changed")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        if _identity(os.fstat(descriptor)) != expected_identity:
            raise _IdentityConflict("output file changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _new_artifact(
    parent: _Parent,
    prefix: str,
    kind: str,
    artifacts: list[_Artifact],
) -> tuple[_Artifact, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    while True:
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=parent.descriptor,
            )
            break
        except FileExistsError:
            continue
    registered = False
    try:
        artifact = _Artifact(
            parent,
            name,
            _identity(os.fstat(descriptor)),
            kind,
        )
        artifacts.append(artifact)
        registered = True
        return artifact, descriptor
    except BaseException:
        os.close(descriptor)
        if not registered:
            try:
                os.unlink(name, dir_fd=parent.descriptor)
            except OSError:
                pass
        raise


def _stage_file(
    parent: _Parent,
    prefix: str,
    kind: str,
    data: bytes,
    mode: int,
    artifacts: list[_Artifact],
) -> _Artifact:
    artifact, descriptor = _new_artifact(parent, prefix, kind, artifacts)
    raw_descriptor = descriptor
    try:
        handle = os.fdopen(descriptor, "wb")
        raw_descriptor = -1
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
    finally:
        if raw_descriptor != -1:
            os.close(raw_descriptor)
    return artifact


def _artifact_matches(artifact: _Artifact) -> bool:
    return _identity(_stat_at(artifact.parent, artifact.name)) == artifact.identity


def _apply_utime(
    artifact: _Artifact,
    ns: tuple[int, int],
    callback,
) -> None:
    if not _artifact_matches(artifact):
        raise _IdentityConflict("transaction artifact changed")
    if callback is None:
        os.utime(
            artifact.name,
            ns=ns,
            dir_fd=artifact.parent.descriptor,
            follow_symlinks=False,
        )
    else:
        callback(artifact.path, ns=ns)
    if not _artifact_matches(artifact):
        raise _IdentityConflict("transaction artifact changed")


def _apply_chmod(artifact: _Artifact, mode: int, callback) -> None:
    if not _artifact_matches(artifact):
        raise _IdentityConflict("transaction artifact changed")
    if callback is None:
        descriptor = os.open(
            artifact.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=artifact.parent.descriptor,
        )
        try:
            if _identity(os.fstat(descriptor)) != artifact.identity:
                raise _IdentityConflict("transaction artifact changed")
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)
    else:
        callback(artifact.path, mode)
    if not _artifact_matches(artifact):
        raise _IdentityConflict("transaction artifact changed")


def _replace_artifact(
    artifact: _Artifact,
    destination: _Entry,
    callback,
) -> None:
    if callback is None:
        os.replace(
            artifact.name,
            destination.name,
            src_dir_fd=artifact.parent.descriptor,
            dst_dir_fd=destination.parent.descriptor,
        )
    else:
        callback(artifact.path, destination.destination)


def _destination_bytes(entry: _Entry, expected_identity) -> bytes | None:
    metadata = _stat_at(entry.parent, entry.name)
    if _identity(metadata) != expected_identity:
        return None
    return _read_at(entry.parent, entry.name, expected_identity)


def _destination_is_stage(entry: _Entry) -> bool:
    return (
        _destination_bytes(entry, entry.stage.identity) == entry.data
    )


def _destination_is_original(entry: _Entry) -> bool:
    metadata = _stat_at(entry.parent, entry.name)
    if entry.metadata is None:
        return metadata is None
    if _identity(metadata) != entry.original_identity:
        return False
    if stat.S_IMODE(metadata.st_mode) != entry.mode:
        return False
    if metadata.st_mtime_ns != entry.metadata.st_mtime_ns:
        return False
    return (
        _read_at(entry.parent, entry.name, entry.original_identity)
        == entry.original_data
    )


def _verify_commit_state(
    entries: list[_Entry],
    created_directories: list[tuple[Path, tuple[int, int]]],
) -> None:
    for entry in entries:
        _check_parent(entry.parent)
        matches = (
            _destination_is_stage(entry)
            if entry.published
            else _destination_is_original(entry)
        )
        if not matches:
            raise _IdentityConflict("output destination changed")
        if not entry.published and not _artifact_matches(entry.stage):
            raise _IdentityConflict("staged output changed")
        if entry.backup is not None and not _artifact_matches(entry.backup):
            raise _IdentityConflict("output backup changed")
    for directory, identity in created_directories:
        if _identity(_lstat(directory)) != identity:
            raise _IdentityConflict("created output directory changed")


def _cleanup_artifacts(
    artifacts: Iterable[_Artifact],
    *,
    preserve_backups: bool,
) -> bool:
    cleaned = True
    for artifact in artifacts:
        if preserve_backups and artifact.kind == "backup":
            continue
        try:
            metadata = _stat_at(artifact.parent, artifact.name)
            if metadata is None:
                continue
            if _identity(metadata) != artifact.identity:
                cleaned = False
                continue
            os.unlink(artifact.name, dir_fd=artifact.parent.descriptor)
        except BaseException:
            cleaned = False
    return cleaned


def _remove_created_directories(
    directories: Iterable[tuple[Path, tuple[int, int]]],
) -> bool:
    cleaned = True
    for directory, identity in reversed(list(directories)):
        try:
            if _identity(_lstat(directory)) != identity:
                cleaned = False
                continue
            directory.rmdir()
        except BaseException:
            cleaned = False
    return cleaned


def _close_parents(parents: Iterable[_Parent]) -> bool:
    closed = True
    for parent in parents:
        try:
            os.close(parent.descriptor)
        except OSError:
            closed = False
    return closed


def _rollback_existing(
    entry: _Entry,
    artifacts: list[_Artifact],
    rollback_replace,
    rollback_chmod,
    rollback_utime,
) -> bool:
    if not _destination_is_stage(entry):
        return False
    backup = entry.backup
    if backup is None or not _artifact_matches(backup):
        return False
    backup_data = _read_at(backup.parent, backup.name, backup.identity)
    restore = _stage_file(
        entry.parent,
        ".agent-sync-rule-restore-",
        "restore",
        backup_data,
        entry.mode,
        artifacts,
    )
    try:
        _apply_chmod(restore, entry.mode, rollback_chmod)
        _apply_utime(
            restore,
            (entry.metadata.st_atime_ns, entry.metadata.st_mtime_ns),
            rollback_utime,
        )
        if not _destination_is_stage(entry):
            return False
        _replace_artifact(restore, entry, rollback_replace)
    except BaseException:
        metadata = _stat_at(entry.parent, entry.name)
        if _identity(metadata) != restore.identity:
            return False
    metadata = _stat_at(entry.parent, entry.name)
    if _identity(metadata) != restore.identity:
        return False
    if stat.S_IMODE(metadata.st_mode) != entry.mode:
        return False
    if metadata.st_mtime_ns != entry.metadata.st_mtime_ns:
        return False
    return (
        _read_at(entry.parent, entry.name, restore.identity)
        == entry.original_data
    )


def _rollback_new(entry: _Entry) -> bool:
    if not _destination_is_stage(entry):
        return False
    try:
        os.unlink(entry.name, dir_fd=entry.parent.descriptor)
    except BaseException:
        return _stat_at(entry.parent, entry.name) is None
    return _stat_at(entry.parent, entry.name) is None


def publish_outputs(
    outputs: Iterable[tuple[Path, bytes]],
    *,
    replace: Callable[[os.PathLike[str], os.PathLike[str]], None] | None = None,
    utime=None,
    rollback_replace=None,
    rollback_chmod=None,
    rollback_utime=None,
) -> None:
    """Stage and publish all outputs, rolling back completed replacements."""

    entries: list[_Entry] = []
    artifacts: list[_Artifact] = []
    parents: dict[Path, _Parent] = {}
    created_directories: list[tuple[Path, tuple[int, int]]] = []
    failure: BaseException | None = None
    try:
        materialized = [(Path(path), bytes(data)) for path, data in outputs]
        destinations = [path for path, _data in materialized]
        if len(set(destinations)) != len(destinations):
            raise OSError("duplicate output destination")

        missing_directories = []
        for destination, _data in materialized:
            _validate_destination(destination)
            missing_directories.extend(_missing_directories(destination.parent))
        missing_directories = sorted(
            set(missing_directories),
            key=lambda path: len(path.parts),
        )

        for directory in missing_directories:
            if _lstat(directory) is None:
                try:
                    directory.mkdir()
                    metadata = directory.lstat()
                    created_directories.append((directory, _identity(metadata)))
                except BaseException as error:
                    already_registered = any(
                        path == directory
                        for path, _identity_value in created_directories
                    )
                    if not isinstance(error, Exception) and not already_registered:
                        metadata = _lstat(directory)
                        if metadata is not None and stat.S_ISDIR(metadata.st_mode):
                            created_directories.append(
                                (directory, _identity(metadata))
                            )
                    raise
            _validate_directory(directory)

        default_mode = _default_file_mode()
        for destination, data in materialized:
            parent = parents.get(destination.parent)
            if parent is None:
                parent = _open_parent(destination.parent)
                parents[destination.parent] = parent
            _check_parent(parent)
            metadata = _validate_destination(destination)
            internal_metadata = _stat_at(parent, destination.name)
            if _identity(internal_metadata) != _identity(metadata):
                raise _IdentityConflict("output destination changed")
            mode = (
                stat.S_IMODE(metadata.st_mode)
                if metadata is not None
                else default_mode
            )
            original_data = None
            if metadata is not None:
                original_data = _read_at(
                    parent,
                    destination.name,
                    _identity(metadata),
                )
            stage = _stage_file(
                parent,
                ".agent-sync-rule-stage-",
                "stage",
                data,
                mode,
                artifacts,
            )
            backup = None
            if metadata is not None:
                backup = _stage_file(
                    parent,
                    ".agent-sync-rule-backup-",
                    "backup",
                    original_data,
                    mode,
                    artifacts,
                )
                _apply_utime(
                    backup,
                    (metadata.st_atime_ns, metadata.st_mtime_ns),
                    utime,
                )
            entries.append(
                _Entry(
                    destination,
                    parent,
                    destination.name,
                    data,
                    mode,
                    metadata,
                    original_data,
                    stage,
                    backup,
                )
            )

        _verify_commit_state(entries, created_directories)
        for entry in entries:
            _verify_commit_state(entries, created_directories)
            _replace_artifact(entry.stage, entry, replace)
            if not _destination_is_stage(entry):
                raise _IdentityConflict("published output changed")
            entry.published = True
            _verify_commit_state(entries, created_directories)
    except BaseException as error:
        failure = error

    if failure is None:
        cleaned = _cleanup_artifacts(artifacts, preserve_backups=False)
        cleaned = _close_parents(parents.values()) and cleaned
        if not cleaned:
            raise OutputTransactionError(recovered=False) from None
        return

    identity_conflict = isinstance(failure, _IdentityConflict)
    for parent in parents.values():
        try:
            _check_parent(parent)
        except BaseException:
            identity_conflict = True
    for directory, identity in created_directories:
        if _identity(_lstat(directory)) != identity:
            identity_conflict = True
    for artifact in artifacts:
        if artifact.kind == "backup" and not _artifact_matches(artifact):
            identity_conflict = True
    for entry in entries:
        try:
            is_stage = _destination_is_stage(entry)
            is_original = _destination_is_original(entry)
        except BaseException:
            identity_conflict = True
            continue
        if is_stage:
            entry.published = True
        elif not is_original:
            identity_conflict = True

    rollback_failed = False
    for entry in reversed(entries):
        if not entry.published:
            continue
        try:
            restored = (
                _rollback_new(entry)
                if entry.backup is None
                else _rollback_existing(
                    entry,
                    artifacts,
                    rollback_replace,
                    rollback_chmod,
                    rollback_utime,
                )
            )
        except BaseException:
            restored = False
        if not restored:
            rollback_failed = True

    recovered = not identity_conflict and not rollback_failed
    cleaned = _cleanup_artifacts(
        artifacts,
        preserve_backups=not recovered,
    )
    if recovered:
        cleaned = _remove_created_directories(created_directories) and cleaned
    recovered = recovered and cleaned

    recovered = _close_parents(parents.values()) and recovered

    if recovered and not isinstance(failure, Exception):
        raise failure
    raise OutputTransactionError(recovered=recovered) from None
