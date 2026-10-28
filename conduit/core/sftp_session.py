"""SFTP operations on a worker thread, fed by a job queue."""

from __future__ import annotations

import logging
import os
import queue
import stat
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import paramiko
from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


@dataclass
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    is_link: bool
    size: int
    mtime: float
    mode: int

    @property
    def permissions(self) -> str:
        return stat.filemode(self.mode)


@dataclass
class SftpJob:
    kind: str  # listdir | download | upload | mkdir | delete | rename | stat
    args: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancel: threading.Event = field(default_factory=threading.Event)

    def label(self) -> str:
        if self.kind == "download":
            return f"Download {PurePosixPath(self.args['remote']).name}"
        if self.kind == "upload":
            return f"Upload {Path(self.args['local']).name}"
        return self.kind


class SftpWorker(QThread):
    """Runs SFTP jobs sequentially. All results are reported through signals."""

    ready = Signal()
    listing = Signal(str, list)  # path, list[RemoteEntry]
    progress = Signal(str, int, int)  # job id, done bytes, total bytes
    job_started = Signal(str, str)  # job id, label
    job_finished = Signal(str, bool, str)  # job id, ok, message
    error = Signal(str)
    closed = Signal()

    def __init__(self, open_sftp: Callable[[], paramiko.SFTPClient], parent=None) -> None:
        super().__init__(parent)
        self._open_sftp = open_sftp
        self._jobs: "queue.Queue[SftpJob | None]" = queue.Queue()
        self._sftp: paramiko.SFTPClient | None = None
        self._stop = threading.Event()
        self.home: str = "/"

    # UI-side API -----------------------------------------------------------

    def submit(self, job: SftpJob) -> str:
        self._jobs.put(job)
        return job.id

    def list_dir(self, path: str) -> str:
        return self.submit(SftpJob("listdir", {"path": path}))

    def download(self, remote: str, local: str) -> str:
        return self.submit(SftpJob("download", {"remote": remote, "local": local}))

    def upload(self, local: str, remote: str) -> str:
        return self.submit(SftpJob("upload", {"local": local, "remote": remote}))

    def mkdir(self, path: str) -> str:
        return self.submit(SftpJob("mkdir", {"path": path}))

    def delete(self, path: str, is_dir: bool) -> str:
        return self.submit(SftpJob("delete", {"path": path, "is_dir": is_dir}))

    def rename(self, old: str, new: str) -> str:
        return self.submit(SftpJob("rename", {"old": old, "new": new}))

    def shutdown(self) -> None:
        self._stop.set()
        self._jobs.put(None)

    # Worker ----------------------------------------------------------------

    def run(self) -> None:
        try:
            self._sftp = self._open_sftp()
            try:
                self.home = self._sftp.normalize(".")
            except OSError:
                self.home = "/"
        except Exception as exc:
            self.error.emit(f"Could not open SFTP channel: {exc}")
            self.closed.emit()
            return
        self.ready.emit()
        while not self._stop.is_set():
            job = self._jobs.get()
            if job is None:
                break
            self._run_job(job)
        try:
            self._sftp.close()
        except Exception:
            pass
        self.closed.emit()

    def _run_job(self, job: SftpJob) -> None:
        sftp = self._sftp
        assert sftp is not None
        handler = getattr(self, f"_job_{job.kind}", None)
        if handler is None:
            self.job_finished.emit(job.id, False, f"Unknown job {job.kind}")
            return
        self.job_started.emit(job.id, job.label())
        try:
            message = handler(sftp, job) or ""
        except _Cancelled:
            self.job_finished.emit(job.id, False, "Cancelled")
            return
        except (OSError, paramiko.SSHException) as exc:
            self.job_finished.emit(job.id, False, f"{job.label()} failed: {exc}")
            return
        except Exception as exc:  # pragma: no cover
            log.exception("SFTP job failed")
            self.job_finished.emit(job.id, False, f"{job.label()} failed: {exc}")
            return
        self.job_finished.emit(job.id, True, message)

    # Job implementations ---------------------------------------------------

    def _job_listdir(self, sftp: paramiko.SFTPClient, job: SftpJob) -> str:
        path = job.args["path"] or "."
        path = sftp.normalize(path)
        entries: list[RemoteEntry] = []
        for attr in sftp.listdir_attr(path):
            mode = attr.st_mode or 0
            is_link = stat.S_ISLNK(mode)
            is_dir = stat.S_ISDIR(mode)
            full = str(PurePosixPath(path) / attr.filename)
            if is_link:
                try:
                    target = sftp.stat(full)
                    is_dir = stat.S_ISDIR(target.st_mode or 0)
                except OSError:
                    pass
            entries.append(
                RemoteEntry(
                    name=attr.filename,
                    path=full,
                    is_dir=is_dir,
                    is_link=is_link,
                    size=attr.st_size or 0,
                    mtime=float(attr.st_mtime or 0),
                    mode=mode,
                )
            )
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        self.listing.emit(path, entries)
        return ""

    def _job_download(self, sftp: paramiko.SFTPClient, job: SftpJob) -> str:
        remote, local = job.args["remote"], job.args["local"]
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        sftp.get(remote, local, callback=self._progress_cb(job))
        return f"Downloaded {PurePosixPath(remote).name}"

    def _job_upload(self, sftp: paramiko.SFTPClient, job: SftpJob) -> str:
        local, remote = job.args["local"], job.args["remote"]
        if os.path.isdir(local):
            self._upload_tree(sftp, job, Path(local), remote)
            return f"Uploaded folder {Path(local).name}"
        sftp.put(local, remote, callback=self._progress_cb(job))
        return f"Uploaded {Path(local).name}"

    def _upload_tree(self, sftp: paramiko.SFTPClient, job: SftpJob, local: Path, remote: str) -> None:
        try:
            sftp.mkdir(remote)
        except OSError:
            pass  # exists
        for child in sorted(local.iterdir()):
            if job.cancel.is_set():
                raise _Cancelled()
            target = str(PurePosixPath(remote) / child.name)
            if child.is_dir():
                self._upload_tree(sftp, job, child, target)
            else:
                sftp.put(str(child), target, callback=self._progress_cb(job))

    def _job_mkdir(self, sftp: paramiko.SFTPClient, job: SftpJob) -> str:
        sftp.mkdir(job.args["path"])
        return f"Created {PurePosixPath(job.args['path']).name}"

    def _job_delete(self, sftp: paramiko.SFTPClient, job: SftpJob) -> str:
        path = job.args["path"]
        if job.args.get("is_dir"):
            self._rmtree(sftp, path)
        else:
            sftp.remove(path)
        return f"Deleted {PurePosixPath(path).name}"

    def _rmtree(self, sftp: paramiko.SFTPClient, path: str) -> None:
        for attr in sftp.listdir_attr(path):
            child = str(PurePosixPath(path) / attr.filename)
            if stat.S_ISDIR(attr.st_mode or 0):
                self._rmtree(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(path)

    def _job_rename(self, sftp: paramiko.SFTPClient, job: SftpJob) -> str:
        sftp.rename(job.args["old"], job.args["new"])
        return f"Renamed to {PurePosixPath(job.args['new']).name}"

    def _progress_cb(self, job: SftpJob):
        def callback(done: int, total: int) -> None:
            if job.cancel.is_set() or self._stop.is_set():
                raise _Cancelled()
            self.progress.emit(job.id, done, total)

        return callback


class _Cancelled(Exception):
    pass


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
