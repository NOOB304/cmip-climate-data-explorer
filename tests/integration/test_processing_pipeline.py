import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from shapely.geometry import box

from cmip_explorer.infrastructure.processing import ProcessingOptions, process_to_geotiffs


def _write_year(path: Path, year: int, offset: float) -> None:
    time = pd.date_range(f"{year}-01-01", periods=12, freq="MS")
    values = np.full((12, 3, 4), 273.15 + offset, dtype="float32")
    dataset = xr.Dataset(
        {"tas": (("time", "lat", "lon"), values, {"units": "K"})},
        coords={
            "time": time,
            "lat": ("lat", [28.0, 27.0, 26.0], {"standard_name": "latitude"}),
            "lon": ("lon", [105.0, 106.0, 107.0, 108.0], {"standard_name": "longitude"}),
        },
    )
    dataset.to_netcdf(path, engine="h5netcdf")


def test_pipeline_creates_one_masked_cog_per_year_and_manifest(tmp_path: Path) -> None:
    historical = tmp_path / "historical.nc"
    scenario = tmp_path / "ssp245.nc"
    _write_year(historical, 2000, 1.0)
    _write_year(scenario, 2001, 2.0)
    region = box(105.5, 26.5, 107.5, 28.5)
    result = process_to_geotiffs(
        [historical, scenario],
        region.wkb_hex,
        tmp_path / "outputs",
        ProcessingOptions(
            variable_id="tas",
            start_year=2000,
            end_year=2001,
            target_unit="degC",
            source_id="BCC-CSM2-MR",
            experiment_id="historical-ssp245",
            member_id="r1i1p1f1",
            grid_label="gn",
            region_name="Guizhou-test",
        ),
    )
    assert len(result.artifacts) == 2
    assert result.manifest.exists()
    with rasterio.open(result.artifacts[0]) as raster:
        values = raster.read(1)
        assert raster.crs.to_epsg() == 4326
        assert np.isclose(values[0, 1], 1.0)
        assert raster.nodata in values


def test_pipeline_regrids_curvilinear_coordinates(tmp_path: Path) -> None:
    source = tmp_path / "curvilinear.nc"
    time = pd.date_range("2015-01-01", periods=12, freq="MS")
    y, x = np.mgrid[0:4, 0:4]
    latitudes = 21.5 + y + x * 0.05
    longitudes = 102.5 + x + y * 0.05
    dataset = xr.Dataset(
        {"tas": (("time", "j", "i"), np.full((12, 4, 4), 278.15), {"units": "K"})},
        coords={
            "time": time,
            "lat": (("j", "i"), latitudes, {"standard_name": "latitude"}),
            "lon": (("j", "i"), longitudes, {"standard_name": "longitude"}),
        },
    )
    dataset.to_netcdf(source, engine="h5netcdf")
    result = process_to_geotiffs(
        [source],
        box(102.0, 21.0, 106.0, 25.0).wkb_hex,
        tmp_path / "curvilinear-output",
        ProcessingOptions(
            variable_id="tas",
            start_year=2015,
            end_year=2015,
            target_unit="degC",
            regrid_resolution_degrees=1.0,
        ),
    )
    with rasterio.open(result.artifacts[0]) as raster:
        values = raster.read(1, masked=True)
        assert raster.width == 4
        assert raster.height == 4
        assert np.isclose(values.compressed(), 5.0).all()
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["regridding"]["method"] == "pyresample_nearest"
    assert manifest["regridding"]["resolution_degrees"] == 1.0
