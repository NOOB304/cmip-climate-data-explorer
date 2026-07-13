from __future__ import annotations

import csv
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer.application import WorkflowService
from cmip_explorer.domain.enums import TaskStatus
from cmip_explorer.domain.models import (
    FacetConstraint,
    LogicalFile,
    SearchRequest,
    VariableDefinition,
)
from cmip_explorer.domain.models import SearchPage as ResultPage
from cmip_explorer.infrastructure.catalog import VariableCatalog, VariableOption
from cmip_explorer.infrastructure.download import DownloadCancelled
from cmip_explorer.infrastructure.providers import (
    PROVIDERS,
    ProviderVariable,
    discover_provider_variables,
    filter_provider_variables,
    provider_definition,
)
from cmip_explorer.infrastructure.search import MultiBackendSearchService, default_registry
from cmip_explorer.ui.async_runner import AsyncRunnable
from cmip_explorer.ui.state import ApplicationState
from cmip_explorer.ui.variable_labels import friendly_variable_name


class SearchPage(QWidget):
    columns = (
        "选择",
        "变量名称",
        "变量代码",
        "模型",
        "分辨率",
        "情景",
        "频率",
        "数据表",
        "覆盖时间",
        "文件数",
        "文件大小",
        "访问",
        "文件名",
    )
    facet_names = ("source_id", "experiment_id", "table_id", "frequency", "grid_label")
    common_scenarios = (
        "historical",
        "ssp119",
        "ssp126",
        "ssp245",
        "ssp370",
        "ssp434",
        "ssp460",
        "ssp534-over",
        "ssp585",
        "piControl",
    )

    def __init__(
        self,
        state: ApplicationState,
        catalog: VariableCatalog,
        data_dir: Path,
        workflow: WorkflowService,
    ) -> None:
        super().__init__()
        self.state = state
        self.catalog = catalog
        self.data_dir = data_dir
        self.workflow = workflow
        self.files: list[LogicalFile] = []
        self.pool = QThreadPool.globalInstance()
        self._workers: set[AsyncRunnable] = set()
        self._search_worker: AsyncRunnable | None = None
        self._search_sequence = 0
        self._last_request: SearchRequest | None = None
        self._next_cursors: dict[str, str | int | None] = {}
        self._page_number = 1
        self._facet_sequence = 0
        self._variable_sequence = 0
        self._provider_id = "esgf"
        self._provider_variables: tuple[ProviderVariable, ...] = ()
        self._populating = False
        self._build_ui()

        self._variable_timer = QTimer(self)
        self._variable_timer.setSingleShot(True)
        self._variable_timer.setInterval(180)
        self._variable_timer.timeout.connect(self._update_variable_matches)
        self.variable_search.textChanged.connect(lambda: self._variable_timer.start())
        self._facet_timer = QTimer(self)
        self._facet_timer.setSingleShot(True)
        self._facet_timer.setInterval(450)
        self._facet_timer.timeout.connect(self.refresh_facets)
        self.variable_match.currentIndexChanged.connect(self._variable_changed)
        for combo in (self.source, self.experiment, self.table_filter, self.frequency, self.grid):
            combo.currentIndexChanged.connect(self._facet_changed)
        self._provider_changed(self.provider_tabs.currentIndex())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        title = QLabel("CMIP 数据下载")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        self.provider_tabs = QTabBar()
        self.provider_tabs.setDocumentMode(True)
        self.provider_tabs.setExpanding(False)
        self.provider_tabs.setIconSize(QSize(26, 26))
        for provider in PROVIDERS:
            index = self.provider_tabs.addTab(
                _provider_icon(provider.icon_text, provider.icon_color), provider.name
            )
            self.provider_tabs.setTabData(index, provider.id)
            self.provider_tabs.setTabToolTip(index, provider.description)
        self.provider_tabs.currentChanged.connect(self._provider_changed)
        root.addWidget(self.provider_tabs)
        self.provider_info = QLabel()
        self.provider_info.setObjectName("ProviderInfo")
        self.provider_info.setWordWrap(True)
        root.addWidget(self.provider_info)
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        filters = QWidget()
        filters.setFixedWidth(330)
        form = QFormLayout(filters)
        self.variable_search = QLineEdit()
        self.variable_search.setPlaceholderText("搜索变量名称或代码")
        self.product = QComboBox()
        self.variable_match = QComboBox()
        self.variable_match.setMinimumContentsLength(24)
        self.source = self._filter_combo("全部模型")
        self.experiment = self._filter_combo("全部情景（分页显示）")
        for scenario in self.common_scenarios:
            self.experiment.addItem(_scenario_label(scenario), scenario)
        self.table_filter = self._filter_combo("全部数据表")
        self.frequency = self._filter_combo("全部频率")
        self.grid = self._filter_combo("全部网格")
        self.latitude = QDoubleSpinBox()
        self.latitude.setRange(-90, 90)
        self.latitude.setDecimals(4)
        self.latitude.setValue(39.9)
        self.longitude = QDoubleSpinBox()
        self.longitude.setRange(-180, 180)
        self.longitude.setDecimals(4)
        self.longitude.setValue(116.4)
        self.station = QLineEdit()
        self.station.setPlaceholderText("例如 USW00094728")
        self.start_year = QSpinBox()
        self.start_year.setRange(1, 9999)
        self.start_year.setValue(2020)
        self.end_year = QSpinBox()
        self.end_year.setRange(1, 9999)
        self.end_year.setValue(2100)
        self.refresh_facets_button = QPushButton("刷新可选项")
        self.refresh_facets_button.clicked.connect(self.refresh_facets)
        self.search_button = QPushButton("查询数据")
        self.search_button.setObjectName("PrimaryButton")
        self.search_button.clicked.connect(self.run_search)
        form.addRow("数据产品", self.product)
        form.addRow("变量搜索", self.variable_search)
        form.addRow("变量", self.variable_match)
        form.addRow("模型", self.source)
        form.addRow("情景", self.experiment)
        form.addRow("数据表", self.table_filter)
        form.addRow("频率", self.frequency)
        form.addRow("网格", self.grid)
        form.addRow("纬度", self.latitude)
        form.addRow("经度", self.longitude)
        form.addRow("站点编号", self.station)
        form.addRow("开始年份", self.start_year)
        form.addRow("结束年份", self.end_year)
        form.addRow(self.refresh_facets_button)
        form.addRow(self.search_button)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        toolbar = QHBoxLayout()
        self.summary = QLabel("选择变量后查询。时间条件按覆盖范围匹配。")
        self.selected_count = QLabel("已选 0 个数据系列")
        self.selected_count.setObjectName("SelectionCount")
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self.next_page)
        self.next_button.setEnabled(False)
        export_button = QPushButton("导出列表")
        export_button.clicked.connect(self._export)
        self.open_source_button = QPushButton("打开来源")
        self.open_source_button.setEnabled(False)
        self.open_source_button.clicked.connect(self._open_selected_source)
        self.download_button = QPushButton("下载所选")
        self.download_button.setObjectName("PrimaryButton")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._download_selected)
        toolbar.addWidget(self.summary, 1)
        toolbar.addWidget(self.selected_count)
        toolbar.addWidget(export_button)
        toolbar.addWidget(self.open_source_button)
        toolbar.addWidget(self.next_button)
        toolbar.addWidget(self.download_button)
        self.activity = QWidget()
        activity_layout = QHBoxLayout(self.activity)
        activity_layout.setContentsMargins(8, 4, 8, 4)
        self.activity_text = QLabel("正在查询数据节点，请稍候…")
        self.activity_bar = QProgressBar()
        self.activity_bar.setRange(0, 0)
        activity_layout.addWidget(self.activity_text)
        activity_layout.addWidget(self.activity_bar, 1)
        self.activity.hide()
        self.table_widget = QTableWidget(0, len(self.columns))
        self.table_widget.setHorizontalHeaderLabels(self.columns)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_widget.itemChanged.connect(self._selection_changed)
        self.table_widget.itemSelectionChanged.connect(self._result_row_changed)
        header = self.table_widget.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(
            (42, 100, 55, 80, 65, 85, 50, 50, 105, 52, 65, 82, 180)
        ):
            self.table_widget.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        results_layout.addLayout(toolbar)
        results_layout.addWidget(self.activity)
        results_layout.addWidget(self.table_widget, 1)
        content_layout.addWidget(filters)
        content_layout.addWidget(results, 1)
        root.addWidget(content, 1)
        self._form = form
        self.product.currentIndexChanged.connect(self._product_changed)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._sync_horizontal_scrollbar)

    def _sync_horizontal_scrollbar(self) -> None:
        overflow = (
            self.table_widget.horizontalHeader().length()
            - self.table_widget.viewport().width()
        )
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if overflow <= 2
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.table_widget.setHorizontalScrollBarPolicy(policy)

    @staticmethod
    def _filter_combo(all_label: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(all_label, "")
        return combo

    def _provider_changed(self, index: int) -> None:
        provider_id = str(self.provider_tabs.tabData(index) or "esgf")
        self._provider_id = provider_id
        provider = provider_definition(provider_id)
        self.provider_info.setText(provider.description)
        self.product.blockSignals(True)
        self.product.clear()
        for product in provider.products:
            self.product.addItem(product.name, product.id)
            self.product.setItemData(
                self.product.count() - 1,
                product.description,
                Qt.ItemDataRole.ToolTipRole,
            )
        self.product.blockSignals(False)
        self._reset_filter_values()
        self._set_filter_visibility(provider.visible_filters)
        self._reset_results(f"已切换到 {provider.name}，请选择变量后查询。")
        self.variable_search.clear()
        self._provider_variables = ()
        if provider_id == "esgf":
            self._update_variable_matches()
        else:
            self._load_provider_variables()

    def _product_changed(self) -> None:
        if self._provider_id == "esgf":
            return
        product_id = str(self.product.currentData() or "")
        product = next(
            (
                item
                for item in provider_definition(self._provider_id).products
                if item.id == product_id
            ),
            None,
        )
        if product:
            self.provider_info.setText(
                f"{provider_definition(self._provider_id).description}  {product.description}"
            )
        self._reset_results("数据产品已切换，请重新选择变量。")
        self._reset_filter_values()
        self._load_provider_variables()

    def _reset_filter_values(self) -> None:
        self._populating = True
        try:
            for combo, label in (
                (self.source, "全部模型"),
                (self.experiment, "全部情景（分页显示）"),
                (self.table_filter, "全部数据表"),
                (self.frequency, "全部频率"),
                (self.grid, "全部网格"),
            ):
                combo.clear()
                combo.addItem(label, "")
            if self._provider_id == "esgf":
                for scenario in self.common_scenarios:
                    self.experiment.addItem(_scenario_label(scenario), scenario)
        finally:
            self._populating = False

    def _set_filter_visibility(self, visible_filters: frozenset[str]) -> None:
        fields = {
            "product": self.product,
            "model": self.source,
            "scenario": self.experiment,
            "table": self.table_filter,
            "frequency": self.frequency,
            "grid": self.grid,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "station": self.station,
        }
        visibility = {
            "product": self._provider_id != "esgf",
            "model": "model" in visible_filters,
            "scenario": "scenario" in visible_filters,
            "table": "table" in visible_filters,
            "frequency": "frequency" in visible_filters,
            "grid": "grid" in visible_filters,
            "latitude": "location" in visible_filters,
            "longitude": "location" in visible_filters,
            "station": "station" in visible_filters,
        }
        for name, widget in fields.items():
            shown = visibility[name]
            widget.setVisible(shown)
            label = self._form.labelForField(widget)
            if label:
                label.setVisible(shown)
        has_facets = bool(
            visible_filters & {"model", "scenario", "table", "frequency", "grid"}
        )
        self.refresh_facets_button.setVisible(has_facets)

    def _load_provider_variables(self) -> None:
        if self._provider_id == "esgf":
            return
        product_id = str(self.product.currentData() or "")
        if not product_id:
            return
        self._variable_sequence += 1
        sequence = self._variable_sequence
        provider_id = self._provider_id
        self.variable_match.clear()
        self.variable_match.addItem("正在读取变量…", None)
        self.variable_match.setEnabled(False)
        self.search_button.setEnabled(False)
        self.activity_text.setText("正在读取数据源变量目录，请稍候…")
        self.activity.show()

        async def load_variables():
            variables = await discover_provider_variables(provider_id, product_id)
            return sequence, provider_id, product_id, variables

        worker = AsyncRunnable(load_variables)
        self._workers.add(worker)
        worker.signals.result.connect(self._show_provider_variables)
        worker.signals.error.connect(self._show_error)
        worker.signals.finished.connect(
            lambda: self._finish_provider_variable_worker(worker, sequence)
        )
        self.pool.start(worker)

    def _show_provider_variables(self, result: object) -> None:
        sequence, provider_id, product_id, variables = result
        if (
            sequence != self._variable_sequence
            or provider_id != self._provider_id
            or product_id != str(self.product.currentData() or "")
        ):
            return
        self._provider_variables = tuple(variables)
        self._update_variable_matches()
        self.summary.setText(f"已读取 {len(variables)} 个变量，可用中文或英文搜索。")

    def _finish_provider_variable_worker(
        self, worker: AsyncRunnable, sequence: int
    ) -> None:
        self._workers.discard(worker)
        if sequence != self._variable_sequence:
            return
        self.variable_match.setEnabled(True)
        self.search_button.setEnabled(True)
        self.activity.hide()

    def _reset_results(self, message: str) -> None:
        self.files = []
        self._next_cursors = {}
        self._last_request = None
        self._page_number = 1
        self.table_widget.setRowCount(0)
        self.next_button.setEnabled(False)
        self.open_source_button.setEnabled(False)
        self.summary.setText(message)
        self._selection_changed()

    def _facet_changed(self) -> None:
        if not self._populating:
            self._facet_timer.start()

    def _variable_changed(self) -> None:
        if self._populating:
            return
        self._populating = True
        try:
            self.table_filter.setCurrentIndex(0)
            self.frequency.setCurrentIndex(0)
        finally:
            self._populating = False
        self._facet_timer.start()

    def _update_variable_matches(self) -> None:
        previous = self.variable_match.currentData()
        if self._provider_id == "esgf":
            matches = self.catalog.search_grouped(
                self.variable_search.text().strip(), limit=100
            )
        else:
            matches = filter_provider_variables(
                self._provider_variables, self.variable_search.text().strip(), limit=100
            )
        self.variable_match.blockSignals(True)
        self.variable_match.clear()
        self.variable_match.addItem("请选择变量", None)
        for option in matches:
            if isinstance(option, VariableOption):
                definition = option.preferred
                name = friendly_variable_name(
                    option.variable_id, definition.long_name, definition.chinese_name
                )
                codes = " / ".join(option.variable_ids)
                tooltip = (
                    f"变量代码: {', '.join(option.variable_ids)}\n"
                    f"可用频率: "
                    f"{', '.join(_frequency_label(value) for value in option.frequencies)}\n"
                    f"数据表: {', '.join(option.table_ids)}"
                )
            else:
                name = option.display_name
                codes = option.id
                details = [f"英文名称: {option.english_name}"]
                if option.units:
                    details.append(f"单位: {option.units}")
                tooltip = "\n".join(details)
            self.variable_match.addItem(f"{name} ({codes})", option)
            self.variable_match.setItemData(
                self.variable_match.count() - 1, tooltip, Qt.ItemDataRole.ToolTipRole
            )
        if isinstance(previous, VariableOption):
            for index in range(1, self.variable_match.count()):
                candidate = self.variable_match.itemData(index)
                if isinstance(candidate, VariableOption) and (
                    candidate.variable_ids == previous.variable_ids
                ):
                    self.variable_match.setCurrentIndex(index)
                    break
        elif isinstance(previous, ProviderVariable):
            index = self.variable_match.findData(previous)
            if index >= 0:
                self.variable_match.setCurrentIndex(index)
        self.variable_match.blockSignals(False)

    def _request(
        self, *, include_filters: bool = True, show_message: bool = True
    ) -> SearchRequest | None:
        variable = self.variable_match.currentData()
        if isinstance(variable, VariableOption):
            variable_ids = variable.variable_ids
        elif isinstance(variable, ProviderVariable):
            variable_ids = (variable.id,)
        else:
            if show_message:
                QMessageBox.information(
                    self, "请选择变量", "请先从“变量”下拉列表中选择一项。"
                )
            return None
        facets: list[FacetConstraint] = [
            FacetConstraint(name="variable_id", values=variable_ids)
        ]
        visible_filters = provider_definition(self._provider_id).visible_filters
        if include_filters:
            for visible_name, name, combo in (
                ("model", "source_id", self.source),
                ("scenario", "experiment_id", self.experiment),
                ("table", "table_id", self.table_filter),
                ("frequency", "frequency", self.frequency),
                ("grid", "grid_label", self.grid),
            ):
                if visible_name not in visible_filters:
                    continue
                value = str(combo.currentData() or "")
                if value:
                    facets.append(FacetConstraint(name=name, values=(value,)))
        return SearchRequest(
            provider_id=self._provider_id,
            product_id=str(self.product.currentData() or "cmip6"),
            facets=tuple(facets),
            replicas="masters",
            start_year=self.start_year.value(),
            end_year=self.end_year.value(),
            page_size=100,
            parameters={
                "latitude": str(self.latitude.value()),
                "longitude": str(self.longitude.value()),
                "station": self.station.text().strip(),
            },
        )

    def run_search(self) -> None:
        request = self._request()
        if request is not None:
            self._page_number = 1
            variable = self.variable_match.currentData()
            variable_ids = (
                variable.variable_ids
                if isinstance(variable, VariableOption)
                else (variable.id,)
            )
            self.state.message.emit(
                f"查询 {provider_definition(self._provider_id).name}："
                f"{', '.join(variable_ids)}，"
                f"{self.start_year.value()}-{self.end_year.value()}"
            )
            self._run_request(request, {})

    def next_page(self) -> None:
        if self._last_request and any(value is not None for value in self._next_cursors.values()):
            self._page_number += 1
            self._run_request(self._last_request, self._next_cursors)

    def _run_request(self, request: SearchRequest, cursors: dict[str, str | int | None]) -> None:
        if self._search_worker is not None:
            return
        self._last_request = request
        self._search_sequence += 1
        sequence = self._search_sequence
        self.search_button.setEnabled(False)
        self.search_button.setText("查询中…")
        self.next_button.setEnabled(False)
        provider_name = provider_definition(request.provider_id).name
        self.summary.setText(f"正在查询 {provider_name}…")
        self.activity_text.setText(f"正在查询 {provider_name}，请稍候…")
        self.activity.show()

        async def search() -> ResultPage:
            registry = default_registry(request.provider_id)
            try:
                service = MultiBackendSearchService(registry)
                return await service.search(request, cursors)
            finally:
                await registry.close()

        worker = AsyncRunnable(search)
        self._search_worker = worker
        self._workers.add(worker)
        worker.signals.result.connect(lambda page: self._show_search_results(sequence, page))
        worker.signals.error.connect(self._show_error)
        worker.signals.finished.connect(lambda: self._finish_search_worker(worker))
        self.pool.start(worker)

    def _show_search_results(self, sequence: int, page: ResultPage) -> None:
        if sequence == self._search_sequence:
            self._show_results(page)

    def _finish_search_worker(self, worker: AsyncRunnable) -> None:
        self._workers.discard(worker)
        if self._search_worker is worker:
            self._search_worker = None
            self.search_button.setEnabled(True)
            self.search_button.setText("查询数据")
            self.activity.hide()
            self._selection_changed()

    def _show_results(self, page: ResultPage) -> None:
        self.files = list(page.files)
        if page.facet_counts:
            self._populate_facet_filters(page.facet_counts)
        self._next_cursors = page.next_cursors
        self.next_button.setEnabled(any(value is not None for value in page.next_cursors.values()))
        self.table_widget.blockSignals(True)
        self.table_widget.setSortingEnabled(False)
        self.table_widget.setRowCount(len(self.files))
        for row, file in enumerate(self.files):
            definition = self._definition_for(file)
            provider_variable = next(
                (
                    variable
                    for variable in self._provider_variables
                    if variable.id == file.variable_id
                ),
                None,
            )
            name = (
                provider_variable.display_name
                if provider_variable
                else friendly_variable_name(
                    file.variable_id or "",
                    definition.long_name if definition else None,
                    definition.chinese_name if definition else None,
                )
            )
            select_item = QTableWidgetItem()
            if _is_downloadable(file):
                select_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                select_item.setToolTip("勾选后可加入下载任务")
            else:
                select_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                select_item.setToolTip(
                    str(file.raw_provenance.get("access_note") or "当前不能直接下载")
                )
            select_item.setCheckState(Qt.CheckState.Unchecked)
            select_item.setData(Qt.ItemDataRole.UserRole, file.logical_key)
            self.table_widget.setItem(row, 0, select_item)
            values = (
                name,
                file.variable_id or "-",
                file.source_id or "-",
                file.nominal_resolution or _grid_resolution(file.grid_label),
                _scenario_label(file.experiment_id or "-"),
                _frequency_label(file.frequency or file.table_id or "-"),
                file.table_id or "-",
                _coverage(file),
                str(file.file_count),
                _human_bytes(file.size_bytes),
                str(file.raw_provenance.get("access_note") or "可直接下载"),
                file.filename,
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, file.logical_key)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(str(value))
                self.table_widget.setItem(row, column, item)
        self.table_widget.setSortingEnabled(True)
        self.table_widget.blockSignals(False)
        self._sync_horizontal_scrollbar()
        self._selection_changed()
        totals = "，".join(
            f"{name}: {value:,}" for name, value in page.raw_total_by_backend.items()
        )
        total_text = f"；节点命中 {totals}" if totals else ""
        unavailable = len(page.warnings)
        warning_text = f"；{unavailable} 个节点提示" if unavailable else ""
        physical_count = sum(file.file_count for file in self.files)
        series_count = sum(1 for file in self.files if file.series_members)
        if series_count:
            result_text = (
                f"{len(self.files)} 个数据系列，包含 {physical_count} 个实际文件"
            )
        else:
            result_text = f"{len(self.files)} 个文件"
        self.summary.setText(
            f"第 {self._page_number} 页显示 {result_text}{total_text}{warning_text}"
        )

    def _definition_for(self, file: LogicalFile) -> VariableDefinition | None:
        if file.provider_id != "esgf":
            return None
        for item in self.catalog.search(file.variable_id or "", limit=100):
            if item.variable_id == file.variable_id and item.table_id == file.table_id:
                return item
        return None

    def _selected_files(self) -> list[LogicalFile]:
        keys = {
            self.table_widget.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table_widget.rowCount())
            if self.table_widget.item(row, 0).checkState() == Qt.CheckState.Checked
        }
        return [file for file in self.files if file.logical_key in keys]

    def _selection_changed(self, *_args: object) -> None:
        selected = self._selected_files()
        physical_count = sum(file.file_count for file in selected)
        if any(file.series_members for file in selected):
            self.selected_count.setText(
                f"已选 {len(selected)} 个系列（{physical_count} 个文件）"
            )
        else:
            self.selected_count.setText(f"已选 {physical_count} 个文件")
        self.download_button.setEnabled(bool(selected) and not self.activity.isVisible())
        previous = self.table_widget.blockSignals(True)
        try:
            for row in range(self.table_widget.rowCount()):
                checked = (
                    self.table_widget.item(row, 0).checkState()
                    == Qt.CheckState.Checked
                )
                color = QColor("transparent") if not checked else QColor("#b8e1e3")
                for column in range(self.table_widget.columnCount()):
                    item = self.table_widget.item(row, column)
                    if item:
                        item.setBackground(color)
        finally:
            self.table_widget.blockSignals(previous)

    def _result_row_changed(self) -> None:
        rows = self.table_widget.selectionModel().selectedRows()
        if not rows:
            self.open_source_button.setEnabled(False)
            return
        file = self._file_for_row(rows[0].row())
        self.open_source_button.setEnabled(bool(_source_url(file)))

    def _open_selected_source(self) -> None:
        rows = self.table_widget.selectionModel().selectedRows()
        if not rows:
            return
        url = _source_url(self._file_for_row(rows[0].row()))
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _file_for_row(self, row: int) -> LogicalFile:
        key = self.table_widget.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return next(file for file in self.files if file.logical_key == key)

    def _download_selected(self) -> None:
        selected = self._selected_files()
        if not selected:
            return
        expanded = {
            physical.logical_key: physical
            for entry in selected
            for physical in entry.download_files
        }
        physical_files = list(expanded.values())
        series_count = sum(1 for entry in selected if entry.series_members)
        job_name = (
            f"下载 {series_count} 个数据系列（{len(physical_files)} 个文件）"
            if series_count
            else f"下载 {len(physical_files)} 个文件"
        )
        job = self.workflow.create_job(
            job_name,
            {
                "series": [item.logical_key for item in selected],
                "files": [item.logical_key for item in physical_files],
            },
        )
        queued: list[tuple[UUID, LogicalFile]] = []
        existing = 0
        for file in physical_files:
            task_id, created = self.workflow.enqueue_download(job, file)
            if created:
                queued.append((task_id, file))
            else:
                existing += 1
        self.table_widget.blockSignals(True)
        for row in range(self.table_widget.rowCount()):
            self.table_widget.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self._selection_changed()
        if series_count:
            message = (
                f"已选择 {series_count} 个数据系列，共 {len(physical_files)} 个文件；"
                f"新增 {len(queued)} 个下载任务"
            )
        else:
            message = f"已添加 {len(queued)} 个下载任务"
        if existing:
            message += f"，另有 {existing} 个已在任务列表中"
        QMessageBox.information(self, "已添加到下载任务", message)
        self.state.message.emit(message)

        async def download_all() -> tuple[list[Path], list[tuple[str, str]]]:
            paths: list[Path] = []
            failures: list[tuple[str, str]] = []
            for task_id, file in queued:
                if self.workflow.shutdown_requested:
                    break
                if self.workflow.repository.status(task_id) is TaskStatus.CANCELED:
                    continue
                try:
                    paths.append(await self.workflow.run_download_task(task_id, file))
                except DownloadCancelled:
                    continue
                except Exception as exc:
                    failures.append((file.filename, str(exc)))
            return paths, failures

        if not queued:
            return
        worker = AsyncRunnable(download_all)
        self._start_worker(worker, self._downloads_finished, self._download_failed)

    def _downloads_finished(self, result: tuple[list[Path], list[tuple[str, str]]]) -> None:
        paths, failures = result
        self.summary.setText(f"下载队列结束：完成 {len(paths)} 个，失败 {len(failures)} 个")
        message = (
            f"下载结束：成功 {len(paths)} 个，失败 {len(failures)} 个；"
            f"保存位置 {self.workflow.storage_root}"
        )
        self.state.message.emit(message)

    def _download_failed(self, trace: str, error: object) -> None:
        self.summary.setText("下载未完成，可在“下载任务”中查看失败原因")
        self.state.message.emit(f"下载队列异常: {error}")

    def refresh_facets(self) -> None:
        request = self._request(include_filters=False, show_message=False)
        if request is None:
            return
        self._facet_sequence += 1
        sequence = self._facet_sequence
        self.refresh_facets_button.setEnabled(False)

        async def load_facets():
            registry = default_registry(request.provider_id)
            try:
                return sequence, await MultiBackendSearchService(registry).facets(
                    request, self.facet_names
                )
            finally:
                await registry.close()

        worker = AsyncRunnable(load_facets)
        self._start_worker(worker, self._show_facets)

    def _show_facets(self, result: object) -> None:
        sequence, (facets, warnings) = result
        if sequence != self._facet_sequence:
            return
        self._populate_facet_filters(facets)
        self.refresh_facets_button.setEnabled(True)
        self.summary.setText(f"可选项已更新，{len(warnings)} 个节点提示")

    def _populate_facet_filters(self, facets: dict[str, dict[str, int]]) -> None:
        self._populating = True
        try:
            for name, combo, all_label in (
                ("source_id", self.source, "全部模型"),
                ("experiment_id", self.experiment, "全部情景"),
                ("table_id", self.table_filter, "全部数据表"),
                ("frequency", self.frequency, "全部频率"),
                ("grid_label", self.grid, "全部网格"),
            ):
                current = str(combo.currentData() or "")
                values = sorted(facets.get(name, {}), key=lambda v: (-facets[name][v], v))
                if name == "frequency":
                    values = sorted(values, key=_frequency_sort_key)
                if name == "experiment_id" and self._provider_id == "esgf":
                    values = list(dict.fromkeys((*self.common_scenarios, *values)))
                combo.clear()
                if name == "experiment_id":
                    combo.addItem("全部情景（分页显示）", "")
                else:
                    combo.addItem(all_label, "")
                for value in values:
                    if name == "experiment_id":
                        label = _scenario_label(value)
                    elif name == "frequency":
                        friendly = _frequency_label(value)
                        label = f"{friendly} ({value})" if friendly != value else value
                    else:
                        label = value
                    count = facets.get(name, {}).get(value)
                    display = f"{label} · {count:,}" if count is not None else label
                    combo.addItem(display, value)
                index = combo.findData(current)
                combo.setCurrentIndex(max(0, index))
        finally:
            self._populating = False

    def _show_error(self, trace: str, error: object) -> None:
        self.activity.hide()
        QMessageBox.critical(self, "操作失败", f"{error}\n\n{trace[-1200:]}")

    def _start_worker(self, worker: AsyncRunnable, result_slot, error_slot=None) -> None:
        self._workers.add(worker)
        worker.signals.result.connect(result_slot)
        worker.signals.error.connect(error_slot or self._show_error)
        worker.signals.finished.connect(lambda: self._finish_worker(worker))
        self.pool.start(worker)

    def _finish_worker(self, worker: AsyncRunnable) -> None:
        self._workers.discard(worker)
        self.refresh_facets_button.setEnabled(True)

    def _export(self) -> None:
        if not self.files:
            QMessageBox.information(self, "导出列表", "当前没有可导出的数据。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出当前列表", "cmip-data.csv", "CSV (*.csv)")
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8-sig") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=(
                    "provider_id",
                    "product_id",
                    "filename",
                    "variable_id",
                    "source_id",
                    "experiment_id",
                    "frequency",
                    "grid_label",
                    "nominal_resolution",
                    "file_count",
                    "size_bytes",
                ),
                extrasaction="ignore",
            )
            writer.writeheader()
            for file in self.files:
                row = file.model_dump(mode="json")
                row["file_count"] = file.file_count
                writer.writerow(row)
        self.state.message.emit(f"已导出 {len(self.files)} 条数据记录")


