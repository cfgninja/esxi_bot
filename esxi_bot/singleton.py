"""Single-instance lock handling."""

from __future__ import annotations

import atexit
import os
import sys
import tempfile
from typing import Optional

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore

from .config import DEFAULT_LOCK_PATH

__all__ = ["ensure_single_instance"]


_lock_file: Optional[object] = None
_lock_path: Optional[str] = None


def _cleanup_lock() -> None:
    global _lock_file, _lock_path
    try:
        if _lock_file:
            try:
                _lock_file.close()
            except Exception:
                pass
            _lock_file = None
        if _lock_path and os.path.exists(_lock_path):
            try:
                os.unlink(_lock_path)
            except Exception:
                pass
    except Exception:
        pass


def ensure_single_instance() -> None:
    """Acquire an inter-process lock to avoid running multiple instances."""
    global _lock_file, _lock_path
    if _lock_file:
        return  # already locked

    lock_path = DEFAULT_LOCK_PATH or "/var/run/esxi_bot.lock"

    if fcntl is not None:
        try:
            _lock_file = open(lock_path, "w")
        except Exception:
            lock_path = os.path.join(tempfile.gettempdir(), "esxi_bot.lock")
            _lock_file = open(lock_path, "w")
        try:
            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_file.write(str(os.getpid()))
            _lock_file.flush()
            _lock_path = lock_path
        except OSError:
            sys.stderr.write("Another esxi_bot instance is already running. Exiting.\n")
            sys.exit(1)
    else:
        lock_path = os.path.join(tempfile.gettempdir(), "esxi_bot.lock")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as fp:
                fp.write(str(os.getpid()))
                fp.flush()
            _lock_file = open(lock_path, "r")
            _lock_path = lock_path
        except FileExistsError:
            sys.stderr.write("Another esxi_bot instance is already running. Exiting.\n")
            sys.exit(1)

    atexit.register(_cleanup_lock)
