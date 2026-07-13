from __future__ import annotations

import json
import os
import sqlite3
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr

from cmip_explorer.config import AppPaths
from cmip_explorer.infrastructure.persistence import Database
from cmip_explorer.infrastructure.processing import (
    ProcessingOptions,
    ResamplingOptions,
    aggregate_netcdf_to_geotiffs,
    convert_local_netcdf_to_geotiffs,
    process_local_netcdf,
    process_to_geotiffs,
)
from cmip_explorer.infrastructure.region import RegionImporter


def run_self_test(output_dir: Path, paths: AppPaths, database: Database) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "self-test-report.json"
    time = pd.date_range("2001-01-01", periods=12, freq="MS")
    source = output_dir / "self-test.nc"
    dataset = xr.Dataset(
        {
            "tas": (
                ("time", "lat", "lon"),
                np.full((12, 3, 4), 278.15, dtype="float32"),
                {"units": "K", "cell_methods": "time: mean"},
            )
        },
        coords={"time": time, "lat": [28.0, 27.0, 26.0], "lon": [105, 106, 107, 108]},
    )
    dataset.to_netcdf(source, engine="h5netcdf")
    geojson = output_dir / "self-test-region.geojson"
    geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "self-test"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [105.5, 25.5],
                                    [107.5, 25.5],
                                    [107.5, 28.5],
                                    [105.5, 28.5],
                                    [105.5, 25.5],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    region = RegionImporter().import_region(geojson).region
    result = process_to_geotiffs(
        [source],
        region.geometry_wkb_hex,
        output_dir / "outputs",
        ProcessingOptions(
            variable_id="tas",
            start_year=2001,
            end_year=2001,
            target_unit="degC",
            source_id="self-test",
            experiment_id="historical",
            member_id="r1i1p1f1",
            grid_label="gn",
            region_name="self-test",
        ),
        provenance={"diagnostic": True},
    )
    with rasterio.open(result.artifacts[0]) as raster:
        cog = {
            "layout": raster.tags(ns="IMAGE_STRUCTURE").get("LAYOUT"),
            "epsg": raster.crs.to_epsg() if raster.crs else None,
            "width": raster.width,
            "height": raster.height,
        }
    local_processing = _test_local_processing(output_dir)
    with sqlite3.connect(paths.catalog) as catalog:
        variable_count = catalog.execute("SELECT count(*) FROM variable_definitions").fetchone()[0]
    with database.engine.connect() as connection:
        migration = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    report = {
        "status": "passed",
        "database_revision": migration,
        "variable_count": variable_count,
        "region_bbox": region.bbox,
        "netcdf_bytes": source.stat().st_size,
        "cog": cog,
        "local_processing": local_processing,
        "manifest": str(result.manifest.resolve()),
        "font_resource": files("cmip_explorer.resources")
        .joinpath("fonts/NotoSansCJKsc-Regular.otf")
        .is_file(),
    }
    temporary = report_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, report_path)
    return report_path