def _coverage(file: LogicalFile) -> str:
    return f"{file.temporal.start or '?'} - {file.temporal.end or '?'}"


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "未知"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "未知"


def _scenario_label(value: str) -> str:
    labels = {
        "historical": "历史模拟 (historical)",
        "piControl": "工业化前控制 (piControl)",
        "ssp119": "SSP1-1.9 低排放 (ssp119)",
        "ssp126": "SSP1-2.6 低排放 (ssp126)",
        "ssp245": "SSP2-4.5 中等排放 (ssp245)",
        "ssp370": "SSP3-7.0 高排放 (ssp370)",
        "ssp434": "SSP4-3.4 (ssp434)",
        "ssp460": "SSP4-6.0 (ssp460)",
        "ssp534-over": "SSP5-3.4 过冲 (ssp534-over)",
        "ssp585": "SSP5-8.5 极高排放 (ssp585)",
        "ssp1_1_9": "SSP1-1.9 低排放 (ssp1_1_9)",
        "ssp1_2_6": "SSP1-2.6 低排放 (ssp1_2_6)",
        "ssp2_4_5": "SSP2-4.5 中等排放 (ssp2_4_5)",
        "ssp3_7_0": "SSP3-7.0 高排放 (ssp3_7_0)",
        "ssp4_3_4": "SSP4-3.4 (ssp4_3_4)",
        "ssp4_6_0": "SSP4-6.0 (ssp4_6_0)",
        "ssp5_3_4os": "SSP5-3.4 过冲 (ssp5_3_4os)",
        "ssp5_8_5": "SSP5-8.5 极高排放 (ssp5_8_5)",
    }
    return labels.get(value, value)


