"""Crash-safe replacement of tool configuration files."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def _umask_mode() -> int:
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def atomic_write_text(path: Path | str, text: str, default_mode: int | None = None) -> None:
    """Replace ``path`` with ``text`` without ever leaving a partial file.

    Writes a temporary file in the same directory, fsyncs it and swaps it in
    with ``os.replace``. A symlinked path is followed so the shared file it
    points at is replaced, not the link. An existing file keeps its mode; a
    new one gets ``default_mode`` (or the umask default when that is None).
    """
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        mode = stat.S_IMODE(target.stat().st_mode)
    else:
        mode = _umask_mode() if default_mode is None else default_mode
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    try:
        directory = os.open(target.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory)
    except OSError:
        pass
    finally:
        os.close(directory)
