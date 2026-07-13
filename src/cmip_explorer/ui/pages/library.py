from __future__ import annotations

import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QProcess, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer.ui.state import ApplicationState


@dataclass(frozen=True, slots=True)
class LocalDataGroup:
    path: Path
    category: str
    file_count: int
    contents: str
    size_bytes: int
    modified_at: float


def scan_local_data_groups(root: Path) -> tuple[LocalDataGroup, ...]:
    grouped: dict[Path, list[Path]] = defaultdict(list)
    supported = {
        ".tif",
        ".tiff",
        ".nc",
        ".nc4",
        ".csv",
        ".hdf",
        ".h5",
        ".he5",
    }
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in supported:
                grouped[path.parent].append(path)

    results: list[LocalDataGroup] = []
    for folder, files in grouped.items():
        counts = Counter(path.suffix.casefold().removeprefix(".").upper() for path in files)
        relative = folder.relative_to(root)
        top_level = relative.parts[0] if relative.parts else folder.name
        category = (
            "处理结果"
            if top_level.casefold() in {"processed", "geotiff", "output", "outputs"}
            else "下载数据"
        )
        contents = "，".join(f"{count} 个 {kind}" for kind, count in sorted(counts.items()))
        stats = [path.stat() for path in files]
        results.append(
            LocalDataGroup(
                folder,
                category,
                len(files),
                contents,
                sum(item.st_size for item in stats),
                max(item.st_mtime for item in stats),
            )
        )
    results.sort(key=lambda item: (item.modified_at, str(item.path)), reverse=True)
    return tuple(results)


class LibraryPage(QWidget):
    def __init__(self, output_root: Path, state: ApplicationState | None = None) -> None:
        super().__init__()
        self.output_root = output_root
        self.state = state
        self.groups: tuple[LocalDataGroup, ...] = ()
        layout = QVBoxLayout(self)
        title = QLabel("本地数据")
        title.setObjectName("PageTitle")
        refresh = QPushButton("刷新列表")
        refresh.clicked.connect(self.refresh)
        open_folder = QPushButton("打开文件夹")
        open_folder.clicked.connect(self._open_folder)
        delete = QPushButton("删除数据组")
        delete.setObjectName("DangerButton")
        delete.clicked.connect(self._delete)
        toolbar = QHBoxLayout()
        toolbar.addWidget(refresh)
        toolbar.addWidget(open_folder)
        toolbar.addWidget(delete)
        toolbar.addStretch(1)
        self.status = QLabel()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ("数据组", "来源", "包含文件", "总大小", "最近更新")
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(lambda _index: self._open_folder())
        layout.addWidget(title)
        layout.addLayout(toolbar)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)
        self.refresh()

    def set_output_root(self, output_root: Path) -> None:
        self.output_root = output_root
        self.refresh()

    def refresh(self) -> None:
        self.groups = scan_local_data_groups(self.output_root)
        self.table.setRowCount(len(self.groups))
        total_files = 0
        for row, group in enumerate(self.groups):
            total_files += group.file_count
            try:
                relative = group.path.relative_to(self.output_root)
                display_name = str(relative)
            except ValueError:
                display_name = group.path.name
            values = (
                display_name,
                group.category,
                group.contents,
                _human_bytes(group.size_bytes),
                datetime.fromtimestamp(group.modified_at).strftime("%Y-%m-%d %H:%M"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(group.path))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, column, item)
        self.status.setText(f"共 {len(self.groups)} 个数据组，{total_files} 个数据文件")

    def _selected_group(self) -> LocalDataGroup | None:
        row = self.table.currentRow()
        return self.groups[row] if 0 <= row < len(self.groups) else None

    def _open_folder(self) -> None:
        group = self._selected_group()
        if group is None:
            self.status.setText("请先选择一个数据组")
            return
        QProcess.startDetached("explorer.exe", [str(group.path)])
        if self.state:
            self.state.message.emit(f"已打开数据目录: {group.path}")

    def _context_menu(self, position: QPoint) -> None:
        if self.table.itemAt(position) is None:
            return
        self.table.setCurrentItem(self.table.itemAt(position))
        menu = QMenu(self)
        open_action = menu.addAction("打开文件夹")
        delete_action = menu.addAction("删除数据组")
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == open_action:
            self._open_folder()
        elif selected == delete_action:
            self._delete()

    def _delete(self) -> None:
        group = self._selected_group()
        if group is None:
            self.status.setText("请先选择一个数据组")
            return
        root = self.output_root.resolve()
        resolved = group.path.resolve()
        if resolved == root or root not in resolved.parents:
            QMessageBox.critical(self, "删除数据组", "目录不在数据保存位置中，已拒绝删除。")
            return
        answer = QMessageBox.question(
            self,
            "删除数据组",
            f"确定永久删除此数据组及其中 {group.file_count} 个数据文件？\n{resolved}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            shutil.rmtree(resolved)
            self.status.setText(f"已删除数据组: {resolved.name}")
            if self.state:
                self.state.message.emit(f"已删除本地数据组: {resolved}")
            self.refresh()


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "未知"
