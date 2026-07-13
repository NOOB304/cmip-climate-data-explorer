from pathlib import Path

import fiona
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from PySide6.QtWidgets import QFileDialog, QMessageBox
from shapely.geometry import box, mapping

from cmip_explorer.ui.pages.processing import ProcessingPage
from cmip_explorer.ui.state import ApplicationState


def _write_downloaded(
    root: Path,
    filename: str,
    variable: str,
    times,
    values: np.ndarray,
    frequency: str,
    units: str,
) -> Path:
    path = root / "NetCDF" / "TestModel" / "ssp245" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    xr.Dataset(
        {
            variable: (
                ("time", "lat", "lon"),
                np.broadcast_to(values[:, None, None], (len(times), 2, 2)).astype("float32"),
                {
                    "units": units,
                    "standard_name": (
                        "air_temperature"
                        if variable.startswith("ta")
                        else "precipitation_flux"
                    ),
                },
            )
        },
        coords={"time": times, "lat": [1.0, 0.0], "lon": [100.0, 101.0]},
        attrs={
            "source_id": "TestModel",
            "experiment_id": "ssp245",
            "variant_label": "r1i1p1f1",
            "table_id": frequency,
            "frequency": frequency,
            "grid_label": "gn",
        },
    ).to_netcdf(path)
    return path


def test_downloaded_daily_group_runs_annual_composition_from_processing_page(
    qtbot, tmp_path: Path
) -> None:
    times = pd.date_range("2020-01-01", "2020-12-31", freq="D")
    values = np.full(len(times), 280.0, dtype="float32")
    values[100] = 300.0
    _write_downloaded(
        tmp_path, "tasmax_day.nc", "tasmax", times, values, "day", "K"
    )

    page = ProcessingPage(ApplicationState(), tmp_path)
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: len(page.groups) == 1, timeout=15_000)

    assert page.group_table.rowCount() == 1
    assert page.group.variable_id == "tasmax"
    page.target_period.setCurrentIndex(page.target_period.findData("annual"))
    page.output.setText(str(tmp_path / "annual"))

    with qtbot.waitSignal(page.processed, timeout=15_000):
        page._process()

    output = tmp_path / "annual" / "tasmax_2020_max.tif"
    assert output.exists()
    with rasterio.open(output) as raster:
        assert np.allclose(raster.read(1), 26.85, atol=1e-4)
        assert raster.tags()["units"] == "degC"
    assert page.progress.value() == 100


def test_downloaded_three_hourly_group_runs_monthly_precipitation_total(
    qtbot, tmp_path: Path
) -> None:
    times = pd.date_range("2020-01-01 01:30", periods=31 * 8, freq="3h")
    values = np.full(len(times), 1e-5, dtype="float32")
    _write_downloaded(
        tmp_path,
        "pr_3hr.nc",
        "pr",
        times,
        values,
        "3hr",
        "kg m-2 s-1",
    )

    page = ProcessingPage(ApplicationState(), tmp_path)
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: len(page.groups) == 1, timeout=15_000)

    assert page.group.frequency == "3hr"
    assert page.target_period.currentData() == "monthly"
    assert page.method.currentData() == "auto"
    page.output.setText(str(tmp_path / "monthly"))

    with qtbot.waitSignal(page.processed, timeout=15_000):
        page._process()

    output = tmp_path / "monthly" / "pr_2020_01_total.tif"
    assert output.exists()
    with rasterio.open(output) as raster:
        assert np.allclose(raster.read(1), 26.784, rtol=1e-6)
        assert raster.tags()["units"] == "mm"
    assert "生成 1 个 TIF" in page.progress_text.text()


