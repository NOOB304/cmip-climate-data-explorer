from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from cmip_explorer.domain.enums import ConfirmationScope
from cmip_explorer.domain.errors import ExplorerError
from cmip_explorer.domain.models import LogicalFile


class StrictSubsetFailureDialog(QDialog):
    DOWNLOAD_FULL = 2
    CANCEL_TASK = 0

    def __init__(
        self, file: LogicalFile, error: ExplorerError, free_bytes: int, parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("严格区域提取失败")
        self.setModal(True)
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        title = QLabel("远程服务未能返回目标研究区子集")
        title.setObjectName("PageTitle")
        summary = QLabel(
            "系统没有自动下载完整全球 NetCDF。请审阅失败详情，再决定是否为当前文件"
            "明确授权完整下载和本地裁剪。"
        )
        summary.setWordWrap(True)
        summary.setStyleSheet("color: #8b3329;")
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(
            "\n".join(
                [
                    f"文件: {file.filename}",
                    f"模型: {file.source_id or '-'}",
                    f"场景: {file.experiment_id or '-'}",
                    f"成员: {file.member_id or '-'}",
                    f"时间: {file.temporal.start or '-'} - {file.temporal.end or '-'}",
                    f"完整文件估计: {_human_bytes(file.size_bytes or 0)}",
                    f"空闲磁盘: {_human_bytes(free_bytes)}",
                    f"错误代码: {error.code.value}",
                    f"原因: {error.message}",
                    "",
                    "镜像尝试:",
                    json.dumps(error.details.get("attempts", []), ensure_ascii=False, indent=2),
                ]
            )
        )
        layout.addWidget(title)
        layout.addWidget(summary)
        layout.addWidget(details, 1)
        self.batch_scope = QCheckBox("允许当前批次后续同类失败文件完整下载")
        self.batch_scope.setChecked(False)
        layout.addWidget(self.batch_scope)
        buttons = QHBoxLayout()
        full_button = QPushButton("下载完整文件并继续")
        full_button.setObjectName("DangerButton")
        cancel_button = QPushButton("取消任务")
        copy_button = QPushButton("复制错误")
        full_button.clicked.connect(lambda: self.done(self.DOWNLOAD_FULL))
        cancel_button.clicked.connect(lambda: self.done(self.CANCEL_TASK))
        copy_button.clicked.connect(lambda: self._copy(details.toPlainText()))
        buttons.addWidget(copy_button)
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(full_button)
        layout.addLayout(buttons)

    def confirmation_scope(self) -> ConfirmationScope:
        return (
            ConfirmationScope.JOB_REMAINDER
            if self.batch_scope.isChecked()
            else ConfirmationScope.FILE
        )

    def _copy(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"
