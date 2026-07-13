from pathlib import Path
from threading import Event

import fiona
import numpy as np
import pandas as pd
import pytest
import rasterio
import xarray as xr
from shapely.geometry import box, mapping

from cmip_explorer.domain.errors import ExplorerError
from cmip_explorer.infrastructure.processing import (
    ResamplingOptions,
    aggregate_netcdf_to_geotiffs,
    inspect_vector_region,
    scan_downloaded_datasets,
)


def _write_dataset(
    path: Path,
    variable: str,
    times,
    values: np.ndarray,
    *,
    frequency: str,
    units: str,
    standard_name: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset = xr.Dataset(
        {
            variable: (
                ("time", "lat", "lon"),
                np.broadcast_to(values[:, None, None], (len(times), 2, 2)).astype("float32"),
                {"units": units, "standard_name": standard_name, "long_name": standard_name},
            )
        },
        coords={"time": times, "lat": [1.0, 0.0], "lon": [100.0, 101.0]},
        attrs={
            "variable_id": variable,
            "source_id": "TestModel",
            "experiment_id": "ssp245",
            "variant_label": "r1i1p1f1",
            "table_id": "Amon" if frequency == "mon" else frequency,
            "frequency": frequency,
            "grid_label": "gn",
        },
    )
    dataset.to_netcdf(path)


def test_scan_groups_many_compatible_monthly_files_and_annual_mean_is_weighted(
    tmp_path: Path,
) -> None:
    times = pd.date_range("2020-01-01", periods=12, freq="MS")
    values = 273.15 + np.arange(1, 13, dtype="float32")
    _write_dataset(
        tmp_path / "NetCDF" / "TestModel" / "ssp245" / "tas_first.nc",
        "tas",
        times[:6],
        values[:6],
        frequency="mon",
        units="K",
        standard_name="air_temperature",
    )
    _write_dataset(
        tmp_path / "NetCDF" / "TestModel" / "ssp245" / "tas_second.nc",
        "tas",
        times[6:],
        values[6:],
        frequency="mon",
        units="K",
        standard_name="air_temperature",
    )

    scan = scan_downloaded_datasets(tmp_path / "NetCDF")

    assert not scan.warnings
    assert len(scan.groups) == 1
    group = scan.groups[0]
    assert group.variable_id == "tas"
    assert group.frequency == "mon"
    assert len(group.paths) == 2
    outputs = aggregate_netcdf_to_geotiffs(
        group.paths,
        "tas",
        tmp_path / "annual",
        source_frequency=group.frequency,
        target_period="annual",
        statistic="auto",
    )
    expected = np.average(np.arange(1, 13), weights=times.days_in_month)
    assert len(outputs) == 1
    with rasterio.open(outputs[0]) as raster:
        assert np.allclose(raster.read(1), expected)
        assert raster.tags()["units"] == "degC"
        assert raster.tags()["statistic"] == "mean"
        assert raster.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"


def test_daily_temperature_is_aggregated_to_monthly_celsius(tmp_path: Path) -> None:
    times = pd.date_range("2020-01-01", periods=31, freq="D")
    values = 273.15 + np.arange(1, 32, dtype="float32")
    source = tmp_path / "tas_day.nc"
    _write_dataset(
        source,
        "tas",
        times,
        values,
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )

    outputs = aggregate_netcdf_to_geotiffs(
        [source],
        "tas",
        tmp_path / "monthly",
        source_frequency="day",
        target_period="monthly",
        statistic="auto",
    )

    with rasterio.open(outputs[0]) as raster:
        assert np.allclose(raster.read(1), 16.0)
        assert raster.tags()["period"] == "2020-01"
        assert raster.tags()["time_steps"] == "31"


def test_three_hourly_precipitation_is_integrated_to_monthly_millimetres(
    tmp_path: Path,
) -> None:
    times = pd.date_range("2020-01-01 01:30", periods=31 * 8, freq="3h")
    rate = np.full(len(times), 1e-5, dtype="float32")
    source = tmp_path / "pr_3hr.nc"
    _write_dataset(
        source,
        "pr",
        times,
        rate,
        frequency="3hr",
        units="kg m-2 s-1",
        standard_name="precipitation_flux",
    )

    outputs = aggregate_netcdf_to_geotiffs(
        [source],
        "pr",
        tmp_path / "monthly-pr",
        source_frequency="3hr",
        target_period="monthly",
        statistic="auto",
    )

    with rasterio.open(outputs[0]) as raster:
        assert np.allclose(raster.read(1), 1e-5 * 31 * 86_400, rtol=1e-6)
        assert raster.tags()["units"] == "mm"
        assert raster.tags()["statistic"] == "total"
        assert raster.tags()["time_steps"] == str(31 * 8)


def test_incomplete_three_hourly_month_is_rejected(tmp_path: Path) -> None:
    times = pd.date_range("2020-01-01 01:30", periods=31 * 8 - 1, freq="3h")
    source = tmp_path / "incomplete_pr_3hr.nc"
    _write_dataset(
        source,
        "pr",
        times,
        np.ones(len(times), dtype="float32"),
        frequency="3hr",
        units="kg m-2 s-1",
        standard_name="precipitation_flux",
    )

    with pytest.raises(ExplorerError, match="数据不完整"):
        aggregate_netcdf_to_geotiffs(
            [source],
            "pr",
            tmp_path / "rejected",
            source_frequency="3hr",
            target_period="monthly",
        )


def test_daily_temperature_can_be_exported_as_one_tif_per_day(tmp_path: Path) -> None:
    times = pd.date_range("2020-01-01", periods=2, freq="D")
    source = tmp_path / "tas_day.nc"
    _write_dataset(
        source,
        "tas",
        times,
        np.array([273.15, 283.15], dtype="float32"),
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )

    outputs = aggregate_netcdf_to_geotiffs(
        [source],
        "tas",
        tmp_path / "daily",
        source_frequency="day",
        target_period="daily",
        statistic="auto",
    )

    assert [path.name for path in outputs] == [
        "tas_2020_01_01_mean.tif",
        "tas_2020_01_02_mean.tif",
    ]
    with rasterio.open(outputs[0]) as first, rasterio.open(outputs[1]) as second:
        assert np.allclose(first.read(1), 0.0, atol=1e-4)
        assert np.allclose(second.read(1), 10.0, atol=1e-4)
        assert first.tags()["target_period"] == "daily"


def test_monthly_data_cannot_be_expanded_to_daily(tmp_path: Path) -> None:
    source = tmp_path / "tas_mon.nc"
    _write_dataset(
        source,
        "tas",
        pd.date_range("2020-01-01", periods=1, freq="MS"),
        np.array([280.0], dtype="float32"),
        frequency="mon",
        units="K",
        standard_name="air_temperature",
    )

    with pytest.raises(ExplorerError, match="不能生成比原始时间尺度更短"):
        aggregate_netcdf_to_geotiffs(
            [source],
            "tas",
            tmp_path / "invalid-daily",
            source_frequency="mon",
            target_period="daily",
        )


def test_daily_output_rejects_a_missing_whole_day(tmp_path: Path) -> None:
    source = tmp_path / "tas_day_gap.nc"
    _write_dataset(
        source,
        "tas",
        pd.to_datetime(["2020-01-01", "2020-01-03"]),
        np.array([280.0, 282.0], dtype="float32"),
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )

    with pytest.raises(ExplorerError, match="缺少 2020 年 1 月 2 日"):
        aggregate_netcdf_to_geotiffs(
            [source],
            "tas",
            tmp_path / "daily-gap",
            source_frequency="day",
            target_period="daily",
        )


def test_unit_conversion_can_be_disabled_for_temperature(tmp_path: Path) -> None:
    source = tmp_path / "tas_day.nc"
    _write_dataset(
        source,
        "tas",
        pd.date_range("2020-01-01", periods=1, freq="D"),
        np.array([280.0], dtype="float32"),
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )

    output = aggregate_netcdf_to_geotiffs(
        [source],
        "tas",
        tmp_path / "kelvin",
        source_frequency="day",
        target_period="daily",
        convert_units=False,
    )[0]

    with rasterio.open(output) as raster:
        assert np.allclose(raster.read(1), 280.0)
        assert raster.tags()["units"] == "K"
        assert raster.tags()["unit_conversion"] == "False"


def test_bilinear_resampling_uses_requested_degree_grid(tmp_path: Path) -> None:
    source = tmp_path / "tas_day.nc"
    _write_dataset(
        source,
        "tas",
        pd.date_range("2020-01-01", periods=1, freq="D"),
        np.array([280.0], dtype="float32"),
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )

    output = aggregate_netcdf_to_geotiffs(
        [source],
        "tas",
        tmp_path / "resampled",
        source_frequency="day",
        target_period="daily",
        resampling=ResamplingOptions(method="bilinear", target_resolution=0.5),
    )[0]

    with rasterio.open(output) as raster:
        assert (raster.width, raster.height) == (4, 4)
        assert raster.crs.to_string() == "EPSG:4326"
        assert raster.res == (0.5, 0.5)
        assert np.allclose(raster.read(1), 6.85, atol=1e-4)
        assert raster.tags()["resampling"] == "bilinear"


def test_template_resampling_copies_exact_grid(tmp_path: Path) -> None:
    source = tmp_path / "tas_day.nc"
    _write_dataset(
        source,
        "tas",
        pd.date_range("2020-01-01", periods=1, freq="D"),
        np.array([280.0], dtype="float32"),
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )
    template = tmp_path / "template.tif"
    with rasterio.open(
        template,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=rasterio.transform.from_origin(99.5, 1.5, 0.5, 0.5),
    ) as dataset:
        dataset.write(np.ones((4, 4), dtype="uint8"), 1)

    output = aggregate_netcdf_to_geotiffs(
        [source],
        "tas",
        tmp_path / "template-output",
        source_frequency="day",
        target_period="daily",
        resampling=ResamplingOptions(method="nearest", template_path=template),
    )[0]

    with rasterio.open(template) as expected, rasterio.open(output) as actual:
        assert actual.crs == expected.crs
        assert actual.transform == expected.transform
        assert (actual.width, actual.height) == (expected.width, expected.height)


def _write_region(path: Path, driver: str = "GeoJSON") -> None:
    schema = {"geometry": "Polygon", "properties": {"name": "str"}}
    with fiona.open(
        path,
        "w",
        driver=driver,
        crs="EPSG:4326",
        schema=schema,
    ) as collection:
        collection.write(
            {
                "geometry": mapping(box(99.5, -0.5, 100.5, 1.5)),
                "properties": {"name": "west-half"},
            }
        )


def test_resampling_then_vector_clipping_produces_only_final_cog(tmp_path: Path) -> None:
    source = tmp_path / "tas_day.nc"
    _write_dataset(
        source,
        "tas",
        pd.date_range("2020-01-01", periods=1, freq="D"),
        np.array([280.0], dtype="float32"),
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )
    region = tmp_path / "region.geojson"
    _write_region(region)
    output_dir = tmp_path / "clipped"

    output = aggregate_netcdf_to_geotiffs(
        [source],
        "tas",
        output_dir,
        source_frequency="day",
        target_period="daily",
        resampling=ResamplingOptions(method="bilinear", target_resolution=0.5),
        clip_path=region,
    )[0]

    with rasterio.open(output) as raster:
        assert raster.crs.to_string() == "EPSG:4326"
        assert raster.res == (0.5, 0.5)
        assert (raster.width, raster.height) == (2, 4)
        assert raster.tags()["resampling"] == "bilinear"
        assert raster.tags()["clipped"] == "true"
        assert np.allclose(raster.read(1), 6.85, atol=1e-4)
    assert not list(output_dir.glob(".cmip-working-*"))
    assert not list(output_dir.rglob("*.part"))


def test_shapefile_reader_loads_same_name_components(tmp_path: Path) -> None:
    region = tmp_path / "region.shp"
    _write_region(region, "ESRI Shapefile")

    info = inspect_vector_region(region)

    suffixes = {path.suffix.casefold() for path in info.companion_files}
    assert {".shp", ".shx", ".dbf", ".prj"}.issubset(suffixes)
    assert info.feature_count == 1
    assert info.layer_count == 1
    assert np.allclose(info.bounds_wgs84, (99.5, -0.5, 100.5, 1.5))


def test_processing_cancellation_stops_before_next_period_and_skips_manifest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tas_day.nc"
    _write_dataset(
        source,
        "tas",
        pd.date_range("2020-01-01", periods=3, freq="D"),
        np.array([280.0, 281.0, 282.0], dtype="float32"),
        frequency="day",
        units="K",
        standard_name="air_temperature",
    )
    cancelled = Event()
    output_dir = tmp_path / "cancelled"

    with pytest.raises(InterruptedError, match="已取消"):
        aggregate_netcdf_to_geotiffs(
            [source],
            "tas",
            output_dir,
            source_frequency="day",
            target_period="daily",
            progress=lambda current, _total, _name: cancelled.set() if current == 1 else None,
            cancelled=cancelled.is_set,
        )

    assert len(list(output_dir.glob("*.tif"))) == 1
    assert not (output_dir / "processing_manifest.json").exists()