def test_selecting_another_group_resets_progress_and_updates_output_path(
    qtbot, tmp_path: Path
) -> None:
    daily_times = pd.date_range("2020-01-01", periods=2, freq="D")
    monthly_times = pd.date_range("2020-01-01", periods=2, freq="MS")
    _write_downloaded(
        tmp_path,
        "tas_day.nc",
        "tas",
        daily_times,
        np.full(2, 280.0, dtype="float32"),
        "day",
        "K",
    )
    _write_downloaded(
        tmp_path,
        "pr_mon.nc",
        "pr",
        monthly_times,
        np.full(2, 1.0, dtype="float32"),
        "mon",
        "mm",
    )

    page = ProcessingPage(ApplicationState(), tmp_path)
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: len(page.groups) == 2, timeout=15_000)
    page.progress.setValue(100)
    page.progress_text.setText("处理完成")

    next_row = 1 if page.group_table.currentRow() == 0 else 0
    expected = page.groups[next_row]
    page.group_table.selectRow(next_row)

    assert page.progress.value() == 0
    assert page.progress_text.text() == "就绪"
    assert expected.variable_id in page.output.text()
    assert expected.frequency in page.output.text()
    available = [page.target_period.itemData(i) for i in range(page.target_period.count())]
    if expected.temporal_resolution == "monthly":
        assert available == ["monthly", "annual"]
    else:
        assert available == ["daily", "monthly", "annual"]
    assert page.unit_conversion.isEnabled()


def test_processing_months_are_collapsed_and_refresh_removes_deleted_data(
    qtbot, tmp_path: Path
) -> None:
    path = _write_downloaded(
        tmp_path,
        "tas_day.nc",
        "tas",
        pd.date_range("2020-01-01", periods=2, freq="D"),
        np.full(2, 280.0, dtype="float32"),
        "day",
        "K",
    )
    page = ProcessingPage(ApplicationState(), tmp_path)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: len(page.groups) == 1, timeout=15_000)

    assert page.month_panel.isVisible()
    assert not page.month_body.isVisible()
    assert page.month_toggle.text().startswith("处理月份")
    assert page.month_toggle.text().endswith("已选 12 个月")

    path.unlink()
    page.refresh_data()
    qtbot.waitUntil(
        lambda: not page.groups and "没有可处理" in page.detected.text(),
        timeout=15_000,
    )

    assert page.group_table.rowCount() == 0
    assert not page.variable.isEnabled()
    assert "没有可处理" in page.detected.text()


def test_resampling_and_vector_upload_controls_are_directly_usable(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    _write_downloaded(
        tmp_path,
        "tas_day.nc",
        "tas",
        pd.date_range("2020-01-01", periods=2, freq="D"),
        np.full(2, 280.0, dtype="float32"),
        "day",
        "K",
    )
    template = tmp_path / "template.tif"
    with rasterio.open(
        template,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=rasterio.transform.from_origin(99.5, 1.5, 1.0, 1.0),
    ) as raster:
        raster.write(np.ones((2, 2), dtype="float32"), 1)
    region = tmp_path / "region.geojson"
    with fiona.open(
        region,
        "w",
        driver="GeoJSON",
        crs="EPSG:4326",
        schema={"geometry": "Polygon", "properties": {}},
    ) as collection:
        collection.write({"geometry": mapping(box(99.5, -0.5, 100.5, 1.5)), "properties": {}})

    page = ProcessingPage(ApplicationState(), tmp_path)
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: len(page.groups) == 1, timeout=15_000)
    assert not page.resampling_enabled.isChecked()
    assert not page.target_grid.isEnabled()

    page.resampling_enabled.setChecked(True)
    assert page.target_grid.isEnabled()
    assert page.resampling_method.isEnabled()

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(template), "GeoTIFF"),
    )
    page._choose_template()
    assert page.resampling_enabled.isChecked()
    assert page.target_grid.currentData() == "template"
    assert page._template_path == template

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(region), "Vector"),
    )
    page._choose_region()
    assert page.clipping_enabled.isChecked()
    assert page._clip_path == region
    assert "要素: 1" in page.region.toolTip()


def test_stopping_can_delete_only_files_created_by_current_run(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    page = ProcessingPage(ApplicationState(), tmp_path)
    qtbot.addWidget(page)
    output = tmp_path / "Processed" / "run"
    output.mkdir(parents=True)
    existing = output / "existing.tif"
    generated = output / "generated.tif"
    existing.write_bytes(b"old")
    page._run_output_dir = output
    page._run_existing_files = {existing.resolve()}
    page._run_manifest_existed = False
    page._processing = True
    generated.write_bytes(b"new")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    page._handle_cancelled()

    assert existing.exists()
    assert not generated.exists()
    assert "已删除本次生成的 1 个 TIF" in page.progress_text.text()
