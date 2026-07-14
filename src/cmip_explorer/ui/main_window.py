from __future__ import annotations

import json
import logging
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QThreadPool, QUrl
from PySide6.QtGui import QCloseEvent, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer import __version__
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
        sidebar.setFixedWidth(208)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 14)
        side_layout.setSpacing(0)
        brand_bar = QFrame()
        brand_bar.setObjectName("BrandBar")
        brand_bar.setFixedHeight(66)
        brand_layout = QHBoxLayout(brand_bar)
        brand_layout.setContentsMargins(16, 0, 12, 0)
        brand_icon = QLabel()
        brand_icon.setPixmap(load_app_icon().pixmap(28, 28))
        brand_icon.setStyleSheet("background: transparent;")
        brand = QLabel("CMIP Explorer")
        brand.setObjectName("Brand")
        brand.setToolTip(APP_DISPLAY_NAME)
        brand_layout.addWidget(brand_icon)
        brand_layout.addWidget(brand, 1)
        self.navigation = QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setSpacing(3)
        self.navigation.setIconSize(QSize(20, 20))
        side_layout.addWidget(brand_bar)
        side_layout.addWidget(self.navigation, 1)
        footer = QWidget()
        footer.setStyleSheet("background: transparent;")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(18, 8, 14, 0)
        footer_layout.setSpacing(2)
        connection = QHBoxLayout()
        connection.setSpacing(7)
        dot = QLabel("●")
        dot.setObjectName("ConnectionDot")
        connection_text = QLabel("工作区已就绪")
        connection_text.setObjectName("SidebarCaption")
        connection.addWidget(dot)
        connection.addWidget(connection_text)
        connection.addStretch(1)
        self.sidebar_version = QLabel(f"v{__version__}  |  Developed by Wei Heng")
        self.sidebar_version.setObjectName("SidebarVersion")
        footer_layout.addLayout(connection)
        footer_layout.addWidget(self.sidebar_version)
        side_layout.addWidget(footer)
        self.stack = QStackedWidget()
        catalog = VariableCatalog(self.paths.catalog)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self._log_records: list[dict[str, object]] = []
        self._detailed_logs = False
        settings_page = SettingsPage(self.paths.data / "settings.json", self.workflow)
        self.library_page = LibraryPage(self.workflow.storage_root, self.state)
        self.processing_page = ProcessingPage(self.state, self.workflow.storage_root)
        self.processing_page.processed.connect(self.library_page.refresh)
        self.library_page.data_changed.connect(self.processing_page.refresh_data)
        settings_page.saved.connect(self._settings_saved)
        pages = (
            ("数据下载", SearchPage(self.state, catalog, self.paths.data, self.workflow)),
            ("下载任务", TasksPage(self.repository, self.workflow, self.state)),
            ("文件处理", self.processing_page),
            ("本地数据", self.library_page),
            ("操作日志", self._log_page()),
            ("设置", settings_page),
        )
        icons = (
            QStyle.StandardPixmap.SP_DriveNetIcon,
            QStyle.StandardPixmap.SP_FileDialogListView,
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            QStyle.StandardPixmap.SP_DirOpenIcon,
            QStyle.StandardPixmap.SP_FileDialogInfoView,
            QStyle.StandardPixmap.SP_ComputerIcon,
        )
        for (name, page), icon in zip(pages, icons, strict=True):
            item = QListWidgetItem(self.style().standardIcon(icon), name)
            item.setToolTip(name)
            self.navigation.addItem(item)
            self.stack.addWidget(page)
        self.navigation.currentRowChanged.connect(self._page_changed)
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
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        title = QLabel("操作日志")
        title.setObjectName("PageTitle")
        subtitle = QLabel("默认显示下载、处理和删除等用户操作；需要排错时可切换详细日志")
        subtitle.setObjectName("PageSubtitle")
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.log_level = QComboBox()
        self.log_level.addItems(("全部级别", "信息", "警告", "错误"))
        self.log_level.currentIndexChanged.connect(self._render_logs)
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("搜索日志内容")
        self.log_search.setClearButtonEnabled(True)
        self.log_search.textChanged.connect(self._render_logs)
        self.log_mode_button = QPushButton("查看详细日志")
        self.log_mode_button.setCheckable(True)
        self.log_mode_button.toggled.connect(self._set_detailed_logs)
        refresh = QPushButton("刷新日志")
        refresh.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh.clicked.connect(self._refresh_logs)
        open_folder = QPushButton("打开日志目录")
        open_folder.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.logs)))
        )
        self.log_open_folder = open_folder
        toolbar.addWidget(self.log_level)
        toolbar.addWidget(self.log_search, 1)
        toolbar.addWidget(self.log_mode_button)
        toolbar.addWidget(refresh)
        toolbar.addWidget(open_folder)
        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(("时间", "级别", "模块", "事件"))
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.log_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log_table.verticalHeader().setDefaultSectionSize(34)
        header = self.log_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.log_table.itemSelectionChanged.connect(self._show_log_details)
        detail_frame = QFrame()
        detail_frame.setObjectName("DetailsPanel")
        self.log_detail_frame = detail_frame
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_title = QLabel("详细信息")
        detail_title.setObjectName("SectionTitle")
        self.logs.setMaximumHeight(220)
        detail_layout.addWidget(detail_title)
        detail_layout.addWidget(self.logs)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.log_table)
        splitter.addWidget(detail_frame)
        splitter.setStretchFactor(0, 1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(toolbar)
        layout.addWidget(splitter, 1)
        self.log_level.hide()
        self.log_open_folder.hide()
        self.log_detail_frame.hide()
        self._refresh_logs()
        return page

    def _refresh_logs(self) -> None:
        path = self.paths.logs / "app.jsonl"
        if not path.exists():
            self._log_records = []
            self._render_logs()
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-1000:]
            records: list[dict[str, object]] = []
            for line in lines:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self._log_records = list(reversed(records))
            self._render_logs()
        except (OSError, json.JSONDecodeError) as exc:
            self.logs.setPlainText(f"日志读取失败: {exc}")

    def _render_logs(self) -> None:
        if not hasattr(self, "log_table"):
            return
        level_filter = self.log_level.currentText()
        level_map = {"信息": "INFO", "警告": "WARNING", "错误": "ERROR"}
        keyword = self.log_search.text().strip().casefold()
        records = []
        for record in self._log_records:
            level = str(record.get("level", "INFO")).upper()
            message = str(record.get("message", ""))
            module = str(record.get("logger", record.get("module", "应用")))
            if self._detailed_logs:
                if level_filter != "全部级别" and level != level_map[level_filter]:
                    continue
            elif not _is_operation_record(record):
                continue
            if keyword and keyword not in f"{message} {module}".casefold():
                continue
            records.append(record)
        if self._detailed_logs:
            self.log_table.setColumnCount(4)
            self.log_table.setHorizontalHeaderLabels(("时间", "级别", "模块", "事件"))
            header = self.log_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        else:
            self.log_table.setColumnCount(3)
            self.log_table.setHorizontalHeaderLabels(("时间", "操作", "内容"))
            header = self.log_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.log_table.setRowCount(len(records))
        colors = {
            "WARNING": QColor("#b36a00"),
            "ERROR": QColor("#b84439"),
            "CRITICAL": QColor("#b84439"),
            "INFO": QColor("#1b7376"),
        }
        for row, record in enumerate(records):
            level = str(record.get("level", "INFO")).upper()
            message = str(record.get("message", ""))
            timestamp = _friendly_log_time(record.get("timestamp"))
            if self._detailed_logs:
                values = (
                    timestamp,
                    {"INFO": "信息", "WARNING": "警告", "ERROR": "错误"}.get(
                        level, level
                    ),
                    str(record.get("logger", record.get("module", "应用"))),
                    message,
                )
            else:
                values = (timestamp, _operation_category(message), message)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record)
                if self._detailed_logs and column == 1:
                    item.setForeground(colors.get(level, QColor("#4f6067")))
                self.log_table.setItem(row, column, item)
        if records:
            self.log_table.selectRow(0)
        else:
            self.logs.setPlainText("没有符合当前筛选条件的记录。")

    def _set_detailed_logs(self, enabled: bool) -> None:
        self._detailed_logs = enabled
        self.log_mode_button.setText("返回操作记录" if enabled else "查看详细日志")
        self.log_level.setVisible(enabled)
        self.log_open_folder.setVisible(enabled)
        self.log_detail_frame.setVisible(enabled)
        self._render_logs()

    def _show_log_details(self) -> None:
        if not self._detailed_logs:
            return
        row = self.log_table.currentRow()
        if row < 0 or self.log_table.item(row, 0) is None:
            return
        record = self.log_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.logs.setPlainText(json.dumps(record, ensure_ascii=False, indent=2))

    def _message(self, message: str) -> None:
        logging.getLogger("cmip_explorer.operations").info(message)
        self.statusBar().showMessage(message, 6000)
        self.logs.appendPlainText(message)

    def _page_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index == 2:
            self.processing_page.refresh_data()
        elif index == 3:
            self.library_page.refresh()
        elif index == 4:
            self._refresh_logs()

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


def _is_operation_record(record: dict[str, object]) -> bool:
    logger = str(record.get("logger", record.get("module", "")))
    level = str(record.get("level", "INFO")).upper()
    return level == "INFO" and logger in {
        "cmip_explorer.operations",
        "cmip_explorer.ui",
    }


def _friendly_log_time(value: object) -> str:
    text = str(value or "-")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return text


def _operation_category(message: str) -> str:
    categories = (
        (("查询", "检索"), "数据查询"),
        (("下载", "任务", "暂停", "恢复", "取消"), "数据下载"),
        (("处理", "TIF", "转换", "裁剪", "重采样"), "文件处理"),
        (("删除", "清理", "清空"), "数据管理"),
        (("设置", "保存位置", "更新"), "软件设置"),
        (("导出",), "导出列表"),
        (("打开",), "打开目录"),
    )
    return next(
        (label for keywords, label in categories if any(word in message for word in keywords)),
        "其他操作",
    )
