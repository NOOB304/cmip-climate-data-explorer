from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cmip_explorer.application import JobContext, WorkflowService
from cmip_explorer.domain.errors import ExplorerError
from cmip_explorer.domain.models import LogicalFile, Region, UserConfirmation
from cmip_explorer.infrastructure.processing import ProcessingOptions
from cmip_explorer.ui.async_runner import AsyncRunnable, SyncRunnable
from cmip_explorer.ui.dialogs import StrictSubsetFailureDialog
from cmip_explorer.ui.state import ApplicationState


@dataclass(slots=True)
class ExecutionState:
    job: JobContext
    files: list[LogicalFile]
    index: int
    inputs: list[Path]
    region: Region
    variable_id: str
    start_year: int
    end_year: int
    target_unit: str
    statistic: str
    regrid_resolution_degrees: float | None
    full_download_fallbacks: list[dict]
    batch_confirmation: UserConfirmation | None


class PlanPage(QWidget):
    def __init__(self, state: ApplicationState, workflow: WorkflowService) -> None:
        super().__init__()
        self.state = state
        self.workflow = workflow
        self.pool = QThreadPool.globalInstance()
        self._workers: set[object] = set()
        self.execution: ExecutionState | None = None
        self.preset_path = workflow.paths.data / "processing_plans.json"
        self._build_ui()
        state.files_changed.connect(self.refresh)
        state.region_changed.connect(self.refresh)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("处理计划")
        title.setObjectName("PageTitle")
        self.context = QLabel("尚未选择文件或研究区")
        self.context.setWordWrap(True)
        self.files = QTableWidget(0, 5)
        self.files.setHorizontalHeaderLabels(("文件", "模型", "场景", "成员", "年份"))
        form = QFormLayout()
        preset_row = QHBoxLayout()
        self.presets = QComboBox()
        load_preset = QPushButton("加载")
        load_preset.clicked.connect(self._load_preset)
        save_preset = QPushButton("保存")
        save_preset.clicked.connect(self._save_preset)
        preset_row.addWidget(self.presets, 1)
        preset_row.addWidget(load_preset)
        preset_row.addWidget(save_preset)
        self.variable = QLineEdit("tas")
        self.start_year = QSpinBox()
        self.start_year.setRange(1, 9999)
        self.start_year.setValue(2000)
        self.end_year = QSpinBox()
        self.end_year.setRange(1, 9999)
        self.end_year.setValue(2100)
        self.unit = QComboBox()
        self.unit.addItems(("degC", "K", "mm/day"))
        self.statistic = QComboBox()
        self.statistic.addItems(("mean", "sum", "min", "max"))
        self.regrid_resolution = QDoubleSpinBox()
        self.regrid_resolution.setRange(0.0, 5.0)
        self.regrid_resolution.setDecimals(3)
        self.regrid_resolution.setSingleStep(0.1)
        self.regrid_resolution.setSpecialValueText("自动")
        self.regrid_resolution.setSuffix("°")
        self.output = QLineEdit()
        browse = QPushButton("选择")
        browse.clicked.connect(self._browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(browse)
        form.addRow("处理方案", preset_row)
        form.addRow("变量", self.variable)
        form.addRow("开始年份", self.start_year)
        form.addRow("结束年份", self.end_year)
        form.addRow("目标单位", self.unit)
        form.addRow("年度统计", self.statistic)
        form.addRow("重采样分辨率", self.regrid_resolution)
        form.addRow("输出目录", output_row)
        self.run_button = QPushButton("执行严格区域任务")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.run)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("就绪")
        layout.addWidget(title)
        layout.addWidget(self.context)
        layout.addWidget(self.files, 1)
        layout.addLayout(form)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.run_button, 0)
        self._refresh_presets()

    def refresh(self) -> None:
        self.files.setRowCount(len(self.state.selected_files))
        for row, file in enumerate(self.state.selected_files):
            values = (
                file.filename,
                file.source_id or "-",
                file.experiment_id or "-",
                file.member_id or "-",
                f"{file.temporal.start or '?'}–{file.temporal.end or '?'}",
            )
            for column, value in enumerate(values):
                self.files.setItem(row, column, QTableWidgetItem(value))
        region = self.state.region.name if self.state.region else "未选择"
        self.context.setText(f"已选逻辑文件: {len(self.state.selected_files)}；研究区: {region}")

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.output.setText(path)

    def _read_presets(self) -> dict[str, dict]:
        try:
            payload = json.loads(self.preset_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _refresh_presets(self) -> None:
        self.presets.clear()
        self.presets.addItem("选择处理方案", None)
        for name in sorted(self._read_presets(), key=str.casefold):
            self.presets.addItem(name, name)

    def _save_preset(self) -> None:
        name, accepted = QInputDialog.getText(self, "保存处理方案", "方案名称")
        if not accepted or not name.strip():
            return
        presets = self._read_presets()
        presets[name.strip()] = {
            "variable_id": self.variable.text().strip(),
            "start_year": self.start_year.value(),
            "end_year": self.end_year.value(),
            "target_unit": self.unit.currentText(),
            "statistic": self.statistic.currentText(),
            "regrid_resolution_degrees": self.regrid_resolution.value(),
            "output": self.output.text(),
        }
        self.preset_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.preset_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.preset_path)
        self._refresh_presets()
        self.state.message.emit(f"已保存处理方案: {name.strip()}")

    def _load_preset(self) -> None:
        name = self.presets.currentData()
        payload = self._read_presets().get(name) if name else None
        if not payload:
            return
        self.variable.setText(str(payload.get("variable_id", "tas")))
        self.start_year.setValue(int(payload.get("start_year", 2000)))
        self.end_year.setValue(int(payload.get("end_year", 2100)))
        for widget, value in (
            (self.unit, payload.get("target_unit", "degC")),
            (self.statistic, payload.get("statistic", "mean")),
        ):
            index = widget.findText(str(value))
            if index >= 0:
                widget.setCurrentIndex(index)
        self.regrid_resolution.setValue(float(payload.get("regrid_resolution_degrees", 0.0)))
        self.output.setText(str(payload.get("output", "")))

    def run(self) -> None:
        if not self.state.selected_files or self.state.region is None:
            QMessageBox.warning(self, "处理计划", "请先选择文件并导入研究区。")
            return
        if self.start_year.value() > self.end_year.value():
            QMessageBox.warning(self, "处理计划", "开始年份不能晚于结束年份。")
            return
        output = Path(self.output.text()) if self.output.text() else self.workflow.paths.outputs
        plan = {
            "variable_id": self.variable.text().strip(),
            "start_year": self.start_year.value(),
            "end_year": self.end_year.value(),
            "unit": self.unit.currentText(),
            "statistic": self.statistic.currentText(),
            "regrid_resolution_degrees": self.regrid_resolution.value() or None,
            "region_id": str(self.state.region.id),
            "files": [file.logical_key for file in self.state.selected_files],
            "output": str(output),
        }
        job = self.workflow.create_job(
            f"{plan['variable_id']} {plan['start_year']}-{plan['end_year']}", plan
        )
        self.execution = ExecutionState(
            job=job,
            files=list(self.state.selected_files),
            index=0,
            inputs=[],
            region=self.state.region,
            variable_id=plan["variable_id"],
            start_year=plan["start_year"],
            end_year=plan["end_year"],
            target_unit=plan["unit"],
            statistic=plan["statistic"],
            regrid_resolution_degrees=plan["regrid_resolution_degrees"],
            full_download_fallbacks=[],
            batch_confirmation=None,
        )
        self._output_dir = output / str(job.id)
        self.run_button.setEnabled(False)
        self.progress.setValue(0)
        self._run_next_file()

    def _run_next_file(self) -> None:
        assert self.execution is not None
        if self.execution.index >= len(self.execution.files):
            self._run_processing()
            return
        file = self.execution.files[self.execution.index]
        self.status.setText(f"严格子集: {file.filename}")

        async def subset():
            return await self.workflow.strict_subset(
                self.execution.job,
                file,
                self.execution.region,
                self.execution.variable_id,
                self.execution.start_year,
                self.execution.end_year,
            )

        worker = AsyncRunnable(subset)
        self._workers.add(worker)
        worker.signals.result.connect(lambda result: self._subset_done(result.path))
        worker.signals.error.connect(lambda _trace, error: self._subset_failed(file, error))
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        self.pool.start(worker)

    def _subset_done(self, path: Path) -> None:
        assert self.execution is not None
        self.execution.inputs.append(path)
        self.execution.index += 1
        self.progress.setValue(int(self.execution.index / len(self.execution.files) * 70))
        self._run_next_file()

    def _subset_failed(self, file: LogicalFile, error: object) -> None:
        if not isinstance(error, ExplorerError):
            QMessageBox.critical(self, "严格子集失败", str(error))
            self._finish(False)
            return
        assert self.execution is not None
        confirmation = self.execution.batch_confirmation
        if confirmation is None:
            free = shutil.disk_usage(self.execution.job.root).free
            dialog = StrictSubsetFailureDialog(file, error, free, self)
            if dialog.exec() != StrictSubsetFailureDialog.DOWNLOAD_FULL:
                self.status.setText("任务已取消；未创建完整下载")
                self._finish(False)
                return
            confirmation = self.workflow.confirm_full_download(
                self.execution.job,
                file,
                error,
                file.size_bytes or 0,
                scope=dialog.confirmation_scope(),
            )
            if confirmation.scope.value == "job_remainder":
                self.execution.batch_confirmation = confirmation
        self.execution.full_download_fallbacks.append(
            {
                "file_key": file.logical_key,
                "filename": file.filename,
                "confirmation_id": str(confirmation.id),
                "confirmed_at": confirmation.confirmed_at.isoformat(),
                "scope": confirmation.scope.value,
                "estimated_bytes": confirmation.estimated_bytes,
                "failure_code": error.code.value,
                "failure_snapshot": error.details,
                "plan_hash": confirmation.plan_hash,
            }
        )
        self.status.setText(f"已确认当前文件完整下载: {file.filename}")

        async def full_download():
            return await self.workflow.full_download(self.execution.job, file, confirmation)

        worker = AsyncRunnable(full_download)
        self._workers.add(worker)
        worker.signals.result.connect(self._subset_done)
        worker.signals.error.connect(lambda trace, exc: self._fatal_error(trace, exc))
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        self.pool.start(worker)

    def _run_processing(self) -> None:
        assert self.execution is not None
        first = self.execution.files[0]
        options = ProcessingOptions(
            variable_id=self.execution.variable_id,
            start_year=self.execution.start_year,
            end_year=self.execution.end_year,
            target_unit=self.execution.target_unit,
            statistic=self.execution.statistic,
            source_id=first.source_id or "unknown-model",
            experiment_id="-".join(
                sorted({item.experiment_id or "unknown" for item in self.execution.files})
            ),
            member_id=first.member_id or "unknown-member",
            grid_label=first.grid_label or "unknown-grid",
            region_name=self.execution.region.name,
            regrid_resolution_degrees=self.execution.regrid_resolution_degrees,
        )
        self.status.setText("时间合成、单位转换和 GeoTIFF 输出")

        def process():
            return self.workflow.process(
                self.execution.job,
                self.execution.inputs,
                self.execution.region,
                self._output_dir,
                options,
                provenance={
                    "strict_mode": not bool(self.execution.full_download_fallbacks),
                    "full_download_fallbacks": self.execution.full_download_fallbacks,
                },
            )

        worker = SyncRunnable(process)
        self._workers.add(worker)
        worker.signals.result.connect(self._processing_done)
        worker.signals.error.connect(lambda trace, exc: self._fatal_error(trace, exc))
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        self.pool.start(worker)

    def _processing_done(self, result: object) -> None:
        self.progress.setValue(100)
        self.status.setText(f"完成: {len(result.artifacts)} 个栅格；manifest={result.manifest}")
        QMessageBox.information(self, "处理完成", self.status.text())
        self._finish(True)

    def _fatal_error(self, trace: str, error: object) -> None:
        QMessageBox.critical(self, "任务失败", f"{error}\n\n{trace[-1800:]}")
        self._finish(False)

    def _finish(self, _success: bool) -> None:
        self.run_button.setEnabled(True)