def _frequency_label(value: str) -> str:
    labels = {
        "mon": "月",
        "monC": "月（气候态）",
        "day": "日",
        "1hr": "1 小时",
        "1hrPt": "1 小时瞬时值",
        "6hr": "6 小时",
        "6hrPt": "6 小时瞬时值",
        "3hr": "3 小时",
        "3hrPt": "3 小时瞬时值",
        "subhrPt": "小时内瞬时值",
        "yr": "年",
        "annual": "年",
        "fx": "固定值",
        "Amon": "月",
    }
    return labels.get(value, value)


def _frequency_sort_key(value: str) -> tuple[int, str]:
    order = {
        "yr": 0,
        "annual": 0,
        "mon": 1,
        "monC": 2,
        "day": 3,
        "6hr": 4,
        "6hrPt": 5,
        "3hr": 6,
        "3hrPt": 7,
        "1hr": 8,
        "1hrPt": 9,
        "subhrPt": 10,
        "fx": 11,
    }
    return order.get(value, 50), value


def _grid_resolution(grid: str | None) -> str:
    return {"gn": "原生网格", "gr": "重网格", "gr1": "重网格 1"}.get(grid or "", grid or "未知")


def _provider_icon(text: str, color: str) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(1, 1, 30, 30, 5, 5)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(10 if len(text) > 2 else 14)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return QIcon(pixmap)


def _is_downloadable(file: LogicalFile) -> bool:
    if file.series_members:
        return bool(file.series_members) and all(
            _is_downloadable(member) for member in file.series_members
        )
    if file.raw_provenance.get("requires_auth"):
        return False
    return any(
        endpoint.service.upper() == "HTTPSERVER"
        for replica in file.replicas
        for endpoint in replica.endpoints
    )


def _source_url(file: LogicalFile) -> str | None:
    if file.series_members:
        return _source_url(file.series_members[0])
    landing_url = file.raw_provenance.get("landing_url")
    if landing_url:
        return str(landing_url)
    return next(
        (
            endpoint.url
            for replica in file.replicas
            for endpoint in replica.endpoints
            if endpoint.url.startswith(("http://", "https://"))
        ),
        None,
    )
