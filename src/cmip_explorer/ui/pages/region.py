from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer.infrastructure.persistence import TaskRepository
from cmip_explorer.infrastructure.region import RegionImporter
from cmip_explorer.ui.state import ApplicationState
from cmip_explorer.ui.widgets import RegionPreview


class RegionPage(QWidget):
    def __init__(self, state: ApplicationState, repository: TaskRepository) -> None:
        super().__init__()
        self.state = state
        self.repository = repository
        self.importer = RegionImporter()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("研究区")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        saved_row = QHBoxLayout()
        self.saved_regions = QComboBox()
        load_saved = QPushButton("加载已保存研究区")
        load_saved.clicked.connect(self._load_saved_region)
        saved_row.addWidget(self.saved_regions, 1)
        saved_row.addWidget(load_saved)
        layout.addLayout(saved_row)
        self._refresh_saved_regions()
        source_row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("Shapefile、ZIP、GeoPackage 或 GeoJSON")
        browse = QPushButton("浏览")
        inspect = QPushButton("读取图层")
        browse.clicked.connect(self._browse)
        inspect.clicked.connect(self._inspect)
        source_row.addWidget(self.path, 1)
        source_row.addWidget(browse)
        source_row.addWidget(inspect)
        layout.addLayout(source_row)
        form = QFormLayout()
        self.layer = QComboBox()
        self.layer.currentIndexChanged.connect(self._load_features)
        self.crs = QLineEdit()
        self.crs.setPlaceholderText("仅在数据缺少 CRS 时填写，例如 EPSG:4326")
        self.name = QLineEdit()
        self.name.setPlaceholderText("研究区名称")
        form.addRow("图层", self.layer)
        form.addRow("CRS 覆盖", self.crs)
        form.addRow("名称", self.name)
        layout.addLayout(form)
        selection_row = QHBoxLayout()
        self.property_field = QComboBox()
        self.property_field.currentIndexChanged.connect(self._refresh_feature_labels)
        self.property_filter = QLineEdit()
        self.property_filter.setPlaceholderText("筛选属性值")
        self.property_filter.textChanged.connect(self._filter_features)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_features(True))
        clear_all = QPushButton("清空")
        clear_all.clicked.connect(lambda: self._set_all_features(False))
        selection_row.addWidget(QLabel("属性字段"))
        selection_row.addWidget(self.property_field)
        selection_row.addWidget(self.property_filter, 1)
        selection_row.addWidget(select_all)
        selection_row.addWidget(clear_all)
        layout.addLayout(selection_row)
        self.feature_list = QListWidget()
        self.feature_list.setMaximumHeight(180)
        layout.addWidget(self.feature_list)
        import_button = QPushButton("验证并导入研究区")
        import_button.setObjectName("PrimaryButton")
        import_button.clicked.connect(self._import)
        layout.addWidget(import_button, 0)
        self.status = QLabel("请选择研究区文件")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #617178;")
        layout.addWidget(self.status)
        self.preview = RegionPreview()
        layout.addWidget(self.preview, 1)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择研究区",
            "",
            "研究区 (*.shp *.zip *.gpkg *.geojson *.json)",
        )
        if path:
            self.path.setText(path)
            self._inspect()

    def _inspect(self) -> None:
        try:
            layers = self.importer.list_layers(Path(self.path.text()))
        except Exception as exc:
            QMessageBox.critical(self, "研究区读取失败", str(exc))
            return
        self.layer.clear()
        for layer in layers:
            self.layer.addItem(
                f"{layer.name} · {layer.geometry_type} · {layer.feature_count} 要素", layer.name
            )
        self._load_features()
        self.status.setText(f"检测到 {len(layers)} 个图层。")

    def _load_features(self) -> None:
        if not self.path.text() or self.layer.currentData() is None:
            return
        try:
            features = self.importer.list_features(Path(self.path.text()), self.layer.currentData())
        except Exception as exc:
            self.status.setText(f"要素读取失败: {exc}")
            return
        self._features = features
        fields = sorted(
            {key for feature in features for key in feature.properties}, key=str.casefold
        )
        self.property_field.blockSignals(True)
        self.property_field.clear()
        self.property_field.addItem("要素 ID", None)
        for field in fields:
            self.property_field.addItem(field, field)
        self.property_field.blockSignals(False)
        self._refresh_feature_labels()

    def _refresh_feature_labels(self) -> None:
        features = getattr(self, "_features", ())
        field = self.property_field.currentData()
        self.feature_list.clear()
        for feature in features:
            value = feature.properties.get(field) if field else feature.id
            item = QListWidgetItem(f"{value}  [ID {feature.id}]")
            item.setData(Qt.ItemDataRole.UserRole, feature.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.feature_list.addItem(item)
        self._filter_features(self.property_filter.text())

    def _filter_features(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.feature_list.count()):
            item = self.feature_list.item(index)
            item.setHidden(bool(query and query not in item.text().casefold()))

    def _set_all_features(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.feature_list.count()):
            item = self.feature_list.item(index)
            if not item.isHidden():
                item.setCheckState(state)

    def _import(self) -> None:
        selected_ids = {
            str(item.data(Qt.ItemDataRole.UserRole))
            for index in range(self.feature_list.count())
            if (item := self.feature_list.item(index)).checkState() == Qt.CheckState.Checked
        }
        if self.feature_list.count() and not selected_ids:
            QMessageBox.warning(self, "研究区验证", "至少选择一个要素。")
            return
        try:
            result = self.importer.import_region(
                Path(self.path.text()),
                layer=self.layer.currentData(),
                selected_feature_ids=selected_ids if self.feature_list.count() else None,
                source_crs_override=self.crs.text().strip() or None,
                name=self.name.text().strip() or None,
            )
        except Exception as exc:
            QMessageBox.critical(self, "研究区验证失败", str(exc))
            return
        self.state.set_region(result.region)
        self.repository.save_region(result.region)
        self._refresh_saved_regions()
        self.preview.set_geometry(result.region.geometry_wkb_hex)
        warnings = f"；警告: {'; '.join(result.warnings)}" if result.warnings else ""
        source = f"已导入 {result.region.name}，bbox={result.region.bbox}"
        self.status.setText(f"{source}，CRS={result.region.source_crs}{warnings}")

    def _refresh_saved_regions(self) -> None:
        self.saved_regions.clear()
        self.saved_regions.addItem("选择已保存研究区", None)
        for region in self.repository.list_regions():
            self.saved_regions.addItem(region.name, region)

    def _load_saved_region(self) -> None:
        region = self.saved_regions.currentData()
        if region is None:
            return
        self.state.set_region(region)
        self.preview.set_geometry(region.geometry_wkb_hex)
        self.status.setText(f"已加载 {region.name}，bbox={region.bbox}")
