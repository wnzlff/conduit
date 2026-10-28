"""SFTP file browser panel shown next to a terminal session."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable

import paramiko
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from conduit.core.sftp_session import RemoteEntry, SftpWorker, human_size
from conduit.ui.icons import icon

ROLE_ENTRY = Qt.ItemDataRole.UserRole


class SftpPanel(QWidget):
    closed = Signal()
    message = Signal(str)

    def __init__(self, open_sftp: Callable[[], paramiko.SFTPClient], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sftpPanel")
        self.setAcceptDrops(True)
        self._open_sftp = open_sftp
        self.worker: SftpWorker | None = None
        self.current_path = "/"
        self._icon_color = "#8a93a6"
        self._active_jobs: dict[str, str] = {}
        self._pending_downloads: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("SFTP")
        title.setObjectName("subtitle")
        header.addWidget(title)
        header.addStretch(1)
        self.close_button = self._tool("close", "Close SFTP panel", self.closed.emit)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        nav = QHBoxLayout()
        nav.setSpacing(2)
        self.up_button = self._tool("up", "Parent folder (Backspace)", self.go_up)
        self.home_button = self._tool("home", "Home folder", self.go_home)
        self.refresh_button = self._tool("refresh", "Refresh (F5)", self.refresh)
        nav.addWidget(self.up_button)
        nav.addWidget(self.home_button)
        nav.addWidget(self.refresh_button)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("/remote/path")
        self.path_edit.returnPressed.connect(lambda: self.navigate(self.path_edit.text()))
        nav.addWidget(self.path_edit, 1)
        self.upload_button = self._tool("upload", "Upload files...", self.upload_dialog)
        self.mkdir_button = self._tool("folder-plus", "New folder", self.new_folder)
        nav.addWidget(self.upload_button)
        nav.addWidget(self.mkdir_button)
        layout.addLayout(nav)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Name", "Size", "Modified", "Permissions"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemActivated.connect(self._activated)
        header_view = self.tree.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        self.status = QLabel("Connecting SFTP ...")
        self.status.setObjectName("muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        self.set_icon_color(self._icon_color)
        self._set_enabled(False)

    def _tool(self, name: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setObjectName("flat")
        button.setToolTip(tip)
        button.setProperty("iconName", name)
        button.clicked.connect(slot)
        return button

    def set_icon_color(self, color: str) -> None:
        self._icon_color = color
        for button in self.findChildren(QToolButton):
            name = button.property("iconName")
            if name:
                button.setIcon(icon(name, color))
        self._refill()

    # Lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if self.worker is not None:
            return
        self.worker = SftpWorker(self._open_sftp, self)
        self.worker.ready.connect(self._on_ready)
        self.worker.listing.connect(self._on_listing)
        self.worker.progress.connect(self._on_progress)
        self.worker.job_started.connect(self._on_job_started)
        self.worker.job_finished.connect(self._on_job_finished)
        self.worker.error.connect(self._on_error)
        self.worker.closed.connect(self._on_closed)
        self.worker.start()

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.shutdown()
            self.worker.wait(2000)
            self.worker = None
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.tree, self.path_edit, self.up_button, self.home_button, self.refresh_button, self.upload_button, self.mkdir_button):
            widget.setEnabled(enabled)

    # Worker callbacks ------------------------------------------------------

    def _on_ready(self) -> None:
        self._set_enabled(True)
        self.status.setText("Ready")
        self.navigate(self.worker.home if self.worker else "/")

    def _on_closed(self) -> None:
        self._set_enabled(False)
        self.status.setText("SFTP closed")

    def _on_error(self, text: str) -> None:
        self.status.setText(text)
        self.message.emit(text)

    def _on_listing(self, path: str, entries: list) -> None:
        self.current_path = path
        self.path_edit.setText(path)
        self._entries: list[RemoteEntry] = entries
        self._refill()
        self.status.setText(f"{len(entries)} item(s)")

    def _refill(self) -> None:
        self.tree.clear()
        for entry in getattr(self, "_entries", []):
            item = QTreeWidgetItem(
                [
                    entry.name,
                    "" if entry.is_dir else human_size(entry.size),
                    datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M") if entry.mtime else "",
                    entry.permissions,
                ]
            )
            item.setData(0, ROLE_ENTRY, entry)
            name = "folder" if entry.is_dir else ("link" if entry.is_link else "file")
            item.setIcon(0, icon(name, self._icon_color if not entry.is_dir else "#e0af68"))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(item)

    def _on_job_started(self, job_id: str, label: str) -> None:
        self._active_jobs[job_id] = label
        if label != "listdir":
            self.status.setText(label + " ...")

    def _on_progress(self, job_id: str, done: int, total: int) -> None:
        if total > 0:
            self.progress.show()
            self.progress.setValue(int(done * 100 / total))
            label = self._active_jobs.get(job_id, "Transfer")
            self.status.setText(f"{label}: {human_size(done)} / {human_size(total)}")

    def _on_job_finished(self, job_id: str, ok: bool, text: str) -> None:
        label = self._active_jobs.pop(job_id, "")
        if not self._active_jobs:
            self.progress.hide()
            self.progress.setValue(0)
        if label == "listdir":
            if not ok:
                self.status.setText(text)
                self.message.emit(text)
            return
        self.status.setText(text or ("Done" if ok else "Failed"))
        if text:
            self.message.emit(text)
        if ok and label.startswith(("Upload", "Deleted", "Created", "Renamed", "mkdir", "delete", "rename")):
            self.refresh()
        elif ok and label.startswith("Download"):
            local = self._pending_downloads.pop(job_id, "")
            if local:
                self.status.setText(f"Saved to {local}")

    # Navigation ------------------------------------------------------------

    def navigate(self, path: str) -> None:
        if self.worker is None:
            return
        self.worker.list_dir(path or "/")

    def refresh(self) -> None:
        self.navigate(self.current_path)

    def go_up(self) -> None:
        parent = str(PurePosixPath(self.current_path).parent)
        self.navigate(parent)

    def go_home(self) -> None:
        self.navigate(self.worker.home if self.worker else "/")

    def _activated(self, item: QTreeWidgetItem, _column: int) -> None:
        entry: RemoteEntry = item.data(0, ROLE_ENTRY)
        if entry.is_dir:
            self.navigate(entry.path)
        else:
            self.download_entries([entry])

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Backspace:
            self.go_up()
        elif event.key() == Qt.Key.Key_F5:
            self.refresh()
        elif event.key() == Qt.Key.Key_Delete:
            self.delete_selected()
        elif event.key() == Qt.Key.Key_F2:
            self.rename_selected()
        else:
            super().keyPressEvent(event)

    # Operations ------------------------------------------------------------

    def _selected_entries(self) -> list[RemoteEntry]:
        return [item.data(0, ROLE_ENTRY) for item in self.tree.selectedItems()]

    def download_entries(self, entries: list[RemoteEntry]) -> None:
        if self.worker is None or not entries:
            return
        files = [e for e in entries if not e.is_dir]
        if not files:
            QMessageBox.information(self, "Download", "Folder download is not supported yet. Select files instead.")
            return
        if len(files) == 1:
            target, _ = QFileDialog.getSaveFileName(self, "Save file as", str(Path.home() / files[0].name))
            if not target:
                return
            job_id = self.worker.download(files[0].path, target)
            self._pending_downloads[job_id] = target
            return
        folder = QFileDialog.getExistingDirectory(self, "Download into folder", str(Path.home()))
        if not folder:
            return
        for entry in files:
            job_id = self.worker.download(entry.path, os.path.join(folder, entry.name))
            self._pending_downloads[job_id] = os.path.join(folder, entry.name)

    def upload_paths(self, paths: list[str]) -> None:
        if self.worker is None:
            return
        for local in paths:
            remote = str(PurePosixPath(self.current_path) / Path(local).name)
            self.worker.upload(local, remote)

    def upload_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Upload files", str(Path.home()))
        if paths:
            self.upload_paths(paths)

    def new_folder(self) -> None:
        if self.worker is None:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name.strip():
            self.worker.mkdir(str(PurePosixPath(self.current_path) / name.strip()))

    def delete_selected(self) -> None:
        entries = self._selected_entries()
        if self.worker is None or not entries:
            return
        names = "\n".join(e.name for e in entries[:8]) + ("\n..." if len(entries) > 8 else "")
        answer = QMessageBox.question(
            self,
            "Delete",
            f"Delete {len(entries)} item(s) on the server? This cannot be undone.\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            for entry in entries:
                self.worker.delete(entry.path, entry.is_dir)

    def rename_selected(self) -> None:
        entries = self._selected_entries()
        if self.worker is None or len(entries) != 1:
            return
        entry = entries[0]
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=entry.name)
        if ok and name.strip() and name.strip() != entry.name:
            self.worker.rename(entry.path, str(PurePosixPath(self.current_path) / name.strip()))

    def _context_menu(self, pos: QPoint) -> None:
        entries = self._selected_entries()
        menu = QMenu(self)
        download = menu.addAction(icon("download", self._icon_color), "Download...")
        download.setEnabled(any(not e.is_dir for e in entries))
        download.triggered.connect(lambda: self.download_entries(entries))
        upload = menu.addAction(icon("upload", self._icon_color), "Upload files here...")
        upload.triggered.connect(self.upload_dialog)
        menu.addSeparator()
        rename = menu.addAction(icon("edit", self._icon_color), "Rename (F2)")
        rename.setEnabled(len(entries) == 1)
        rename.triggered.connect(self.rename_selected)
        copy_path = menu.addAction(icon("copy", self._icon_color), "Copy remote path")
        copy_path.setEnabled(len(entries) == 1)
        copy_path.triggered.connect(lambda: self._copy_path(entries[0]))
        mkdir = menu.addAction(icon("folder-plus", self._icon_color), "New folder...")
        mkdir.triggered.connect(self.new_folder)
        menu.addSeparator()
        delete = menu.addAction(icon("trash", "#f7768e"), "Delete (Del)")
        delete.setEnabled(bool(entries))
        delete.triggered.connect(self.delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _copy_path(self, entry: RemoteEntry) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(entry.path)
        self.status.setText("Path copied")

    # Drag & drop from Explorer --------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and self.worker is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.upload_paths(paths)
            event.acceptProposedAction()
