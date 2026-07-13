from __future__ import annotations

import json
import logging

from PySide6.QtCore import QSize, QThreadPool, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer.application import WorkflowService
from cmip_explorer.config import APP_DISPLAY_NAME, AppPaths
from cmip_explorer.infrastructure.catalog import VariableCatalog
from cmip_explorer.infrastructure.persistence import TaskRepository
from cmip_explorer.ui.fonts import install_ui_font, load_app_icon
from cmip_explorer.ui.pages import (
    LibraryPage,
    ProcessingPage,
    SearchPage,
    SettingsPage,
    TasksPage,
)
from cmip_explorer.ui.state import ApplicationState
from cmip_explorer.ui.style import APP_STYLESHEET


class MainWindow(QMainWindow):
    def __init__(
        self, paths: AppPaths, repository: TaskRepository, workflow: WorkflowService
    ) -> None:
        super().__init__()
        self.paths = paths
        self.repository = repository
        self.workflow = workflow
        self.state = ApplicationState()
        install_ui_font()
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(QSize(1180, 760))
        self.resize(1440, 900)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(205)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 12)
        brand = QLabel("CMIP Climate\nExplorer")
        brand.setObjectName("Brand")
        self.navigation = QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setSpacing(2)
        side_layout.addWidget(brand)
        side_layout.addWidget(self.navigation, 1)
        self.stack = QStackedWidget()
        catalog = VariableCatalog(self.paths.catalog)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        settings_page = SettingsPage(self.paths.data / "settings.json", self.workflow)
        self.library_page = LibraryPage(self.workflow.storage_root, self.state)
        self.processing_page = ProcessingPage(self.state, self.workflow.storage_root)
        self.processing_page.processed.connect(self.library_page.refresh)
        settings_page.saved.connect(self._settings_saved)
        pages = (
            ("数据下载", SearchPage(self.state, catalog, self.paths.data, self.workflow)),
            ("下载任务", TasksPage(self.repository, self.workflow, self.state)),
            ("文件处理", self.processing_page),
            ("本地数据", self.library_page),
            ("操作日志", self._log_page()),
            ("设置", settings_page),
        )
        for name, page in pages:
            self.navigation.addItem(QListWidgetItem(name))
            self.stack.addWidget(page)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.state.message.connect(self._message)
        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        status = QStatusBar()
        status.showMessage(f"数据保存位置: {self.workflow.storage_root}")
        self.setStatusBar(status)

    def _log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("日志")
        title.setObjectName("PageTitle")
        toolbar = QHBoxLayout()
        refresh = QPushButton("刷新日志")
        refresh.clicked.connect(self._refresh_logs)
        open_folder = QPushButton("打开日志目录")
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.logs)))
        )
        toolbar.addWidget(refresh)
        toolbar.addWidget(open_folder)
        toolbar.addStretch(1)
        layout.addWidget(title)
        layout.addLayout(toolbar)
        layout.addWidget(self.logs, 1)
        self._refresh_logs()
        return page

    def _refresh_logs(self) -> None:
        path = self.paths.logs / "app.jsonl"
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-1000:]
            formatted = []
            for line in lines:
                record = json.loads(line)
                formatted.append(
                    f"{record.get('timestamp', '-')}  {record.get('level', '-'):8}  "
                    f"{record.get('message', '')}"
                )
            self.logs.setPlainText("\n".join(formatted))
        except (OSError, json.JSONDecodeError) as exc:
            self.logs.setPlainText(f"日志读取失败: {exc}")

    def _message(self, message: str) -> None:
        logging.getLogger("cmip_explorer.ui").info(message)
        self.statusBar().showMessage(message, 6000)
        self.logs.appendPlainText(message)

    def _settings_saved(self, message: str) -> None:
        self.library_page.set_output_root(self.workflow.storage_root)
        self.processing_page.set_storage_root(self.workflow.storage_root)
        self._message(f"{message} · 数据保存位置: {self.workflow.storage_root}")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.workflow.request_shutdown()
        pool = QThreadPool.globalInstance()
        pool.clear()
        if not pool.waitForDone(15_000):
            logging.getLogger("cmip_explorer.ui").warning(
                "background work did not stop within the shutdown grace period"
            )
        super().closeEvent(event)
