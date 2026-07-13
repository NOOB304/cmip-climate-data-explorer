from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import xarray as xr
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from shapely import from_wkb, to_geojson

from cmip_explorer import __version__
from cmip_explorer.domain.enums import FailureCode
from cmip_explorer.domain.errors import ExplorerError

from .scientific import annual_aggregate, convert_units, stitch_time_series


@dataclass(frozen=True, slots=True)
class ProcessingOptions:
    variable_id: str
    start_year: int
    end_year: int
    target_unit: str
    statistic: str = "mean"
    output_format: str = "COG"
    all_touched: bool = False
    nodata: float = -9999.0
    source_id: str = "unknown-model"
    experiment_id: str = "historical-scenario"
    member_id: str = "unknown-member"
    grid_label: str = "unknown-grid"
    region_name: str = "region"
    regrid_resolution_degrees: float | None = None


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    artifacts: tuple[Path, ...]
    manifest: Path


def process_to_geotiffs(
    input_paths: list[Path],
    region_wkb_hex: str,
    output_dir: Path,
    options: ProcessingOptions,
    provenance: dict[str, Any] | None = None,
) -> ProcessingResult:
    if not input_paths:
        raise ValueError("at least one input NetCDF is required")
    datasets = [xr.open_dataset(path, chunks={"time": 12}) for path in input_paths]
    try:
        data = stitch_time_series(
            datasets, options.variable_id, options.start_year, options.end_year
        )
        annual = annual_aggregate(data, options.statistic)
        annual = convert_units(annual, options.target_unit).load()
    finally:
        for dataset in datasets:
            dataset.close()

    geometry = from_wkb(bytes.fromhex(region_wkb_hex))
    annual, regridding = _rectify_curvilinear(
        annual, geometry.bounds, options.regrid_resolution_degrees
    )
    y_name, x_name = _spatial_coordinates(annual)
    annual = _orient_grid(annual, y_name, x_name)
    latitudes = annual[y_name].values.astype(float)
    longitudes = annual[x_name].values.astype(float)
    transform_value = _grid_transform(latitudes, longitudes)
    mask = geometry_mask(
        [json.loads(to_geojson(geometry))],
        out_shape=(len(latitudes), len(longitudes)),
        transform=transform_value,
        all_touched=options.all_touched,
        invert=True,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for year in annual.year.values:
        values = annual.sel(year=year).values.astype("float32")
        values = np.where(mask & np.isfinite(values), values, options.nodata).astype("float32")
        filename = _output_filename(options, int(year))
        output = output_dir / filename
        _write_raster(output, values, transform_value, options)
        artifacts.append(output)

    expected_years = options.end_year - options.start_year + 1
    if len(artifacts) != expected_years:
        raise ExplorerError(
            FailureCode.TIME_UNAVAILABLE,
            "output year count does not match the requested inclusive range",
            details={"expected": expected_years, "actual": len(artifacts)},
        )

    manifest_path = output_dir / "processing_manifest.json"
    manifest = {
        "schema_version": "1.0",
        "app_version": __version__,
        "created_at": datetime.now(UTC).isoformat(),
        "inputs": [
            {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for path in input_paths
        ],
        "region": {"name": options.region_name, "geometry_sha256": _geometry_hash(region_wkb_hex)},
        "processing": asdict(options),
        "regridding": regridding,
        "provenance": provenance or {},
        "artifacts": [
            {"path": path.name, "sha256": _sha256(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
    }
    temporary = manifest_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, manifest_path)
    return ProcessingResult(tuple(artifacts), manifest_path)


def _spatial_coordinates(data: xr.DataArray) -> tuple[str, str]:
    y_name = next(
        (
            name
            for name in data.dims
            if name.lower() in {"lat", "latitude", "y"}
            or str(data[name].attrs.get("axis", "")).upper() == "Y"
        ),
        None,
    )
    x_name = next(
        (
            name
            for name in data.dims
            if name.lower() in {"lon", "longitude", "x"}
            or str(data[name].attrs.get("axis", "")).upper() == "X"
        ),
        None,
    )
    if not y_name or not x_name:
        raise ExplorerError(FailureCode.COORDINATE_ERROR, "spatial coordinates were not found")
    return y_name, x_name


def _rectify_curvilinear(
    data: xr.DataArray,
    bounds: tuple[float, float, float, float],
    requested_resolution: float | None,
) -> tuple[xr.DataArray, dict[str, Any] | None]:
    latitude_name = _coordinate_by_standard_name(data, "Y", {"lat", "latitude"})
    longitude_name = _coordinate_by_standard_name(data, "X", {"lon", "longitude"})
    if not latitude_name or not longitude_name:
        return data, None
    latitudes = data[latitude_name]
    longitudes = data[longitude_name]
    if latitudes.ndim == 1 and longitudes.ndim == 1:
        return data, None
    if latitudes.ndim != 2 or longitudes.ndim != 2 or latitudes.dims != longitudes.dims:
        raise ExplorerError(
            FailureCode.UNSUPPORTED_GRID,
            "curvilinear latitude and longitude coordinates are incompatible",
        )

    from pyresample.geometry import GridDefinition, SwathDefinition
    from pyresample.kd_tree import resample_nearest

    source_lat = latitudes.values.astype(float)
    source_lon = ((longitudes.values.astype(float) + 180.0) % 360.0) - 180.0
    resolution = requested_resolution or _estimate_grid_resolution(source_lat, source_lon)
    if not 0.01 <= resolution <= 10.0:
        raise ExplorerError(
            FailureCode.VALIDATION_FAILED,
            "regridding resolution must be between 0.01 and 10 degrees",
        )
    west, south, east, north = bounds
    target_lons = np.arange(west + resolution / 2.0, east, resolution)
    target_lats = np.arange(north - resolution / 2.0, south, -resolution)
    if len(target_lons) < 2 or len(target_lats) < 2:
        raise ExplorerError(
            FailureCode.COORDINATE_ERROR,
            "target region is too small for the selected regridding resolution",
            details={"bounds": bounds, "resolution_degrees": resolution},
        )
    lon_grid, lat_grid = np.meshgrid(target_lons, target_lats)
    source = SwathDefinition(lons=source_lon, lats=source_lat)
    target = GridDefinition(lons=lon_grid, lats=lat_grid)
    radius = resolution * 111_320.0 * 2.5
    y_dim, x_dim = latitudes.dims
    ordered = data.transpose("year", y_dim, x_dim)
    values = np.stack(
        [
            resample_nearest(
                source,
                frame.values.astype(float),
                target,
                radius_of_influence=radius,
                fill_value=np.nan,
            )
            for frame in ordered
        ]
    )
    result = xr.DataArray(
        values,
        dims=("year", "lat", "lon"),
        coords={"year": ordered.year.values, "lat": target_lats, "lon": target_lons},
        name=data.name,
        attrs=dict(data.attrs),
    )
    metadata = {
        "method": "pyresample_nearest",
        "source_grid": "curvilinear",
        "resolution_degrees": resolution,
        "radius_of_influence_metres": radius,
        "target_width": len(target_lons),
        "target_height": len(target_lats),
    }
    return result, metadata


def _coordinate_by_standard_name(data: xr.DataArray, axis: str, names: set[str]) -> str | None:
    for name, coordinate in data.coords.items():
        if str(coordinate.attrs.get("axis", "")).upper() == axis:
            return name
        if str(coordinate.attrs.get("standard_name", "")).lower() in names:
            return name
        if name.lower() in names:
            return name
    return None


def _estimate_grid_resolution(latitudes: np.ndarray, longitudes: np.ndarray) -> float:
    distances = []
    for axis in (0, 1):
        lat_delta = np.diff(latitudes, axis=axis)
        lon_delta = ((np.diff(longitudes, axis=axis) + 180.0) % 360.0) - 180.0
        candidate = np.hypot(lat_delta, lon_delta)
        distances.extend(candidate[np.isfinite(candidate) & (candidate > 0)].tolist())
    if not distances:
        raise ExplorerError(
            FailureCode.COORDINATE_ERROR, "could not estimate curvilinear grid resolution"
        )
    return float(np.clip(np.median(distances), 0.05, 5.0))


def _orient_grid(data: xr.DataArray, y_name: str, x_name: str) -> xr.DataArray:
    longitudes = (((data[x_name] + 180) % 360) - 180).astype(float)
    data = data.assign_coords({x_name: longitudes}).sortby(x_name)
    return data.sortby(y_name, ascending=False)


def _grid_transform(latitudes: np.ndarray, longitudes: np.ndarray) -> Any:
    if len(latitudes) < 2 or len(longitudes) < 2:
        raise ExplorerError(
            FailureCode.COORDINATE_ERROR,
            "at least two latitude and longitude cells are required for a GeoTIFF transform",
        )
    dx = float(np.median(np.abs(np.diff(longitudes))))
    dy = float(np.median(np.abs(np.diff(latitudes))))
    if dx <= 0 or dy <= 0:
        raise ExplorerError(FailureCode.COORDINATE_ERROR, "grid spacing is not positive")
    return from_origin(longitudes[0] - dx / 2, latitudes[0] + dy / 2, dx, dy)


def _write_raster(
    path: Path, values: np.ndarray, transform_value: Any, options: ProcessingOptions
) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.unlink(missing_ok=True)
    driver = "COG" if options.output_format.upper() == "COG" else "GTiff"
    profile: dict[str, Any] = {
        "driver": driver,
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform_value,
        "nodata": options.nodata,
        "compress": "DEFLATE",
    }
    if driver == "COG":
        profile.update({"blocksize": 256, "overview_resampling": "AVERAGE"})
    else:
        profile.update({"tiled": True, "blockxsize": 256, "blockysize": 256, "predictor": 3})
    with rasterio.open(temporary, "w", **profile) as destination:
        destination.write(values, 1)
        destination.update_tags(
            variable_id=options.variable_id,
            units="degC" if options.target_unit in {"°C", "celsius"} else options.target_unit,
            statistic=options.statistic,
        )
    with rasterio.open(temporary) as validation:
        if validation.crs is None or validation.count != 1:
            raise ExplorerError(FailureCode.VALIDATION_FAILED, "GeoTIFF validation failed")
    os.replace(temporary, path)


def _output_filename(options: ProcessingOptions, year: int) -> str:
    unit = options.target_unit.replace("°", "deg").replace("/", "-")
    pieces = (
        options.variable_id,
        options.source_id,
        options.experiment_id,
        options.member_id,
        options.grid_label,
        str(year),
        f"annual_{options.statistic}",
        unit,
        options.region_name,
    )
    safe = "_".join(_safe_piece(piece) for piece in pieces)
    return f"{safe}.tif"


def _safe_piece(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-." else "-" for character in value
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _geometry_hash(wkb_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(wkb_hex)).hexdigest()
