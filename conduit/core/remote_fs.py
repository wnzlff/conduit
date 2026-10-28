"""Lightweight remote directory listings over SFTP for path suggestions."""

from __future__ import annotations

import logging
import queue
import stat
import threading
from typing import Callable

import paramiko
from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


class RemotePathLister(QThread):
    """Lists remote directories on request. Results arrive via ``listed(path, entries)`` where
    entries are ``(name, is_dir)`` tuples. Failures are reported as an empty listing so callers
    can cache the miss."""

    ready = Signal(str)  # home directory
    listed = Signal(str, list)
    failed = Signal(str)

    def __init__(self, open_sftp: Callable[[], paramiko.SFTPClient], parent=None) -> None:
        super().__init__(parent)
        self._open_sftp = open_sftp
        self._requests: "queue.Queue[str | None]" = queue.Queue()
        self._stop = threading.Event()
        self.home: str | None = None

    def request(self, path: str) -> None:
        self._requests.put(path)

    def shutdown(self) -> None:
        self._stop.set()
        self._requests.put(None)

    def run(self) -> None:
        try:
            sftp = self._open_sftp()
        except Exception as exc:
            self.failed.emit(f"SFTP unavailable for path suggestions: {exc}")
            return
        try:
            self.home = sftp.normalize(".")
        except OSError:
            self.home = "/"
        self.ready.emit(self.home)
        while not self._stop.is_set():
            path = self._requests.get()
            if path is None:
                break
            entries: list[tuple[str, bool]] = []
            try:
                for attr in sftp.listdir_attr(path):
                    mode = attr.st_mode or 0
                    is_dir = stat.S_ISDIR(mode)
                    if stat.S_ISLNK(mode):
                        try:
                            is_dir = stat.S_ISDIR(sftp.stat(f"{path.rstrip('/')}/{attr.filename}").st_mode or 0)
                        except OSError:
                            pass
                    entries.append((attr.filename, is_dir))
            except (OSError, paramiko.SSHException) as exc:
                log.debug("listdir %s failed: %s", path, exc)
            entries.sort(key=lambda e: (not e[1], e[0].lower()))
            self.listed.emit(path, entries)
        try:
            sftp.close()
        except Exception:
            pass