def _test_local_processing(output_dir: Path) -> dict[str, object]:
    latitudes = [1.0, 0.0]
    longitudes = [100.0, 101.0]
    monthly_time = pd.date_range("2020-01-01", periods=12, freq="MS")
    monthly_values = np.broadcast_to(
        np.arange(1, 13, dtype="float32")[:, None, None], (12, 2, 2)
    )
    monthly_source = output_dir / "self-test-monthly.nc"
    xr.Dataset(
        {"tas": (("time", "lat", "lon"), monthly_values, {"units": "K"})},
        coords={"time": monthly_time, "lat": latitudes, "lon": longitudes},
    ).to_netcdf(monthly_source, engine="h5netcdf")
    monthly_outputs = process_local_netcdf(
        [monthly_source], "tas", output_dir / "local-monthly", statistic="mean"
    )
    expected_monthly = float(
        np.average(np.arange(1, 13), weights=monthly_time.days_in_month)
    )
    with rasterio.open(monthly_outputs[0]) as raster:
        observed_monthly = float(raster.read(1)[0, 0])
        monthly_cog = raster.tags(ns="IMAGE_STRUCTURE").get("LAYOUT")
    if not np.isclose(observed_monthly, expected_monthly):
        raise RuntimeError("monthly annual-mean self-test produced an incorrect value")
    grouped_outputs = aggregate_netcdf_to_geotiffs(
        [monthly_source],
        "tas",
        output_dir / "grouped-annual",
        source_frequency="mon",
        target_period="annual",
        statistic="auto",
    )
    with rasterio.open(grouped_outputs[0]) as raster:
        grouped_annual = float(raster.read(1)[0, 0])
        grouped_units = raster.tags().get("units")
    if not np.isclose(grouped_annual, expected_monthly - 273.15):
        raise RuntimeError("grouped annual self-test produced an incorrect value")
    if grouped_units != "degC":
        raise RuntimeError("grouped annual self-test did not convert temperature to Celsius")
    clip_region = output_dir / "self-test-local-region.geojson"
    clip_region.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [99.5, -0.5],
                                    [100.5, -0.5],
                                    [100.5, 1.5],
                                    [99.5, 1.5],
                                    [99.5, -0.5],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    clipped_outputs = aggregate_netcdf_to_geotiffs(
        [monthly_source],
        "tas",
        output_dir / "grouped-clipped",
        source_frequency="mon",
        target_period="annual",
        resampling=ResamplingOptions(method="bilinear", target_resolution=0.5),
        clip_path=clip_region,
    )
    with rasterio.open(clipped_outputs[0]) as raster:
        clipped_size = (raster.width, raster.height)
        clipped_resolution = raster.res
        clipped_tag = raster.tags().get("clipped")
    if clipped_size != (2, 4) or clipped_resolution != (0.5, 0.5):
        raise RuntimeError("resampling and clipping self-test produced the wrong grid")
    if clipped_tag != "true":
        raise RuntimeError("resampling and clipping self-test did not preserve metadata")

    daily_time = pd.to_datetime(
        ["2020-01-01", "2020-01-02", "2020-01-03", "2020-02-01", "2020-02-02"]
    )
    daily_values = np.broadcast_to(
        np.array([1, 1, 3, 5, 5], dtype="float32")[:, None, None], (5, 2, 2)
    )
    daily_source = output_dir / "self-test-daily.nc"
    xr.Dataset(
        {"pr": (("time", "lat", "lon"), daily_values, {"units": "mm/day"})},
        coords={"time": daily_time, "lat": latitudes, "lon": longitudes},
    ).to_netcdf(daily_source, engine="h5netcdf")
    daily_outputs = process_local_netcdf(
        [daily_source], "pr", output_dir / "local-daily", statistic="sum"
    )
    with rasterio.open(daily_outputs[0]) as raster:
        observed_daily = float(raster.read(1)[0, 0])
    if not np.isclose(observed_daily, 15.0):
        raise RuntimeError("daily annual-sum self-test produced an incorrect value")

    subdaily_time = pd.to_datetime(["2020-01-01 01:30", "2020-01-01 04:30"])
    subdaily_source = output_dir / "self-test-subdaily.nc"
    xr.Dataset(
        {"pr": (("time", "lat", "lon"), np.ones((2, 2, 2), dtype="float32"))},
        coords={"time": subdaily_time, "lat": latitudes, "lon": longitudes},
    ).to_netcdf(subdaily_source, engine="h5netcdf")
    frame_outputs = convert_local_netcdf_to_geotiffs(
        [subdaily_source], output_dir / "local-frames", "pr"
    )
    frame_names = [path.name for path in frame_outputs]
    expected_names = ["pr_20200101T013000.tif", "pr_20200101T043000.tif"]
    if frame_names != expected_names:
        raise RuntimeError("subdaily TIF self-test did not preserve unique times")

    return {
        "monthly_annual_mean": observed_monthly,
        "monthly_expected": expected_monthly,
        "monthly_cog_layout": monthly_cog,
        "grouped_annual_celsius": grouped_annual,
        "resampled_clipped_grid": [*clipped_size, *clipped_resolution],
        "daily_annual_sum": observed_daily,
        "subdaily_frames": frame_names,
    }
