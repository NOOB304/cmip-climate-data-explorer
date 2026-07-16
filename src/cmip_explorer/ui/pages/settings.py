from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStyle,
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
    update_bytes_changed = Signal(object, object)
    update_retry_changed = Signal(object, object, object)

    def __init__(self, path: Path, workflow: WorkflowService) -> None:
        super().__init__()
        self.path = path
        self.workflow = workflow
        self.settings = AppSettings.load(path)
        self.pool = QThreadPool.globalInstance()
        self._workers: set[AsyncRunnable] = set()
        self.update_bytes_changed.connect(
            self._show_update_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        self.update_retry_changed.connect(
            self._show_update_retry,
            Qt.ConnectionType.QueuedConnection,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        subtitle = QLabel("管理数据保存、下载性能和软件更新。")
        subtitle.setObjectName("PageSubtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        storage_section, storage_form = self._create_section(
            "存储与处理",
            "设置下载文件和处理结果的保存位置。修改后不会移动已有数据。",
        )
        self.storage_directory = QLineEdit(str(self.settings.storage_path))
        self.storage_directory.setPlaceholderText("请选择数据存储目录")
        choose_storage = QPushButton("选择目录")
        choose_storage.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        choose_storage.clicked.connect(self._choose_storage)
        storage_row = QWidget()
        storage_layout = QHBoxLayout(storage_row)
        storage_layout.setContentsMargins(0, 0, 0, 0)
        storage_layout.setSpacing(8)
        storage_layout.addWidget(self.storage_directory, 1)
        storage_layout.addWidget(choose_storage)
        self.auto_convert = QCheckBox("下载完成后自动生成 GeoTIFF")
        self.auto_convert.setObjectName("Switch")
        self.auto_convert.setChecked(self.settings.auto_convert_to_tif)
        storage_form.addRow(
            self._setting_label("文件存储位置", "优先建议使用空间充足的非系统盘"),
            storage_row,
        )
        storage_form.addRow(
            self._setting_label("自动转换", "月、年数据自动转换，高频数据保留 NC"),
            self.auto_convert,
        )

        download_section, download_form = self._create_section(
            "下载与网络",
            "控制同时下载数量、缓存占用和旧节点兼容方式。",
        )
        self.download_concurrency = QSpinBox()
        self.download_concurrency.setRange(-1_000_000, 1_000_000)
        self.download_concurrency.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.download_concurrency.setValue(self.settings.download_concurrency)
        self.download_concurrency.editingFinished.connect(self._validate_download_concurrency)
        self.cache_quota = QDoubleSpinBox()
        self.cache_quota.setRange(-1_000_000.0, 1_000_000.0)
        self.cache_quota.setDecimals(1)
        self.cache_quota.setSingleStep(0.1)
        self.cache_quota.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.cache_quota.setSuffix(" GB")
        self.cache_quota.setValue(self.settings.cache_quota_gb)
        self.cache_quota.editingFinished.connect(self._validate_cache_quota)
        self.insecure_http = QCheckBox("允许连接旧式 HTTP 数据节点")
        self.insecure_http.setObjectName("Switch")
        self.insecure_http.setChecked(self.settings.allow_insecure_http)
        download_form.addRow(
            self._setting_label("同时下载任务", "网络不稳定时建议保持 1 至 2 个"),
            self.download_concurrency,
        )
        download_form.addRow(
            self._setting_label("缓存空间上限", "下载中间文件和更新包共用此空间"),
            self.cache_quota,
        )
        download_form.addRow(
            self._setting_label("节点兼容模式", "仅在 HTTPS 节点无法访问时启用"),
            self.insecure_http,
        )

        update_section, update_form = self._create_section(
            "软件更新",
            "从 GitHub Releases 获取经过校验的安装包，更新后会自动重新打开软件。",
        )
        self.update_channel = QComboBox()
        self.update_channel.addItem("稳定版", "stable")
        self.update_channel.addItem("预览版", "preview")
        self.update_channel.setCurrentIndex(
            max(0, self.update_channel.findData(self.settings.update_channel))
        )
        self.update_button = QPushButton("检查更新")
        self.update_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.update_button.clicked.connect(self._check_for_update)
        update_row = QWidget()
        update_layout = QHBoxLayout(update_row)
        update_layout.setContentsMargins(0, 0, 0, 0)
        update_layout.setSpacing(10)
        self.version_label = QLabel(f"当前版本 {__version__}")
        self.version_label.setObjectName("MutedText")
        update_layout.addWidget(self.version_label)
        update_layout.addStretch(1)
        update_layout.addWidget(self.update_button)
        self.update_status = QLabel("点击按钮从 GitHub Releases 检查最新版本。")
        self.update_status.setObjectName("MutedText")
        self.update_status.setWordWrap(True)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 0)
        self.update_progress.hide()

        update_form.addRow(self._setting_label("更新通道"), self.update_channel)
        update_form.addRow(self._setting_label("当前状态"), update_row)
        update_form.addRow(self._setting_label("更新说明"), self.update_status)
        update_form.addRow(QLabel(), self.update_progress)

        save = QPushButton("保存设置")
        save.setObjectName("PrimaryButton")
        save.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        save.clicked.connect(self._save)

        layout.addWidget(storage_section)
        layout.addWidget(download_section)
        layout.addWidget(update_section)
        layout.addStretch(1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(save)
        layout.addLayout(actions)

    @staticmethod
    def _setting_label(title: str, helper: str = "") -> QWidget:
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if helper:
            label.setToolTip(helper)
        return label

    @staticmethod
    def _create_section(title: str, description: str) -> tuple[QFrame, QFormLayout]:
        section = QFrame()
        section.setObjectName("SettingsSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(4, 10, 4, 14)
        section_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        description_label = QLabel(description)
        description_label.setObjectName("SectionDescription")
        description_label.setWordWrap(True)
        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 0)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        section_layout.addWidget(title_label)
        section_layout.addWidget(description_label)
        section_layout.addLayout(form)
        return section, form

    def _choose_storage(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择气候数据存储位置", self.storage_directory.text()
        )
        if selected:
            self.storage_directory.setText(selected)

    def _validate_download_concurrency(self) -> None:
        self.download_concurrency.interpretText()
        value = self.download_concurrency.value()
        corrected = min(20, max(1, value))
        if corrected == value:
            return
        self.download_concurrency.setValue(corrected)
        QMessageBox.warning(
            self,
            "输入超出范围",
            f"同时下载任务应在 1 至 20 之间，已自动设置为 {corrected}。",
        )

    def _validate_cache_quota(self) -> None:
        self.cache_quota.interpretText()
        value = self.cache_quota.value()
        corrected = min(2048.0, max(0.1, value))
        if corrected == value:
            return
        self.cache_quota.setValue(corrected)
        QMessageBox.warning(
            self,
            "输入超出范围",
            f"缓存空间上限应在 0.1 至 2048 GB 之间，已自动设置为 {corrected:g} GB。",
        )

    def _save(self) -> None:
        self._validate_download_concurrency()
        self._validate_cache_quota()
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
        self.workflow.download_concurrency = self.settings.download_concurrency
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
            "是否下载更新？更新完成后软件会自动重启。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_update(release)

    def _download_update(self, release: ReleaseInfo) -> None:
        self._set_update_busy(
            f"正在下载并校验 {release.version} 更新包…",
            show_progress=True,
        )
        self._show_update_progress(0, release.installer.size_bytes)
        channel = str(self.update_channel.currentData())

        async def download() -> Path:
            updater = GitHubReleaseUpdater(channel=channel)
            try:
                return await updater.download(
                    release,
                    self.workflow.paths.cache / "updates",
                    progress=lambda written, total: self.update_bytes_changed.emit(
                        written, total
                    ),
                    reconnect=lambda attempt, maximum, delay, _error: (
                        self.update_retry_changed.emit(attempt, maximum, delay)
                    ),
                )
            finally:
                await updater.close()

        worker = AsyncRunnable(download)
        self._start_update_worker(worker, self._update_downloaded)

    def _update_downloaded(self, installer: Path) -> None:
        self._set_update_busy("更新包校验通过，正在静默安装并重启软件…")
        try:
            install_directory = _current_install_directory()
            launcher = self._write_update_launcher(installer)
        except OSError as exc:
            self._set_update_idle()
            QMessageBox.critical(self, "软件更新", f"无法创建后台更新程序。\n\n{exc}")
            return
        result = QProcess.startDetached(
            "powershell.exe",
            [
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(launcher),
                "-ParentPid",
                str(os.getpid()),
                "-Installer",
                str(installer),
                "-InstallDir",
                str(install_directory),
                "-LogPath",
                str(installer.parent / "update-install.log"),
            ],
        )
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            self._set_update_idle()
            QMessageBox.critical(self, "软件更新", "无法启动后台更新程序。")
            return
        QTimer.singleShot(100, QApplication.quit)

    @staticmethod
    def _write_update_launcher(installer: Path) -> Path:
        launcher = installer.parent / "apply-update.ps1"
        launcher.write_text(
            """param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$LogPath
)
$ErrorActionPreference = 'Stop'
try {
    $InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
    $installedExecutable = Join-Path $InstallDir 'CMIPClimateExplorer.exe'
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
        throw "当前安装目录无效: $InstallDir"
    }
    ("install_directory=" + $InstallDir) | Add-Content -LiteralPath $LogPath
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    $installerArguments = @(
        '/SP-',
        '/VERYSILENT',
        '/SUPPRESSMSGBOXES',
        '/NORESTART',
        '/CLOSEAPPLICATIONS',
        '/FORCECLOSEAPPLICATIONS',
        '/UPDATE=1',
        '/STAGEDUPDATE=1',
        '/DEFERRELAUNCH=1',
        ('/DIR="' + $InstallDir + '"'),
        ('/TARGETDIR="' + $InstallDir + '"'),
        ('/LOG="' + $LogPath + '"')
    )
    $process = Start-Process -FilePath $Installer -ArgumentList $installerArguments `
        -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "安装程序退出码: $($process.ExitCode)"
    }
    Start-Process -FilePath $installedExecutable -WorkingDirectory $InstallDir
    exit 0
} catch {
    ($_ | Out-String) | Add-Content -LiteralPath $LogPath
    exit 1
} finally {
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
""",
            encoding="utf-8",
        )
        return launcher

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
        self.update_status.setText("更新未完成，已保留下载进度，可再次重试。")
        QMessageBox.critical(
            self,
            "软件更新",
            "自动重连仍未能完成更新。已保留下载进度，再次点击检查更新会从断点继续。"
            f"\n\n{error}",
        )

    def _show_update_retry(self, attempt: object, maximum: object, delay: object) -> None:
        wait_seconds = float(delay)
        wait_text = f"{wait_seconds:g}"
        self.update_status.setText(
            f"网络连接中断，{wait_text} 秒后自动重连"
            f"（第 {int(attempt)}/{int(maximum)} 次）…"
        )

    def _show_update_progress(self, written: object, total: object) -> None:
        downloaded = max(0, int(written or 0))
        expected = int(total) if total is not None else 0
        if downloaded and self.update_progress.isVisible():
            self.update_status.setText("正在下载更新包；网络中断时会自动重连并继续。")
        self.update_progress.setRange(0, 1000)
        if expected > 0:
            ratio = min(1.0, downloaded / expected)
            self.update_progress.setValue(round(ratio * 1000))
            self.update_progress.setFormat(
                f"{_human_bytes(downloaded)} / {_human_bytes(expected)} "
                f"({ratio * 100:.1f}%)"
            )
        elif downloaded:
            self.update_progress.setValue(0)
            self.update_progress.setFormat(f"已下载 {_human_bytes(downloaded)}")
        else:
            self.update_progress.setValue(0)
            self.update_progress.setFormat("正在获取安装包大小…")

    def _set_update_busy(self, message: str, *, show_progress: bool = False) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("处理中…")
        self.update_status.setText(message)
        self.update_progress.setVisible(show_progress)

    def _set_update_idle(self) -> None:
        self.update_button.setEnabled(True)
        self.update_button.setText("检查更新")
        self.update_progress.hide()


def _current_install_directory() -> Path:
    if not getattr(sys, "frozen", False):
        raise OSError("当前运行的不是已安装版本，不能执行自动更新")
    executable = Path(sys.executable).resolve()
    if executable.name.casefold() != "cmipclimateexplorer.exe":
        raise OSError(f"无法识别当前程序位置: {executable}")
    return executable.parent


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"
