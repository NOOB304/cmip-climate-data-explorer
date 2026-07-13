from __future__ import annotations

import re
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer.infrastructure.processing import (
    DatasetScanResult,
    LocalDatasetGroup,
    ResamplingOptions,
    aggregate_netcdf_to_geotiffs,
    allowed_target_periods,
    automatic_unit_target,
    inspect_raster_template,
    inspect_vector_region,
    recommended_statistic,
    scan_downloaded_datasets,
)
from cmip_explorer.ui.async_runner import SyncRunnable
from cmip_explorer.ui.state import ApplicationState
from cmip_explorer.ui.variable_labels import friendly_variable_name


class ProcessingPage(QWidget):
    processed = Signal()
    progress_changed = Signal(int, int, str)

    def __init__(self, state: ApplicationState, storage_root: Path) -> None:
        super().__init__()
        self.state = state
        self.storage_root = storage_root
        self.source_root = storage_root / "NetCDF"
        self.groups: list[LocalDatasetGroup] = []
        self.group: LocalDatasetGroup | None = None
        self._template_path: Path | None = None
        self._clip_path: Path | None = None
        self._cancel_event = Event()
        self._run_output_dir: Path | None = None
        self._run_existing_files: set[Path] = set()
        self._run_manifest_existed = False
        self._processing = False
        self._pending_scan = False
        self.pool = QThreadPool.globalInstance()
        self._workers: set[SyncRunnable] = set()
        self._build_ui()
        self.progress_changed.connect(self._update_progress)
        QTimer.singleShot(0, self._scan_folder)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(10)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        title = QLabel("文件处理")
        title.setObjectName("PageTitle")
        subtitle = QLabel("选择已下载的数据组，按时间合成、单位转换、重采样和研究区裁剪生成 TIF")
        subtitle.setObjectName("PageSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("下载目录"))
        self.source = QLineEdit(str(self.source_root))
        self.source.setReadOnly(True)
        refresh = QPushButton("扫描已下载数据")
        refresh.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh.clicked.connect(self._scan_folder)
        self.scan_button = refresh
        choose = QPushButton("选择其他目录…")
        choose.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        choose.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.source, 1)
        folder_row.addWidget(refresh)
        folder_row.addWidget(choose)
        root.addLayout(folder_row)

        self.scan_status = QLabel("正在查找已下载的 NetCDF 数据…")
        self.scan_status.setObjectName("MutedText")
        root.addWidget(self.scan_status)
        self.group_table = QTableWidget(0, 7)
        self.group_table.setHorizontalHeaderLabels(
            ("变量", "模型", "情景", "频率", "覆盖时间", "NC 文件", "数据量")
        )
        self.group_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.group_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.group_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.group_table.setAlternatingRowColors(True)
        self.group_table.verticalHeader().setDefaultSectionSize(38)
        self.group_table.itemSelectionChanged.connect(self._group_selected)
        header = self.group_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.group_table.setMinimumHeight(190)
        self.group_table.setMaximumHeight(270)
        root.addWidget(self.group_table)

        options = QFrame()
        options.setObjectName("Panel")
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(14, 12, 14, 14)
        options_layout.setSpacing(18)
        time_section = QWidget()
        time_layout = QVBoxLayout(time_section)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_title = QLabel("时间合成与单位")
        time_title.setObjectName("SectionTitle")
        time_form = QFormLayout()
        time_form.setVerticalSpacing(8)
        time_layout.addWidget(time_title)
        time_layout.addLayout(time_form)
        spatial_section = QWidget()
        spatial_layout = QVBoxLayout(spatial_section)
        spatial_layout.setContentsMargins(0, 0, 0, 0)
        spatial_title = QLabel("空间重采样与裁剪")
        spatial_title.setObjectName("SectionTitle")
        spatial_form = QFormLayout()
        spatial_form.setVerticalSpacing(8)
        spatial_layout.addWidget(spatial_title)
        spatial_layout.addLayout(spatial_form)
        self.variable = QComboBox()
        self.variable.setEnabled(False)
        self.detected = QLabel("请先选择一个已下载数据组")
        self.target_period = QComboBox()
        self.target_period.setEnabled(False)
        self.target_period.currentIndexChanged.connect(self._options_changed)
        self.method = QComboBox()
        self.method.setEnabled(False)
        self.method.currentIndexChanged.connect(self._options_changed)
        self.unit_conversion = QCheckBox("自动转换单位")
        self.unit_conversion.setEnabled(False)

        self.resampling_enabled = QCheckBox("启用重采样")
        self.resampling_enabled.toggled.connect(self._resampling_enabled_changed)
        self.target_grid = QComboBox()
        self.target_grid.addItem("请选择目标网格", None)
        self.target_grid.addItem("约 1 km（0.008333°）", 0.008333333333)
        self.target_grid.addItem("约 5 km（0.041667°）", 0.041666666667)
        self.target_grid.addItem("约 10 km（0.083333°）", 0.083333333333)
        self.target_grid.addItem("0.25°", 0.25)
        self.target_grid.addItem("0.5°", 0.5)
        self.target_grid.addItem("使用模板 TIF", "template")
        self.target_grid.setEnabled(False)
        self.target_grid.currentIndexChanged.connect(self._target_grid_changed)

        self.resampling_method = QComboBox()
        self.resampling_method.addItem("最近邻", "nearest")
        self.resampling_method.addItem("双线性插值", "bilinear")
        self.resampling_method.addItem("三次卷积", "cubic")
        self.resampling_method.addItem("平均值", "average")
        self.resampling_method.setCurrentIndex(1)
        self.resampling_method.setEnabled(False)

        self.template_panel = QWidget()
        template_layout = QHBoxLayout(self.template_panel)
        template_layout.setContentsMargins(0, 0, 0, 0)
        self.template = QLineEdit()
        self.template.setReadOnly(True)
        self.template.setPlaceholderText("请选择一个带 CRS 的 TIF 模板")
        self.choose_template_button = QPushButton("选择模板…")
        self.choose_template_button.clicked.connect(self._choose_template)
        template_layout.addWidget(self.template, 1)
        template_layout.addWidget(self.choose_template_button)

        self.clipping_enabled = QCheckBox("按研究区裁剪")
        self.clipping_enabled.toggled.connect(self._clipping_changed)
        self.region_panel = QWidget()
        region_layout = QHBoxLayout(self.region_panel)
        region_layout.setContentsMargins(0, 0, 0, 0)
        self.region = QLineEdit()
        self.region.setReadOnly(True)
        self.region.setPlaceholderText("支持 SHP、GeoPackage、GeoJSON 等面矢量")
        self.choose_region_button = QPushButton("选择矢量…")
        self.choose_region_button.clicked.connect(self._choose_region)
        region_layout.addWidget(self.region, 1)
        region_layout.addWidget(self.choose_region_button)
        time_form.addRow("检测结果", self.detected)
        time_form.addRow("处理变量", self.variable)
        time_form.addRow("输出时间尺度", self.target_period)
        time_form.addRow("合成规则", self.method)
        time_form.addRow("单位转换", self.unit_conversion)
        spatial_form.addRow("重采样", self.resampling_enabled)
        spatial_form.addRow("目标网格", self.target_grid)
        spatial_form.addRow("重采样方法", self.resampling_method)
        spatial_form.addRow("模板 TIF", self.template_panel)
        spatial_form.addRow("矢量裁剪", self.clipping_enabled)
        spatial_form.addRow("研究区文件", self.region_panel)
        options_layout.addWidget(time_section, 1)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        options_layout.addWidget(divider)
        options_layout.addWidget(spatial_section, 1)
        root.addWidget(options)

        self.month_panel = QWidget()
        month_layout = QVBoxLayout(self.month_panel)
        month_title = QLabel("处理月份")
        month_title.setObjectName("SectionTitle")
        month_layout.addWidget(month_title)
        grid = QGridLayout()
        self.months: list[QCheckBox] = []
        for index in range(12):
            checkbox = QCheckBox(f"{index + 1} 月")
            checkbox.setChecked(True)
            self.months.append(checkbox)
            grid.addWidget(checkbox, index // 6, index % 6)
        month_layout.addLayout(grid)
        self.month_panel.hide()
        root.addWidget(self.month_panel)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("输出位置"))
        self.output = QLineEdit(str(self.storage_root / "Processed"))
        choose_output = QPushButton("选择…")
        choose_output.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        choose_output.clicked.connect(self._choose_output)
        output_row.addWidget(self.output, 1)
        output_row.addWidget(choose_output)
        root.addLayout(output_row)

        self.progress_text = QLabel("就绪")
        self.progress_text.setObjectName("MutedText")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.process_button = QPushButton("开始处理")
        self.process_button.setObjectName("PrimaryButton")
        self.process_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.process_button.setEnabled(False)
        self.process_button.clicked.connect(self._process)
        self.stop_button = QPushButton("停止处理")
        self.stop_button.setObjectName("StopButton")
        self.stop_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop)
        )
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_processing)
        root.addWidget(self.progress_text)
        root.addWidget(self.progress)
        action_row = QHBoxLayout()
        action_row.addWidget(self.process_button, 1)
        action_row.addWidget(self.stop_button)
        root.addLayout(action_row)

    def set_storage_root(self, root: Path) -> None:
        self.storage_root = root
        self.source_root = root / "NetCDF"
        self.source.setText(str(self.source_root))
        self.output.setText(str(root / "Processed"))
        self._scan_folder()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择包含 NetCDF 的目录",
            str(self.source_root if self.source_root.exists() else self.storage_root),
        )
        if not path:
            return
        self.source_root = Path(path)
        self.source.setText(path)
        self._scan_folder()

    def _scan_folder(self) -> None:
        if any(worker for worker in self._workers):
            self._pending_scan = True
            return
        self._pending_scan = False
        self.group = None
        self.groups = []
        self.group_table.setRowCount(0)
        self.process_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.scan_status.setText("正在扫描 NC 文件并读取变量、频率和时间范围…")
        self.progress.setRange(0, 0)

        worker = SyncRunnable(lambda: scan_downloaded_datasets(self.source_root))
        self._start_worker(worker, self._scanned)

    def _scanned(self, result: DatasetScanResult) -> None:
        self.groups = list(result.groups)
        self.group_table.setRowCount(len(self.groups))
        for row, group in enumerate(self.groups):
            variable_name = friendly_variable_name(
                group.variable_id, group.variable_label, None
            )
            values = (
                f"{variable_name} ({group.variable_id})",
                group.source_id,
                group.experiment_id,
                _frequency_label(group.frequency),
                f"{group.start} - {group.end}",
                str(len(group.paths)),
                _human_bytes(group.size_bytes),
            )
            tooltip = (
                f"模拟版本: {group.member_id}\n"
                f"数据表: {group.table_id}\n"
                f"网格: {group.grid_label}\n"
                f"目录: {group.paths[0].parent}"
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltip)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.group_table.setItem(row, column, item)
        warning = f"，跳过 {len(result.warnings)} 个无法读取的文件" if result.warnings else ""
        self.scan_status.setText(f"找到 {len(self.groups)} 个可处理数据组{warning}")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress_text.setText("就绪")
        self.scan_button.setEnabled(True)
        if self.groups:
            self.group_table.selectRow(0)
        else:
            self.detected.setText("目录中没有可处理的 NetCDF 数据")

    def _group_selected(self) -> None:
        if self._processing:
            return
        row = self.group_table.currentRow()
        if row < 0 or row >= len(self.groups):
            return
        self.group = self.groups[row]
        group = self.group
        self._reset_progress()
        self.variable.clear()
        self.variable.addItem(
            f"{friendly_variable_name(group.variable_id, group.variable_label, None)} "
            f"({group.variable_id})",
            group.variable_id,
        )
        self.variable.setEnabled(True)
        self.target_period.blockSignals(True)
        self.target_period.clear()
        period_labels = {"daily": "日 TIF", "monthly": "月 TIF", "annual": "年 TIF"}
        for period in allowed_target_periods(group.frequency):
            self.target_period.addItem(period_labels[period], period)
        if group.temporal_resolution == "subdaily":
            monthly_index = self.target_period.findData("monthly")
            self.target_period.setCurrentIndex(monthly_index)
        self.target_period.setEnabled(self.target_period.count() > 0)
        self.target_period.blockSignals(False)
        self._populate_methods(group)
        self._configure_unit_conversion(group)
        self.detected.setText(
            f"{_frequency_label(group.frequency)}数据，{len(group.paths)} 个 NC，"
            f"覆盖 {group.start} 至 {group.end}，单位 {group.units or '未知'}"
        )
        self.output.setText(str(self._default_output_path(group)))
        self._options_changed()
        self.process_button.setEnabled(self.target_period.count() > 0)

    def _populate_methods(self, group: LocalDatasetGroup) -> None:
        recommended = recommended_statistic(group.variable_id, group.units)
        labels = {
            "mean": "时间加权平均",
            "total": "累计量",
            "max": "最大值",
            "min": "最小值",
            "mode": "众数",
        }
        self.method.clear()
        self.method.addItem(f"自动: {labels[recommended]}（推荐）", "auto")
        self.method.addItem("时间加权平均", "mean")
        if recommended == "total":
            self.method.addItem("累计量", "total")
        self.method.addItem("最大值", "max")
        self.method.addItem("最小值", "min")
        self.method.addItem("众数", "mode")
        self.method.setEnabled(True)

    def _options_changed(self) -> None:
        enabled = (
            self.target_period.currentData() in {"daily", "monthly", "annual"}
            and self.group is not None
            and self.group.temporal_resolution != "annual"
        )
        self.month_panel.setVisible(enabled)
        labels = {"daily": "日", "monthly": "月", "annual": "年"}
        period = labels.get(str(self.target_period.currentData()), "")
        self.process_button.setText(f"生成{period} TIF")
        if self.method.currentData() == "mode" and self.resampling_enabled.isChecked():
            nearest = self.resampling_method.findData("nearest")
            self.resampling_method.setCurrentIndex(nearest)
        if not self._processing:
            self._reset_progress()

    def _configure_unit_conversion(self, group: LocalDatasetGroup) -> None:
        target = automatic_unit_target(group.variable_id, group.units, group.standard_name)
        self.unit_conversion.blockSignals(True)
        if target == "degC":
            self.unit_conversion.setText("自动转换为摄氏度（°C）")
            self.unit_conversion.setChecked(True)
            self.unit_conversion.setEnabled(True)
        elif target == "mm":
            self.unit_conversion.setText("自动转换降水单位（累计量 mm，平均强度 mm/日）")
            self.unit_conversion.setChecked(True)
            self.unit_conversion.setEnabled(True)
        else:
            self.unit_conversion.setText("当前变量无需自动单位转换")
            self.unit_conversion.setChecked(False)
            self.unit_conversion.setEnabled(False)
        self.unit_conversion.blockSignals(False)

    def _resampling_enabled_changed(self, enabled: bool) -> None:
        self.target_grid.setEnabled(enabled and not self._processing)
        self.resampling_method.setEnabled(enabled and not self._processing)
        if enabled and self.target_grid.currentData() is None:
            self.target_grid.setCurrentIndex(self.target_grid.findData(0.083333333333))
        if not self._processing:
            self._reset_progress()

    def _target_grid_changed(self) -> None:
        template_mode = self.target_grid.currentData() == "template"
        self.template.setEnabled(template_mode)
        self.choose_template_button.setEnabled(not self._processing)
        if not self._processing:
            self._reset_progress()

    def _choose_template(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择重采样模板 TIF",
            str(self._template_path.parent if self._template_path else self.storage_root),
            "GeoTIFF (*.tif *.tiff)",
        )
        if not path:
            return
        try:
            grid = inspect_raster_template(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "模板不可用", str(exc))
            return
        self._template_path = Path(path)
        self.template.setText(path)
        self.template.setToolTip(
            f"CRS: {grid.crs}\n尺寸: {grid.width} × {grid.height}\n"
            f"分辨率: {grid.x_resolution:g} × {grid.y_resolution:g}"
        )
        self.resampling_enabled.setChecked(True)
        self.target_grid.setCurrentIndex(self.target_grid.findData("template"))
        self._reset_progress()

    def _clipping_changed(self, _enabled: bool) -> None:
        if not self._processing:
            self._reset_progress()

    def _choose_region(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择研究区矢量文件",
            str(self._clip_path.parent if self._clip_path else self.storage_root),
            "矢量文件 (*.shp *.gpkg *.geojson *.json *.kml *.gml *.fgb *.sqlite *.tab *.mif);;"
            "所有文件 (*.*)",
        )
        if not path:
            return
        try:
            info = inspect_vector_region(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "研究区不可用", str(exc))
            return
        self._clip_path = Path(path)
        self.region.setText(path)
        west, south, east, north = info.bounds_wgs84
        self.region.setToolTip(
            f"要素: {info.feature_count}，图层: {info.layer_count}\n"
            f"原始 CRS: {info.source_crs}\n"
            f"WGS84 边界: {west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}\n"
            f"读取组件: {', '.join(item.name for item in info.companion_files)}"
        )
        self.clipping_enabled.setChecked(True)
        self._reset_progress()

    def _default_output_path(self, group: LocalDatasetGroup) -> Path:
        return (
            self.storage_root
            / "Processed"
            / _folder(group.source_id)
            / _folder(group.experiment_id)
            / _folder(f"{group.variable_id}_{group.frequency}_{group.member_id}")
        )

    def _reset_progress(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress_text.setText("就绪")

    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出位置", self.output.text())
        if path:
            self.output.setText(path)

    def _process(self) -> None:
        if self.group is None:
            return
        months = tuple(index + 1 for index, item in enumerate(self.months) if item.isChecked())
        if not months:
            QMessageBox.information(self, "选择月份", "请至少选择一个月份。")
            return
        target_period = str(self.target_period.currentData() or "")
        statistic = str(self.method.currentData() or "auto")
        output = Path(self.output.text())
        group = self.group
        try:
            resampling = self._resampling_options()
        except ValueError as exc:
            QMessageBox.information(self, "重采样设置", str(exc))
            return
        if self.clipping_enabled.isChecked() and self._clip_path is None:
            QMessageBox.information(self, "研究区设置", "请先选择研究区矢量文件")
            return
        if (
            resampling.target_resolution is not None
            and resampling.target_resolution <= 0.008333333334
            and QMessageBox.question(
                self,
                "确认 1 km 网格",
                "全球 1 km 网格的单个 TIF 可能达到数 GB，且不会增加原始气候模型的真实信息。"
                "确定继续吗？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._cancel_event.clear()
        self._run_output_dir = output.resolve()
        self._run_existing_files = (
            {path.resolve() for path in output.glob("*.tif")} if output.exists() else set()
        )
        self._run_manifest_existed = (output / "processing_manifest.json").exists()
        self._set_processing(True)
        self.progress.setRange(0, 0)
        self.progress_text.setText("正在检查时间完整性并准备处理…")
        self.state.message.emit(
            f"开始处理 {group.variable_id}: {len(group.paths)} 个 NC -> {target_period} TIF"
        )

        def report(current: int, total: int, name: str) -> None:
            self.progress_changed.emit(current, total, name)

        def factory() -> tuple[Path, ...]:
            return aggregate_netcdf_to_geotiffs(
                group.paths,
                group.variable_id,
                output,
                source_frequency=group.frequency,
                target_period=target_period,  # type: ignore[arg-type]
                statistic=statistic,  # type: ignore[arg-type]
                months=months,
                convert_units=self.unit_conversion.isChecked(),
                resampling=resampling,
                clip_path=self._clip_path if self.clipping_enabled.isChecked() else None,
                progress=report,
                cancelled=self._cancel_event.is_set,
            )

        worker = SyncRunnable(factory)
        self._start_worker(worker, self._finished)

    def _finished(self, paths: tuple[Path, ...]) -> None:
        if self._cancel_event.is_set():
            self._handle_cancelled()
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress_text.setText(f"处理完成，生成 {len(paths)} 个 TIF")
        self._set_processing(False)
        self.state.message.emit(f"文件处理完成，输出位置: {self.output.text()}")
        self.processed.emit()
        self._clear_run_tracking()

    def _failed(self, trace: str, error: object) -> None:
        if isinstance(error, InterruptedError):
            self._handle_cancelled()
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress_text.setText(f"处理失败: {error}")
        self._set_processing(False)
        self.scan_button.setEnabled(True)
        self._clear_run_tracking()
        QMessageBox.critical(self, "文件处理失败", f"{error}\n\n{trace[-1200:]}")

    def _stop_processing(self) -> None:
        if not self._processing or self._cancel_event.is_set():
            return
        self._cancel_event.set()
        self.stop_button.setEnabled(False)
        self.progress_text.setText("正在停止，请等待当前计算步骤结束…")
        self.state.message.emit("已请求停止文件处理")

    def _handle_cancelled(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress_text.setText("处理已停止")
        self._set_processing(False)
        generated = self._current_run_files()
        answer = QMessageBox.question(
            self,
            "处理已停止",
            f"本次处理已生成 {len(generated)} 个 TIF。是否删除这些本次新生成的数据？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        removed = 0
        if answer == QMessageBox.StandardButton.Yes:
            for path in generated:
                if path.exists():
                    path.unlink()
                    removed += 1
            if self._run_output_dir is not None and not self._run_manifest_existed:
                (self._run_output_dir / "processing_manifest.json").unlink(missing_ok=True)
                (self._run_output_dir / "processing_manifest.json.part").unlink(missing_ok=True)
            self.progress_text.setText(f"处理已停止，已删除本次生成的 {removed} 个 TIF")
            self.state.message.emit(f"处理已停止并清理 {removed} 个本次输出文件")
        else:
            self.progress_text.setText(f"处理已停止，保留本次生成的 {len(generated)} 个 TIF")
            self.state.message.emit("处理已停止，已保留本次输出")
        self.processed.emit()
        self._clear_run_tracking()

    def _current_run_files(self) -> tuple[Path, ...]:
        if self._run_output_dir is None or not self._run_output_dir.exists():
            return ()
        return tuple(
            path
            for path in self._run_output_dir.glob("*.tif")
            if path.resolve() not in self._run_existing_files
        )

    def _clear_run_tracking(self) -> None:
        self._cancel_event.clear()
        self._run_output_dir = None
        self._run_existing_files = set()
        self._run_manifest_existed = False

    def _start_worker(self, worker: SyncRunnable, result_slot) -> None:
        self._workers.add(worker)
        worker.signals.result.connect(result_slot)
        worker.signals.error.connect(self._failed)
        worker.signals.finished.connect(lambda: self._worker_finished(worker))
        self.pool.start(worker)

    def _worker_finished(self, worker: SyncRunnable) -> None:
        self._workers.discard(worker)
        self.scan_button.setEnabled(True)
        if self._pending_scan:
            QTimer.singleShot(0, self._scan_folder)

    def _update_progress(self, current: int, total: int, name: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)
        self.progress.setFormat("%v / %m")
        self.progress_text.setText(f"正在处理 {name}")

    def _resampling_options(self) -> ResamplingOptions:
        if not self.resampling_enabled.isChecked():
            return ResamplingOptions()
        target = self.target_grid.currentData()
        if target is None:
            raise ValueError("请选择目标网格")
        method = str(self.resampling_method.currentData() or "nearest")
        if target == "template":
            if self._template_path is None:
                raise ValueError("请先选择模板 TIF")
            return ResamplingOptions(method=method, template_path=self._template_path)  # type: ignore[arg-type]
        return ResamplingOptions(method=method, target_resolution=float(target))  # type: ignore[arg-type]

    def _set_processing(self, active: bool) -> None:
        self._processing = active
        self.group_table.setEnabled(not active)
        self.target_period.setEnabled(not active and self.target_period.count() > 0)
        self.method.setEnabled(not active and self.group is not None)
        self.unit_conversion.setEnabled(
            not active
            and self.group is not None
            and automatic_unit_target(
                self.group.variable_id,
                self.group.units,
                self.group.standard_name,
            )
            is not None
        )
        self.resampling_method.setEnabled(
            not active and self.resampling_enabled.isChecked()
        )
        self.resampling_enabled.setEnabled(not active)
        self.target_grid.setEnabled(not active and self.resampling_enabled.isChecked())
        self.template_panel.setEnabled(not active)
        self.clipping_enabled.setEnabled(not active)
        self.region_panel.setEnabled(not active)
        self.process_button.setEnabled(not active and self.group is not None)
        self.stop_button.setEnabled(active and not self._cancel_event.is_set())
        self.scan_button.setEnabled(not active)


def _frequency_label(value: str) -> str:
    labels = {
        "mon": "月",
        "day": "日",
        "1hr": "1 小时",
        "3hr": "3 小时",
        "3hrPt": "3 小时瞬时值",
        "6hr": "6 小时",
        "6hrPt": "6 小时瞬时值",
        "subhrPt": "小时内瞬时值",
        "yr": "年",
    }
    return labels.get(value, value)


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "未知"


def _folder(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .")
    return cleaned or "未分类"
