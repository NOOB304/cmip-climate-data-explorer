from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QPoint, QProcess, Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer.application import CleanupResult, WorkflowService
from cmip_explorer.domain.enums import TaskStatus
from cmip_explorer.infrastructure.persistence import TaskRepository
from cmip_explorer.ui.async_runner import AsyncRunnable
from cmip_explorer.ui.state import ApplicationState


class TasksPage(QWidget):
    def __init__(
        self,
        repository: TaskRepository,
        workflow: WorkflowService,
        state: ApplicationState | None = None,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.workflow = workflow
        self.state = state
        self.pool = QThreadPool.globalInstance()
        self._workers: set[AsyncRunnable] = set()
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("下载任务")
        title.setObjectName("PageTitle")
        toolbar = QHBoxLayout()
        for label, action in (
            ("刷新", self.refresh),
            ("暂停", lambda: self._control("pause")),
            ("恢复", lambda: self._control("resume")),
            ("重新连接", self._retry_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        cancel = QPushButton("取消")
        cancel.setObjectName("DangerButton")
        cancel.clicked.connect(lambda: self._control("cancel"))
        toolbar.addWidget(cancel)
        cancel_all = QPushButton("全部停止")
        cancel_all.setObjectName("DangerButton")
        cancel_all.clicked.connect(self._cancel_all)
        toolbar.addWidget(cancel_all)
        self.cleanup_button = QPushButton("清理已停止任务")
        self.cleanup_button.setObjectName("DangerButton")
        self.cleanup_button.clicked.connect(self._clear_stopped)
        toolbar.addWidget(self.cleanup_button)
        toolbar.addStretch(1)

        self.activity = QLabel("当前没有运行中的任务")
        self.activity.setObjectName("ActivityBanner")
        self.message = QLabel()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("状态", "下载进度", "文件", "大小", "保存目录"))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 260)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemSelectionChanged.connect(self._show_selected_details)
        self.details = QLabel("选中任务后在这里显示完整文件名和保存位置")
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setWordWrap(True)

        layout.addWidget(title)
        layout.addLayout(toolbar)
        layout.addWidget(self.activity)
        layout.addWidget(self.message)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.details)

    def _control(self, action: str) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.message.setText("请先选择任务")
            return
        task_id = self._task_id(row)
        operation = {
            "pause": self.workflow.pause_task,
            "resume": self.workflow.resume_task,
            "cancel": self.workflow.cancel_task,
        }[action]
        changed = operation(task_id)
        if (
            not changed
            and action == "resume"
            and self.repository.status(task_id)
            in {
                TaskStatus.INTERRUPTED,
                TaskStatus.FAILED,
            }
        ):
            self._retry_task(task_id)
            return
        self.message.setText("任务状态已更新" if changed else "当前任务不支持此操作")
        if changed and self.state:
            labels = {"pause": "暂停", "resume": "恢复", "cancel": "取消"}
            self.state.message.emit(f"{labels[action]}下载任务: {task_id}")
        self.refresh()

    def _retry_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.message.setText("请先选择失败或意外中断的任务")
            return
        self._retry_task(self._task_id(row))

    def _cancel_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "停止全部下载",
            "确定停止所有正在下载和等待中的任务？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = self.workflow.cancel_all_tasks()
        self.message.setText(f"已停止 {count} 个任务")
        if self.state:
            self.state.message.emit(f"已停止全部下载任务，共 {count} 个")
        self.refresh()

    def _clear_stopped(self) -> None:
        stopped_statuses = {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELED.value,
            TaskStatus.INTERRUPTED.value,
        }
        count = sum(
            task.status in stopped_statuses for task in self.repository.list_tasks()
        )
        if count == 0:
            self.message.setText("没有可以清理的已停止任务")
            return
        answer = QMessageBox.warning(
            self,
            "清理已停止任务",
            f"将从列表移除 {count} 个已完成、失败、取消或意外中断的任务。\n\n"
            "已完成的数据文件会保留，可在“本地数据”中查看。\n"
            "失败、取消和中断任务的 NC、断点文件及未完成 TIF 将永久删除。\n"
            "正在下载、等待和暂停的任务不受影响。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.cleanup_button.setEnabled(False)
        self.message.setText("正在清理任务记录和残余文件…")

        async def cleanup() -> CleanupResult:
            return await asyncio.to_thread(self.workflow.clear_stopped_tasks)

        worker = AsyncRunnable(cleanup)
        self._workers.add(worker)
        worker.signals.result.connect(self._cleanup_finished)
        worker.signals.error.connect(
            lambda _trace, error: self.message.setText(f"清理失败: {error}")
        )
        worker.signals.finished.connect(lambda: self._cleanup_worker_finished(worker))
        self.pool.start(worker)

    def _cleanup_finished(self, result: CleanupResult) -> None:
        self.message.setText(
            f"已清理 {result.removed_tasks} 个任务、{result.removed_files} 个残余文件，"
            f"释放 {_human_bytes(result.freed_bytes)}"
        )
        if self.state:
            self.state.message.emit(self.message.text())
        self.refresh()

    def _cleanup_worker_finished(self, worker: AsyncRunnable) -> None:
        self._workers.discard(worker)
        self.cleanup_button.setEnabled(True)

    def _retry_task(self, task_id: UUID) -> None:
        self.message.setText("正在重新连接，将从已有进度继续")

        async def recover():
            return await self.workflow.retry_task(task_id)

        worker = AsyncRunnable(recover)
        self._workers.add(worker)
        worker.signals.result.connect(lambda path: self.message.setText(f"任务完成: {path}"))
        worker.signals.error.connect(
            lambda _trace, error: self.message.setText(f"重新连接失败: {error}")
        )
        worker.signals.finished.connect(lambda: self._recovery_finished(worker))
        self.pool.start(worker)

    def _recovery_finished(self, worker: AsyncRunnable) -> None:
        self._workers.discard(worker)
        self.refresh()

    def refresh(self) -> None:
        tasks = self.repository.list_tasks()
        selected_id = None
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0):
            selected_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            target = Path(task.target_path)
            values = (
                _status_label(task.status),
                "",
                target.name,
                _human_bytes(task.expected_size),
                str(target.parent),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, task.task_id)
                    item.setData(Qt.ItemDataRole.UserRole + 1, task.target_path)
                self.table.setItem(row, column, item)
            self.table.setCellWidget(row, 1, _progress_bar(task))
            if selected_id == task.task_id:
                self.table.selectRow(row)
        active_states = {
            TaskStatus.RESOLVING.value,
            TaskStatus.PROBING.value,
            TaskStatus.DOWNLOADING.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.PROCESSING.value,
            TaskStatus.RETRY_WAIT.value,
        }
        active = sum(task.status in active_states for task in tasks)
        self.activity.setText(
            f"正在运行 {active} 个任务，请保持程序开启" if active else "当前没有运行中的任务"
        )

    def _task_id(self, row: int) -> UUID:
        return UUID(str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)))

    def _selected_path(self) -> Path | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        value = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)
        return Path(str(value)) if value else None

    def _show_selected_details(self) -> None:
        path = self._selected_path()
        if path is None:
            self.details.setText("选中任务后在这里显示完整文件名和保存位置")
        else:
            self.details.setText(f"完整文件名：{path.name}\n保存位置：{path.parent}")

    def _open_in_explorer(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        if path.exists():
            QProcess.startDetached("explorer.exe", ["/select,", str(path)])
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            QProcess.startDetached("explorer.exe", [str(path.parent)])

    def _context_menu(self, position: QPoint) -> None:
        item = self.table.itemAt(position)
        if item is None:
            return
        self.table.selectRow(item.row())
        menu = QMenu(self)
        open_action = menu.addAction("在资源管理器中显示")
        retry_action = menu.addAction("重新连接")
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == open_action:
            self._open_in_explorer()
        elif selected == retry_action:
            self._retry_selected()


def _progress_bar(task) -> QProgressBar:
    bar = QProgressBar()
    total = task.expected_size or 0
    if total > 0:
        # QProgressBar only accepts signed 32-bit integers. Climate files can
        # exceed that limit, so keep bytes in the label and use a fixed scale.
        scale = 1000
        downloaded = max(0, min(task.progress_bytes, total))
        progress = min(scale, int(downloaded * scale / total))
        percentage = downloaded / total * 100
        bar.setRange(0, scale)
        bar.setValue(progress)
        bar.setFormat(
            f"{_human_bytes(downloaded)} / {_human_bytes(total)}  {percentage:.0f}%"
        )
    elif task.status in {
        TaskStatus.RESOLVING.value,
        TaskStatus.PROBING.value,
        TaskStatus.DOWNLOADING.value,
        TaskStatus.VERIFYING.value,
        TaskStatus.PROCESSING.value,
        TaskStatus.RETRY_WAIT.value,
    }:
        bar.setRange(0, 0)
        bar.setFormat("正在连接…")
    else:
        bar.setRange(0, 100)
        bar.setValue(100 if task.status == TaskStatus.COMPLETED.value else 0)
    return bar


def _status_label(value: str) -> str:
    return {
        "queued": "等待中",
        "resolving": "解析地址",
        "probing": "连接节点",
        "downloading": "正在下载",
        "paused": "已暂停",
        "verifying": "正在校验",
        "processing": "正在转换 TIF",
        "retry_wait": "自动重连",
        "interrupted": "意外中断",
        "completed": "已完成",
        "failed": "失败",
        "canceled": "已取消",
    }.get(value, value)


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "未知"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "未知"
