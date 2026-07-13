from __future__ import annotations

import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
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
    data_changed = Signal()

    def __init__(self, output_root: Path, state: ApplicationState | None = None) -> None:
        super().__init__()
        self.output_root = output_root
        self.state = state
        self.groups: tuple[LocalDataGroup, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        title = QLabel("本地数据")
        title.setObjectName("PageTitle")
        subtitle = QLabel("按下载任务和处理目录汇总数据，双击即可在资源管理器中打开")
        subtitle.setObjectName("PageSubtitle")
        refresh = QPushButton("刷新列表")
        refresh.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh.clicked.connect(self.refresh)
        open_folder = QPushButton("打开文件夹")
        open_folder.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        open_folder.clicked.connect(self._open_folder)
        self.delete_button = QPushButton("删除所选")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete)
        toolbar = QHBoxLayout()
        toolbar.addWidget(refresh)
        toolbar.addWidget(open_folder)
        toolbar.addWidget(self.delete_button)
        toolbar.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("MutedText")
        metrics = QFrame()
        metrics.setObjectName("MetricStrip")
        metrics_layout = QHBoxLayout(metrics)
        metrics_layout.setContentsMargins(16, 10, 16, 10)
        self.group_count_value = _add_metric(metrics_layout, "数据组")
        self.file_count_value = _add_metric(metrics_layout, "文件总数")
        self.storage_value = _add_metric(metrics_layout, "占用空间")
        self.recent_value = _add_metric(metrics_layout, "最近更新")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("选择", "数据组", "来源", "包含文件", "总大小", "最近更新")
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(lambda _index: self._open_folder())
        self.table.itemSelectionChanged.connect(self._show_details)
        self.table.itemChanged.connect(self._checked_changed)
        details = QFrame()
        details.setObjectName("DetailsPanel")
        details.setMinimumWidth(265)
        details.setMaximumWidth(330)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(14, 12, 14, 12)
        details_title = QLabel("数据组详情")
        details_title.setObjectName("SectionTitle")
        self.details = QLabel("选择一个数据组查看完整路径和内容。")
        self.details.setObjectName("MutedText")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_open = QPushButton("打开文件夹")
        details_open.setObjectName("PrimaryButton")
        details_open.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        details_open.clicked.connect(self._open_folder)
        details_layout.addWidget(details_title)
        details_layout.addWidget(self.details)
        details_layout.addStretch(1)
        details_layout.addWidget(details_open)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.table)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(metrics)
        layout.addLayout(toolbar)
        layout.addWidget(self.status)
        layout.addWidget(splitter, 1)
        self.refresh()

    def set_output_root(self, output_root: Path) -> None:
        self.output_root = output_root
        self.refresh()

    def refresh(self) -> None:
        checked_paths = {
            Path(str(item.data(Qt.ItemDataRole.UserRole)))
            for row in range(self.table.rowCount())
            if (item := self.table.item(row, 0)) is not None
            and item.checkState() is Qt.CheckState.Checked
        }
        self.groups = scan_local_data_groups(self.output_root)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.groups))
        total_files = 0
        total_size = 0
        for row, group in enumerate(self.groups):
            total_files += group.file_count
            total_size += group.size_bytes
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
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked
                if group.path in checked_paths
                else Qt.CheckState.Unchecked
            )
            check_item.setData(Qt.ItemDataRole.UserRole, str(group.path))
            check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            check_item.setToolTip(f"勾选后可删除数据组\n{group.path}")
            self.table.setItem(row, 0, check_item)
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setToolTip(str(group.path))
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self.status.setText(f"共 {len(self.groups)} 个数据组，{total_files} 个数据文件")
        self.group_count_value.setText(str(len(self.groups)))
        self.file_count_value.setText(str(total_files))
        self.storage_value.setText(_human_bytes(total_size))
        self.recent_value.setText(
            datetime.fromtimestamp(self.groups[0].modified_at).strftime("%Y-%m-%d %H:%M")
            if self.groups
            else "暂无数据"
        )
        if self.groups and self.table.currentRow() < 0:
            self.table.selectRow(0)
        elif not self.groups:
            self.details.setText("当前存储目录中没有可识别的数据组。")
        self._checked_changed()

    def _selected_group(self) -> LocalDataGroup | None:
        row = self.table.currentRow()
        return self.groups[row] if 0 <= row < len(self.groups) else None

    def _show_details(self) -> None:
        group = self._selected_group()
        if group is None:
            self.details.setText("选择一个数据组查看完整路径和内容。")
            return
        self.details.setText(
            f"完整路径\n{group.path}\n\n"
            f"来源\n{group.category}\n\n"
            f"包含文件\n{group.contents}\n\n"
            f"总大小\n{_human_bytes(group.size_bytes)}\n\n"
            f"最近更新\n{datetime.fromtimestamp(group.modified_at):%Y-%m-%d %H:%M}"
        )

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
            group = self._selected_group()
            if group is not None:
                self._delete_groups((group,))

    def _checked_groups(self) -> tuple[LocalDataGroup, ...]:
        selected_paths = {
            Path(str(item.data(Qt.ItemDataRole.UserRole)))
            for row in range(self.table.rowCount())
            if (item := self.table.item(row, 0)) is not None
            and item.checkState() is Qt.CheckState.Checked
        }
        return tuple(group for group in self.groups if group.path in selected_paths)

    def _checked_changed(self) -> None:
        count = len(self._checked_groups())
        self.delete_button.setEnabled(count > 0)
        self.delete_button.setText(f"删除所选 ({count})" if count else "删除所选")

    def _delete(self) -> None:
        groups = self._checked_groups()
        if not groups:
            self.status.setText("请先勾选要删除的数据组")
            return
        self._delete_groups(groups)

    def _delete_groups(self, groups: tuple[LocalDataGroup, ...]) -> None:
        root = self.output_root.resolve()
        resolved_groups: list[tuple[LocalDataGroup, Path]] = []
        for group in groups:
            resolved = group.path.resolve()
            if resolved == root or root not in resolved.parents:
                QMessageBox.critical(
                    self,
                    "删除数据组",
                    f"目录不在数据保存位置中，已拒绝删除。\n{resolved}",
                )
                return
            resolved_groups.append((group, resolved))
        resolved_groups.sort(key=lambda item: len(item[1].parts))
        delete_paths: list[Path] = []
        for _group, resolved in resolved_groups:
            if not any(parent == resolved or parent in resolved.parents for parent in delete_paths):
                delete_paths.append(resolved)
        total_files = sum(group.file_count for group, _resolved in resolved_groups)
        preview = "\n".join(str(path) for path in delete_paths[:5])
        if len(delete_paths) > 5:
            preview += f"\n……另有 {len(delete_paths) - 5} 个目录"
        answer = QMessageBox.question(
            self,
            "删除数据组",
            f"确定永久删除 {len(delete_paths)} 个数据组？\n"
            f"列表中包含 {total_files} 个数据文件，目录内配套文件也会一并删除。\n\n"
            f"{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                for path in delete_paths:
                    shutil.rmtree(path)
            except OSError as exc:
                QMessageBox.critical(self, "删除数据组", f"删除未能完成。\n\n{exc}")
                self.refresh()
                return
            self.status.setText(f"已删除 {len(delete_paths)} 个数据组")
            if self.state:
                self.state.message.emit(
                    "已删除本地数据组: " + "；".join(str(path) for path in delete_paths)
                )
            self.refresh()
            self.data_changed.emit()


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "未知"


def _add_metric(layout: QHBoxLayout, title: str) -> QLabel:
    container = QWidget()
    metric_layout = QVBoxLayout(container)
    metric_layout.setContentsMargins(8, 0, 24, 0)
    metric_layout.setSpacing(1)
    label = QLabel(title)
    label.setObjectName("MetricLabel")
    value = QLabel("—")
    value.setObjectName("MetricValue")
    metric_layout.addWidget(label)
    metric_layout.addWidget(value)
    layout.addWidget(container)
    layout.addStretch(1)
    return value
