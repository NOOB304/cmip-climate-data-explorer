from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer import __version__
from cmip_explorer.application import WorkflowService
from cmip_explorer.infrastructure.update import (
    GitHubReleaseUpdater,
    ReleaseInfo,
)
from cmip_explorer.settings import AppSettings
from cmip_explorer.ui.async_runner import AsyncRunnable


class SettingsPage(QWidget):
    saved = Signal(str)

    def __init__(self, path: Path, workflow: WorkflowService) -> None:
        super().__init__()
        self.path = path
        self.workflow = workflow
        self.settings = AppSettings.load(path)
        self.pool = QThreadPool.globalInstance()
        self._workers: set[AsyncRunnable] = set()
        layout = QVBoxLayout(self)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        form = QFormLayout()
        self.download_concurrency = QSpinBox()
        self.download_concurrency.setRange(1, 8)
        self.download_concurrency.setValue(self.settings.download_concurrency)
        self.cache_quota = QSpinBox()
        self.cache_quota.setRange(1, 1000)
        self.cache_quota.setSuffix(" GiB")
        self.cache_quota.setValue(self.settings.cache_quota_gb)
        self.insecure_http = QCheckBox("兼容没有有效 HTTPS 证书的旧数据节点")
        self.insecure_http.setChecked(self.settings.allow_insecure_http)
        self.update_channel = QComboBox()
        self.update_channel.addItem("稳定版", "stable")
        self.update_channel.addItem("预览版", "preview")
        self.update_channel.setCurrentIndex(
            max(0, self.update_channel.findData(self.settings.update_channel))
        )
        self.storage_directory = QLineEdit(str(self.settings.storage_path))
        choose_storage = QPushButton("选择…")
        choose_storage.clicked.connect(self._choose_storage)
        storage_row = QWidget()
        storage_layout = QHBoxLayout(storage_row)
        storage_layout.setContentsMargins(0, 0, 0, 0)
        storage_layout.addWidget(self.storage_directory, 1)
        storage_layout.addWidget(choose_storage)
        self.auto_convert = QCheckBox("月/年数据下载后自动转 GeoTIFF（高频数据保留 NC）")
        self.auto_convert.setChecked(self.settings.auto_convert_to_tif)
        form.addRow("文件存储位置", storage_row)
        form.addRow("自动转换", self.auto_convert)
        form.addRow("下载并发", self.download_concurrency)
        form.addRow("缓存配额", self.cache_quota)
        form.addRow("节点兼容", self.insecure_http)
        form.addRow("更新通道", self.update_channel)
        self.update_button = QPushButton("检查更新")
        self.update_button.clicked.connect(self._check_for_update)
        update_row = QWidget()
        update_layout = QHBoxLayout(update_row)
        update_layout.setContentsMargins(0, 0, 0, 0)
        self.version_label = QLabel(f"当前版本 {__version__}")
        update_layout.addWidget(self.version_label)
        update_layout.addStretch(1)
        update_layout.addWidget(self.update_button)
        form.addRow("软件更新", update_row)
        self.update_status = QLabel("点击按钮从 GitHub Releases 检查最新版本。")
        self.update_status.setWordWrap(True)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 0)
        self.update_progress.hide()
        save = QPushButton("保存设置")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._save)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(self.update_status)
        layout.addWidget(self.update_progress)
        layout.addWidget(save, 0)
        layout.addStretch(1)

    def _choose_storage(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择气候数据存储位置", self.storage_directory.text()
        )
        if selected:
            self.storage_directory.setText(selected)

    def _save(self) -> None:
        self.settings = AppSettings(
            download_concurrency=self.download_concurrency.value(),
            cache_quota_gb=self.cache_quota.value(),
            allow_insecure_http=self.insecure_http.isChecked(),
            update_channel=str(self.update_channel.currentData()),
            storage_directory=self.storage_directory.text().strip(),
            auto_convert_to_tif=self.auto_convert.isChecked(),
        )
        self.settings.save(self.path)
        self.settings.storage_path.mkdir(parents=True, exist_ok=True)
        self.workflow.allow_insecure_http = self.settings.allow_insecure_http
        self.workflow.storage_root = self.settings.storage_path
        self.workflow.auto_convert_to_tif = self.settings.auto_convert_to_tif
        self.saved.emit("设置已保存")
        QMessageBox.information(self, "设置", "设置已保存。")

    def _check_for_update(self) -> None:
        self._set_update_busy("正在连接 GitHub 检查更新…")
        channel = str(self.update_channel.currentData())

        async def check() -> ReleaseInfo | None:
            updater = GitHubReleaseUpdater(channel=channel)
            try:
                return await updater.check()
            finally:
                await updater.close()

        worker = AsyncRunnable(check)
        self._start_update_worker(worker, self._update_checked)

    def _update_checked(self, release: ReleaseInfo | None) -> None:
        self._set_update_idle()
        if release is None:
            self.update_status.setText(f"当前 {__version__} 已是所选通道的最新版本。")
            QMessageBox.information(self, "软件更新", "当前已经是最新版本。")
            return
        size = (
            f"，安装包约 {release.installer.size_bytes / 1024**2:.1f} MiB"
            if release.installer.size_bytes is not None
            else ""
        )
        self.update_status.setText(f"发现新版本 {release.version}{size}")
        answer = QMessageBox.question(
            self,
            "发现新版本",
            f"当前版本: {__version__}\n最新版本: {release.version}{size}\n\n"
            "是否下载并安装？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_update(release)

    def _download_update(self, release: ReleaseInfo) -> None:
        self._set_update_busy(f"正在下载并校验 {release.version} 安装包…")
        channel = str(self.update_channel.currentData())

        async def download() -> Path:
            updater = GitHubReleaseUpdater(channel=channel)
            try:
                return await updater.download(
                    release, self.workflow.paths.cache / "updates"
                )
            finally:
                await updater.close()

        worker = AsyncRunnable(download)
        self._start_update_worker(worker, self._update_downloaded)

    def _update_downloaded(self, installer: Path) -> None:
        self._set_update_idle()
        self.update_status.setText(f"安装包校验通过: {installer.name}")
        QMessageBox.information(
            self,
            "准备安装更新",
            "安装包已经通过 SHA-256 校验。软件将关闭并启动安装程序，"
            "升级会沿用原安装目录和现有数据。",
        )
        result = QProcess.startDetached(
            str(installer), ["/SP-", "/CLOSEAPPLICATIONS"]
        )
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            QMessageBox.critical(self, "软件更新", "无法启动安装程序。")
            return
        QTimer.singleShot(500, QApplication.quit)

    def _start_update_worker(self, worker: AsyncRunnable, result_slot) -> None:
        self._workers.add(worker)
        worker.signals.result.connect(result_slot)
        worker.signals.error.connect(self._update_failed)
        worker.signals.finished.connect(lambda: self._finish_update_worker(worker))
        self.pool.start(worker)

    def _finish_update_worker(self, worker: AsyncRunnable) -> None:
        self._workers.discard(worker)

    def _update_failed(self, _trace: str, error: object) -> None:
        self._set_update_idle()
        self.update_status.setText(f"更新失败: {error}")
        QMessageBox.critical(self, "软件更新", f"无法完成更新操作。\n\n{error}")

    def _set_update_busy(self, message: str) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("处理中…")
        self.update_status.setText(message)
        self.update_progress.show()

    def _set_update_idle(self) -> None:
        self.update_button.setEnabled(True)
        self.update_button.setText("检查更新")
        self.update_progress.hide()
