"""Atomic multi-file publication for synchronized rule outputs."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Callable, Iterable


class OutputTransactionError(RuntimeError):
    """Raised after a rule-output transaction fails and is rolled back."""


def _lstat(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _validate_directory(path: Path) -> None:
    metadata = _lstat(path)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode):
        raise OSError("invalid output parent")
    mode = stat.S_IMODE(metadata.st_mode)
    if not mode & 0o222 or not mode & 0o111:
        raise OSError("unwritable output parent")
    if not os.access(path, os.W_OK | os.X_OK):
        raise OSError("inaccessible output parent")


def _validate_destination(path: Path) -> os.stat_result | None:
    metadata = _lstat(path)
    if metadata is None:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("invalid output destination")
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
    os.umask(mask)
    return 0o666 & ~mask


def _stage_file(parent: Path, prefix: str, data: bytes, mode: int) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, dir=parent)
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _cleanup(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def publish_outputs(
    outputs: Iterable[tuple[Path, bytes]],
    *,
    replace: Callable[[os.PathLike[str], os.PathLike[str]], None] = os.replace,
) -> None:
    """Stage and atomically publish all outputs, rolling back as one unit."""

    entries = []
    stages: list[Path] = []
    backups: list[Path] = []
    created_directories: list[Path] = []
    committed = []
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
                directory.mkdir()
                created_directories.append(directory)
            _validate_directory(directory)

        default_mode = _default_file_mode()
        for destination, data in materialized:
            _validate_directory(destination.parent)
            metadata = _validate_destination(destination)
            mode = (
                stat.S_IMODE(metadata.st_mode)
                if metadata is not None
                else default_mode
            )
            stage = _stage_file(
                destination.parent,
                ".agent-sync-rule-stage-",
                data,
                mode,
            )
            stages.append(stage)
            backup = None
            if metadata is not None:
                backup = _stage_file(
                    destination.parent,
                    ".agent-sync-rule-backup-",
                    destination.read_bytes(),
                    mode,
                )
                os.utime(
                    backup,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                )
                backups.append(backup)
            entries.append(
                {
                    "destination": destination,
                    "stage": stage,
                    "backup": backup,
                    "metadata": metadata,
                }
            )

        for entry in entries:
            replace(entry["stage"], entry["destination"])
            committed.append(entry)
    except Exception:
        for entry in reversed(committed):
            destination = entry["destination"]
            backup = entry["backup"]
            try:
                if backup is None:
                    destination.unlink(missing_ok=True)
                else:
                    os.replace(backup, destination)
                    metadata = entry["metadata"]
                    os.chmod(destination, stat.S_IMODE(metadata.st_mode))
                    os.utime(
                        destination,
                        ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                    )
            except OSError:
                pass
        _cleanup(stages)
        _cleanup(backups)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise OutputTransactionError("rule output transaction failed") from None
    else:
        _cleanup(stages)
        _cleanup(backups)
