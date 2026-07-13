from pathlib import Path

import numpy as np
import pytest
import rasterio
import xarray as xr

from cmip_explorer.infrastructure.processing import (
    convert_local_netcdf_to_geotiffs,
    inspect_netcdf,
    process_local_netcdf,
)


def test_monthly_file_is_detected_and_selected_months_are_aggregated(tmp_path: Path) -> None:
    source = tmp_path / "tas_monthly.nc"
    times = np.array(
        ["2020-01-15", "2020-02-15", "2021-01-15", "2021-02-15"],
        dtype="datetime64[ns]",
    )
    values = np.array([1.0, 2.0, 3.0, 4.0], dtype="float32")[:, None, None]
    dataset = xr.Dataset(
        {"tas": (("time", "lat", "lon"), np.broadcast_to(values, (4, 2, 2)))},
        coords={"time": times, "lat": [1.0, 0.0], "lon": [100.0, 101.0]},
    )
    dataset["tas"].attrs.update({"long_name": "Near-Surface Air Temperature", "units": "K"})
    dataset.to_netcdf(source)

    info = inspect_netcdf([source])
    assert info.variables == ("tas",)
    assert info.monthly is True
    assert info.frequency_label == "月数据"
    assert info.can_aggregate_annually is True
    assert (info.start_year, info.end_year) == (2020, 2021)

    outputs = process_local_netcdf(
        [source], "tas", tmp_path / "tif", months=(1, 2), statistic="sum"
    )
    assert len(outputs) == 2
    with rasterio.open(outputs[0]) as raster:
        assert np.allclose(raster.read(1), 3.0)
        assert raster.crs.to_string() == "EPSG:4326"
        assert raster.tags()["selected_months"] == "1,2"
        assert raster.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
    with rasterio.open(outputs[1]) as raster:
        assert np.allclose(raster.read(1), 7.0)

    means = process_local_netcdf(
        [source], "tas", tmp_path / "mean", months=(1, 2), statistic="mean"
    )
    with rasterio.open(means[0]) as raster:
        assert np.allclose(raster.read(1), (1.0 * 31 + 2.0 * 29) / 60)
    with rasterio.open(means[1]) as raster:
        assert np.allclose(raster.read(1), (3.0 * 31 + 4.0 * 28) / 59)


@pytest.mark.parametrize(
    ("statistic", "expected"),
    (
        ("mean", (3.0, 3.0)),
        ("sum", (15.0, 12.0)),
        ("min", (1.0, 2.0)),
        ("max", (5.0, 4.0)),
        ("mode", (1.0, 2.0)),
    ),
)
def test_daily_file_supports_every_annual_statistic(
    tmp_path: Path, statistic: str, expected: tuple[float, float]
) -> None:
    source = tmp_path / "pr_daily.nc"
    times = np.array(
        [
            "2020-01-01",
            "2020-01-02",
            "2020-01-03",
            "2020-02-01",
            "2020-02-02",
            "2021-01-01",
            "2021-01-02",
            "2021-02-01",
            "2021-02-02",
        ],
        dtype="datetime64[ns]",
    )
    values = np.array([1, 1, 3, 5, 5, 2, 2, 4, 4], dtype="float32")[:, None, None]
    xr.Dataset(
        {"pr": (("time", "lat", "lon"), np.broadcast_to(values, (9, 2, 2)))},
        coords={"time": times, "lat": [1.0, 0.0], "lon": [100.0, 101.0]},
    ).to_netcdf(source)

    info = inspect_netcdf([source])
    assert info.frequency_label == "日数据"
    assert info.temporal_resolution == "daily"
    assert info.can_aggregate_annually is True

    outputs = process_local_netcdf(
        [source], "pr", tmp_path / statistic, months=(1, 2), statistic=statistic
    )
    assert len(outputs) == 2
    for output, value in zip(outputs, expected, strict=True):
        with rasterio.open(output) as raster:
            assert np.allclose(raster.read(1), value)
            assert raster.crs.to_epsg() == 4326
            assert raster.tags()["statistic"] == statistic


def test_subdaily_conversion_keeps_time_in_each_unique_filename(tmp_path: Path) -> None:
    source = tmp_path / "pr_3hr.nc"
    times = np.array(
        ["2020-01-01T01:30", "2020-01-01T04:30", "2020-01-01T07:30"],
        dtype="datetime64[ns]",
    )
    values = np.arange(12, dtype="float32").reshape(3, 2, 2)
    xr.Dataset(
        {"pr": (("time", "lat", "lon"), values)},
        coords={"time": times, "lat": [1.0, 0.0], "lon": [100.0, 101.0]},
    ).to_netcdf(source)

    outputs = convert_local_netcdf_to_geotiffs([source], tmp_path / "tif", "pr")

    assert [output.name for output in outputs] == [
        "pr_20200101T013000.tif",
        "pr_20200101T043000.tif",
        "pr_20200101T073000.tif",
    ]
    with rasterio.open(outputs[1]) as raster:
        assert np.allclose(raster.read(1), values[1])
        assert raster.tags()["source_time"] == "20200101T043000"
        assert raster.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"


def test_three_hourly_file_is_not_mislabeled_as_monthly(tmp_path: Path) -> None:
    source = tmp_path / "pr_3hr.nc"
    times = np.array(
        ["2020-01-01T01:30", "2020-01-01T04:30", "2020-01-01T07:30"],
        dtype="datetime64[ns]",
    )
    xr.Dataset(
        {
            "pr": (
                ("time", "lat", "lon"),
                np.ones((3, 2, 2), dtype="float32"),
            )
        },
        coords={"time": times, "lat": [1.0, 0.0], "lon": [100.0, 101.0]},
    ).to_netcdf(source)
    info = inspect_netcdf([source])
    assert info.monthly is False
    assert info.frequency_label == "小时级数据"
    assert info.can_aggregate_annually is False
