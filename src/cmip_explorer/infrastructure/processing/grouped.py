from __future__ import annotations

import calendar as calendar_module
import json
import os
from collections import defaultdict
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import fiona
import numpy as np
import rasterio
import xarray as xr
from rasterio.enums import Resampling
from rasterio.mask import mask
from rasterio.transform import Affine, from_origin
from rasterio.warp import reproject, transform_geom
from scipy import stats
from shapely import make_valid
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from cmip_explorer.domain.enums import FailureCode
from cmip_explorer.domain.errors import ExplorerError

from .local import (
    _grid_transform,
    _has_spatial_dimensions,
    _orient_grid,
    _safe,
    _spatial_coordinates,
    _validate_raster_frame,
    _write_geotiff,
)

TargetPeriod = Literal["daily", "monthly", "annual"]
AggregationStatistic = Literal["auto", "mean", "total", "max", "min", "mode"]
ResamplingMethod = Literal["none", "nearest", "bilinear", "cubic", "average"]


@dataclass(frozen=True, slots=True)
class ResamplingOptions:
    method: ResamplingMethod = "none"
    target_resolution: float | None = None
    template_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RasterGridInfo:
    crs: str
    transform: Affine
    width: int
    height: int

    @property
    def x_resolution(self) -> float:
        return abs(float(self.transform.a))

    @property
    def y_resolution(self) -> float:
        return abs(float(self.transform.e))


@dataclass(frozen=True, slots=True)
class VectorRegionInfo:
    path: Path
    source_crs: str
    bounds_wgs84: tuple[float, float, float, float]
    feature_count: int
    layer_count: int
    geometry_wgs84: dict[str, object]
    companion_files: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class LocalDatasetGroup:
    key: str
    root: Path
    paths: tuple[Path, ...]
    variable_id: str
    variable_label: str
    source_id: str
    experiment_id: str
    member_id: str
    table_id: str
    frequency: str
    grid_label: str
    temporal_resolution: str
    start: str
    end: str
    units: str
    standard_name: str
    cell_methods: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DatasetScanResult:
    groups: tuple[LocalDatasetGroup, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _SourceHeader:
    path: Path
    variable_id: str
    variable_label: str
    source_id: str
    experiment_id: str
    member_id: str
    table_id: str
    frequency: str
    grid_label: str
    temporal_resolution: str
    start: str
    end: str
    units: str
    standard_name: str
    cell_methods: str
    grid_signature: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _PeriodSlice:
    path: Path
    indices: tuple[int, ...]
    durations: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _PeriodDefinition:
    key: str
    year: int
    month: int | None
    day: int | None
    calendar: str
    slices: tuple[_PeriodSlice, ...]
    covered_seconds: float
    expected_seconds: float
    time_steps: int


def scan_downloaded_datasets(
    root: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> DatasetScanResult:
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".nc", ".nc4"}
    )
    grouped: dict[tuple[object, ...], list[_SourceHeader]] = defaultdict(list)
    warnings: list[str] = []
    for index, path in enumerate(files, start=1):
        try:
            for header in _read_headers(path):
                key = (
                    header.variable_id,
                    header.source_id,
                    header.experiment_id,
                    header.member_id,
                    header.table_id,
                    header.frequency,
                    header.grid_label,
                    header.units,
                    header.grid_signature,
                )
                grouped[key].append(header)
        except Exception as exc:
            warnings.append(f"{path.name}: {exc}")
        if progress:
            progress(index, len(files), path.name)

    groups: list[LocalDatasetGroup] = []
    for headers in grouped.values():
        headers.sort(key=lambda item: (item.start, item.path.name))
        first = headers[0]
        paths = tuple(header.path for header in headers)
        key = "|".join(
            (
                first.variable_id,
                first.source_id,
                first.experiment_id,
                first.member_id,
                first.table_id,
                first.grid_label,
            )
        )
        groups.append(
            LocalDatasetGroup(
                key=key,
                root=root,
                paths=paths,
                variable_id=first.variable_id,
                variable_label=first.variable_label,
                source_id=first.source_id,
                experiment_id=first.experiment_id,
                member_id=first.member_id,
                table_id=first.table_id,
                frequency=first.frequency,
                grid_label=first.grid_label,
                temporal_resolution=first.temporal_resolution,
                start=min(header.start for header in headers),
                end=max(header.end for header in headers),
                units=first.units,
                standard_name=first.standard_name,
                cell_methods=first.cell_methods,
                size_bytes=sum(path.stat().st_size for path in paths),
            )
        )
    groups.sort(
        key=lambda item: (
            item.variable_id,
            item.source_id,
            item.experiment_id,
            item.frequency,
            item.member_id,
        )
    )
    return DatasetScanResult(tuple(groups), tuple(warnings))


def aggregate_netcdf_to_geotiffs(
    paths: tuple[Path, ...] | list[Path],
    variable: str,
    output_dir: Path,
    *,
    source_frequency: str,
    target_period: TargetPeriod,
    statistic: AggregationStatistic = "auto",
    months: tuple[int, ...] = tuple(range(1, 13)),
    convert_units: bool = True,
    resampling: ResamplingOptions | None = None,
    clip_path: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    chunk_size: int = 32,
) -> tuple[Path, ...]:
    if not paths:
        raise ValueError("请至少选择一个 NetCDF 数据组")
    if target_period not in {"daily", "monthly", "annual"}:
        raise ValueError(f"不支持的输出时间尺度: {target_period}")
    if target_period not in allowed_target_periods(source_frequency):
        raise ExplorerError(
            FailureCode.VALIDATION_FAILED,
            f"{source_frequency} 数据不能生成比原始时间尺度更短的 {target_period} TIF",
        )
    selected_months = tuple(sorted(set(months)))
    if not selected_months or any(month < 1 or month > 12 for month in selected_months):
        raise ValueError("请至少选择一个有效月份")

    manifest, grid = _build_period_manifest(
        tuple(Path(path) for path in paths),
        variable,
        source_frequency,
        target_period,
        selected_months,
        cancelled,
    )
    first_path = Path(paths[0])
    with xr.open_dataset(first_path, decode_times=True) as first_dataset:
        source_data = first_dataset[variable]
        source_units = str(source_data.attrs.get("units", ""))
        standard_name = str(source_data.attrs.get("standard_name", ""))
        cell_methods = str(source_data.attrs.get("cell_methods", ""))
    resolved = _resolve_statistic(variable, source_units, statistic)
    output_units = _output_units(
        variable, source_units, standard_name, resolved, convert_units
    )
    if resolved == "total":
        _integration_factors(source_units, np.array([1.0]))

    latitudes, longitudes = grid
    transform = _grid_transform(latitudes, longitudes)
    resampling_options = resampling or ResamplingOptions()
    target_grid = _resolve_target_grid(
        latitudes,
        longitudes,
        transform,
        resampling_options,
    )
    clip_region = inspect_vector_region(clip_path) if clip_path is not None else None
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    output_records: list[dict[str, object]] = []
    month_suffix = (
        ""
        if target_period in {"daily", "monthly"}
        or selected_months == tuple(range(1, 13))
        else "_m" + "-".join(f"{month:02d}" for month in selected_months)
    )
    working_context = (
        TemporaryDirectory(prefix=".cmip-working-", dir=output_dir)
        if clip_region is not None
        else nullcontext(None)
    )
    with working_context as working_root:
        for position, period in enumerate(manifest, start=1):
            if cancelled and cancelled():
                raise InterruptedError("TIF 处理已取消")
            values = _aggregate_period(
                period,
                variable,
                resolved,
                source_units,
                chunk_size,
                cancelled,
            )
            values = _convert_values(
                values,
                variable,
                source_units,
                standard_name,
                resolved,
                convert_units,
            )
            label = period.key.replace("-", "_")
            output = output_dir / f"{_safe(variable)}_{label}_{resolved}{month_suffix}.tif"
            write_path = Path(working_root) / output.name if working_root else output
            metadata = {
                "source_units": source_units,
                "source_frequency": source_frequency,
                "target_period": target_period,
                "period": period.key,
                "selected_months": ",".join(map(str, selected_months)),
                "time_steps": period.time_steps,
                "covered_seconds": f"{period.covered_seconds:.3f}",
                "cell_methods": cell_methods,
                "unit_conversion": convert_units,
                "resampling": resampling_options.method,
                "clip_region": str(clip_region.path) if clip_region else "none",
            }
            if target_grid is None:
                _write_geotiff(
                    write_path,
                    values.astype("float32"),
                    transform,
                    variable,
                    output_units,
                    resolved,
                    metadata,
                )
            else:
                _write_resampled_geotiff(
                    write_path,
                    values,
                    transform,
                    target_grid,
                    resampling_options.method,
                    variable,
                    output_units,
                    resolved,
                    metadata,
                )
            if clip_region is not None:
                _clip_geotiff(write_path, output, clip_region)
            artifacts.append(output)
            output_records.append(
                {
                    "path": output.name,
                    "period": period.key,
                    "statistic": resolved,
                    "units": output_units,
                    "time_steps": period.time_steps,
                }
            )
            if progress:
                progress(position, len(manifest), output.name)

    if cancelled and cancelled():
        raise InterruptedError("TIF 处理已取消")

    report = {
        "variable_id": variable,
        "source_frequency": source_frequency,
        "source_units": source_units,
        "target_period": target_period,
        "statistic_requested": statistic,
        "statistic_applied": resolved,
        "output_units": output_units,
        "unit_conversion": convert_units,
        "resampling": _resampling_report(resampling_options, target_grid),
        "clipping": _clipping_report(clip_region),
        "selected_months": selected_months,
        "source_files": [str(Path(path).resolve()) for path in paths],
        "outputs": output_records,
    }
    temporary = output_dir / "processing_manifest.json.part"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output_dir / "processing_manifest.json")
    return tuple(artifacts)


def recommended_statistic(variable: str, units: str) -> str:
    return _resolve_statistic(variable, units, "auto")


def allowed_target_periods(source_frequency: str) -> tuple[TargetPeriod, ...]:
    resolution = _temporal_resolution(source_frequency)
    if resolution == "subdaily":
        return ("daily", "monthly", "annual")
    if resolution == "daily":
        return ("daily", "monthly", "annual")
    if resolution == "monthly":
        return ("monthly", "annual")
    if resolution == "annual":
        return ("annual",)
    return ()


def automatic_unit_target(variable: str, units: str, standard_name: str = "") -> str | None:
    if _is_temperature(variable, units, standard_name):
        return "degC"
    if _is_precipitation(variable, units, standard_name):
        return "mm"
    return None


def inspect_raster_template(path: Path) -> RasterGridInfo:
    try:
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                raise ExplorerError(
                    FailureCode.COORDINATE_ERROR,
                    "模板 TIF 没有坐标参考系 (CRS)",
                )
            if dataset.width < 1 or dataset.height < 1:
                raise ExplorerError(FailureCode.VALIDATION_FAILED, "模板 TIF 尺寸无效")
            return RasterGridInfo(
                dataset.crs.to_string(),
                dataset.transform,
                dataset.width,
                dataset.height,
            )
    except ExplorerError:
        raise
    except Exception as exc:
        raise ExplorerError(
            FailureCode.VALIDATION_FAILED,
            f"无法读取模板 TIF: {exc}",
        ) from exc


def inspect_vector_region(path: Path) -> VectorRegionInfo:
    path = Path(path)
    if not path.exists():
        raise ExplorerError(FailureCode.VALIDATION_FAILED, "研究区矢量文件不存在")
    companion_files: tuple[Path, ...] = (path,)
    if path.suffix.casefold() == ".shp":
        matching = {
            item.suffix.casefold(): item
            for item in path.parent.iterdir()
            if item.is_file() and item.stem.casefold() == path.stem.casefold()
        }
        missing = [suffix for suffix in (".shx", ".dbf", ".prj") if suffix not in matching]
        if missing:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "SHP 缺少同名组件: " + ", ".join(missing),
            )
        companion_files = tuple(matching[suffix] for suffix in sorted(matching))

    try:
        try:
            layers: tuple[str | None, ...] = tuple(fiona.listlayers(path))
        except Exception:
            layers = (None,)
        if not layers:
            layers = (None,)
        polygons = []
        feature_count = 0
        source_crs_values: list[str] = []
        for layer in layers:
            open_options = {"layer": layer} if layer is not None else {}
            with fiona.open(path, **open_options) as collection:
                source_crs = collection.crs_wkt or collection.crs
                if not source_crs:
                    raise ExplorerError(
                        FailureCode.COORDINATE_ERROR,
                        f"矢量图层 {layer or path.name} 没有坐标参考系 (CRS)",
                    )
                source_crs_text = str(source_crs)
                source_crs_values.append(source_crs_text)
                for feature in collection:
                    if feature.geometry is None:
                        continue
                    transformed = transform_geom(
                        source_crs,
                        "EPSG:4326",
                        dict(feature.geometry),
                        precision=12,
                    )
                    geometry = shape(transformed)
                    if not geometry.is_valid:
                        geometry = make_valid(geometry)
                    parts = _polygon_parts(geometry)
                    polygons.extend(parts)
                    if parts:
                        feature_count += 1
        if not polygons:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "研究区文件中没有可用于裁剪的面或多面要素",
            )
        merged = unary_union(polygons)
        if merged.is_empty:
            raise ExplorerError(FailureCode.VALIDATION_FAILED, "研究区几何为空")
        source_crs_label = (
            source_crs_values[0]
            if len(set(source_crs_values)) == 1
            else f"{len(set(source_crs_values))} 种 CRS"
        )
        return VectorRegionInfo(
            path=path,
            source_crs=source_crs_label,
            bounds_wgs84=tuple(float(value) for value in merged.bounds),
            feature_count=feature_count,
            layer_count=len(layers),
            geometry_wgs84=dict(mapping(merged)),
            companion_files=companion_files,
        )
    except ExplorerError:
        raise
    except Exception as exc:
        raise ExplorerError(
            FailureCode.VALIDATION_FAILED,
            f"无法读取研究区矢量文件: {exc}",
        ) from exc


def _polygon_parts(geometry) -> list[object]:
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    if hasattr(geometry, "geoms"):
        return [part for item in geometry.geoms for part in _polygon_parts(item)]
    return []


def _read_headers(path: Path) -> tuple[_SourceHeader, ...]:
    with xr.open_dataset(path, decode_times=True) as dataset:
        source_id = str(dataset.attrs.get("source_id") or path.parent.parent.name or "未知模型")
        experiment_id = str(
            dataset.attrs.get("experiment_id") or path.parent.name or "未知情景"
        )
        member_id = str(dataset.attrs.get("variant_label") or "默认模拟")
        table_id = str(dataset.attrs.get("table_id") or "")
        frequency = str(dataset.attrs.get("frequency") or _infer_frequency(dataset))
        grid_label = str(dataset.attrs.get("grid_label") or "")
        temporal_resolution = _temporal_resolution(frequency)
        start, end = _coverage(dataset)
        headers: list[_SourceHeader] = []
        for variable_id, data in dataset.data_vars.items():
            if not _has_spatial_dimensions(data):
                continue
            non_time = tuple(dimension for dimension in data.dims if dimension != "time")
            if len(non_time) != 2:
                continue
            headers.append(
                _SourceHeader(
                    path=path,
                    variable_id=variable_id,
                    variable_label=str(
                        data.attrs.get("long_name")
                        or data.attrs.get("standard_name")
                        or variable_id
                    ),
                    source_id=source_id,
                    experiment_id=experiment_id,
                    member_id=member_id,
                    table_id=table_id,
                    frequency=frequency,
                    grid_label=grid_label,
                    temporal_resolution=temporal_resolution,
                    start=start,
                    end=end,
                    units=str(data.attrs.get("units", "")),
                    standard_name=str(data.attrs.get("standard_name", "")),
                    cell_methods=str(data.attrs.get("cell_methods", "")),
                    grid_signature=tuple(
                        (dimension, int(data.sizes[dimension])) for dimension in non_time
                    ),
                )
            )
        if not headers:
            raise ExplorerError(
                FailureCode.VARIABLE_MISSING,
                "文件中没有可直接转换为二维 TIF 的变量",
            )
        return tuple(headers)


def _coverage(dataset: xr.Dataset) -> tuple[str, str]:
    if "time" not in dataset.coords or not dataset.sizes.get("time", 0):
        return "无时间", "无时间"
    values = dataset.time.values
    return _date_label(values[0]), _date_label(values[-1])


def _date_label(value: object) -> str:
    text = str(value).replace("T", " ")
    return text.split(" ", 1)[0]


def _infer_frequency(dataset: xr.Dataset) -> str:
    if "time" not in dataset.coords or dataset.sizes.get("time", 0) < 2:
        return "fx"
    counts = dataset.time.dt.year.groupby("time.year").count().values
    if counts.size and int(np.max(counts)) <= 12:
        return "mon"
    delta = _difference_seconds(dataset.time.values[0], dataset.time.values[1])
    if delta <= 5_400:
        return "1hr"
    if delta <= 16_200:
        return "3hr"
    if delta <= 32_400:
        return "6hr"
    if delta <= 129_600:
        return "day"
    return "mon"


def _temporal_resolution(frequency: str) -> str:
    value = frequency.casefold()
    if "mon" in value:
        return "monthly"
    if value in {"day", "daily"}:
        return "daily"
    if "hr" in value or "sub" in value:
        return "subdaily"
    if value in {"yr", "year", "annual"}:
        return "annual"
    return "static"


def _build_period_manifest(
    paths: tuple[Path, ...],
    variable: str,
    source_frequency: str,
    target_period: TargetPeriod,
    selected_months: tuple[int, ...],
    cancelled: Callable[[], bool] | None = None,
) -> tuple[tuple[_PeriodDefinition, ...], tuple[np.ndarray, np.ndarray]]:
    period_entries: dict[str, dict[Path, list[tuple[int, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    period_metadata: dict[str, tuple[int, int | None, int | None, str]] = {}
    seen_times: set[str] = set()
    reference_grid: tuple[np.ndarray, np.ndarray] | None = None
    reference_calendar: str | None = None
    for path in paths:
        if cancelled and cancelled():
            raise InterruptedError("TIF 处理已取消")
        with xr.open_dataset(path, decode_times=True) as dataset:
            if variable not in dataset:
                raise ExplorerError(
                    FailureCode.VARIABLE_MISSING,
                    f"{path.name} 中没有变量 {variable}",
                )
            data = dataset[variable]
            if "time" not in data.dims:
                raise ExplorerError(FailureCode.TIME_UNAVAILABLE, "所选数据没有时间维度")
            y_name, x_name = _spatial_coordinates(data)
            extra = tuple(
                dimension
                for dimension in data.dims
                if dimension not in {"time", y_name, x_name}
            )
            if extra:
                raise ExplorerError(
                    FailureCode.UNSUPPORTED_GRID,
                    f"变量还有未选择的维度: {', '.join(extra)}",
                )
            ordered = _orient_grid(data, y_name, x_name).transpose("time", y_name, x_name)
            latitudes = ordered[y_name].values.astype(float)
            longitudes = ordered[x_name].values.astype(float)
            if latitudes.ndim != 1 or longitudes.ndim != 1:
                raise ExplorerError(
                    FailureCode.UNSUPPORTED_GRID,
                    "当前批量处理仅支持规则经纬度网格",
                )
            if reference_grid is None:
                reference_grid = (latitudes, longitudes)
            elif not (
                np.array_equal(reference_grid[0], latitudes)
                and np.array_equal(reference_grid[1], longitudes)
            ):
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    "数据组中的 NC 文件经纬度网格不一致",
                )

            years = dataset.time.dt.year.values.astype(int)
            months = dataset.time.dt.month.values.astype(int)
            days = dataset.time.dt.day.values.astype(int)
            calendar_name = str(dataset.time.dt.calendar)
            if reference_calendar is None:
                reference_calendar = calendar_name
            elif reference_calendar != calendar_name:
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    "数据组中的日历类型不一致",
                )
            durations = _time_durations(dataset, source_frequency)
            for index, (value, year, month, day, duration) in enumerate(
                zip(dataset.time.values, years, months, days, durations, strict=True)
            ):
                if index % 256 == 0 and cancelled and cancelled():
                    raise InterruptedError("TIF 处理已取消")
                if int(month) not in selected_months:
                    continue
                time_key = str(value)
                if time_key in seen_times:
                    raise ExplorerError(
                        FailureCode.VALIDATION_FAILED,
                        f"所选数据包含重复时间: {time_key}",
                    )
                seen_times.add(time_key)
                if target_period == "daily":
                    period_key = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                elif target_period == "monthly":
                    period_key = f"{int(year):04d}-{int(month):02d}"
                else:
                    period_key = f"{int(year):04d}"
                period_entries[period_key][path].append((index, float(duration)))
                period_metadata[period_key] = (
                    int(year),
                    int(month) if target_period in {"daily", "monthly"} else None,
                    int(day) if target_period == "daily" else None,
                    calendar_name,
                )
    if not period_entries or reference_grid is None:
        raise ExplorerError(FailureCode.TIME_UNAVAILABLE, "所选月份没有可处理数据")
    _validate_period_sequence(
        period_entries,
        period_metadata,
        target_period,
        selected_months,
        reference_calendar or "standard",
    )

    periods: list[_PeriodDefinition] = []
    for key in sorted(period_entries):
        year, month, day, calendar_name = period_metadata[key]
        slices = tuple(
            _PeriodSlice(
                path,
                tuple(index for index, _duration in entries),
                tuple(duration for _index, duration in entries),
            )
            for path, entries in sorted(period_entries[key].items(), key=lambda item: str(item[0]))
        )
        covered = sum(sum(item.durations) for item in slices)
        if day is not None:
            expected = 86_400
        else:
            expected_months = (month,) if month is not None else selected_months
            expected = sum(
                _days_in_month(year, item, calendar_name) * 86_400
                for item in expected_months
            )
        tolerance = max(60.0, expected * 1e-6)
        if abs(covered - expected) > tolerance:
            if day is not None:
                label = f"{year} 年 {month} 月 {day} 日"
            elif month is not None:
                label = f"{year} 年 {month} 月"
            else:
                label = f"{year} 年"
            raise ExplorerError(
                FailureCode.TIME_UNAVAILABLE,
                f"{label}数据不完整: 应覆盖 {expected / 86_400:.1f} 天, "
                f"实际覆盖 {covered / 86_400:.1f} 天",
            )
        periods.append(
            _PeriodDefinition(
                key,
                year,
                month,
                day,
                calendar_name,
                slices,
                covered,
                float(expected),
                sum(len(item.indices) for item in slices),
            )
        )
    return tuple(periods), reference_grid


def _validate_period_sequence(
    entries: dict[str, dict[Path, list[tuple[int, float]]]],
    metadata: dict[str, tuple[int, int | None, int | None, str]],
    target_period: TargetPeriod,
    selected_months: tuple[int, ...],
    calendar_name: str,
) -> None:
    values = sorted((year, month or 1, day or 1) for year, month, day, _ in metadata.values())
    first = values[0]
    last = values[-1]
    if target_period == "annual":
        for year in range(first[0], last[0] + 1):
            if f"{year:04d}" not in entries:
                raise ExplorerError(
                    FailureCode.TIME_UNAVAILABLE,
                    f"时间序列缺少 {year} 年数据",
                )
        return
    if target_period == "monthly":
        year, month = first[:2]
        while (year, month) <= last[:2]:
            key = f"{year:04d}-{month:02d}"
            if month in selected_months and key not in entries:
                raise ExplorerError(
                    FailureCode.TIME_UNAVAILABLE,
                    f"时间序列缺少 {year} 年 {month} 月数据",
                )
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return

    year, month, day = first
    while (year, month, day) <= last:
        key = f"{year:04d}-{month:02d}-{day:02d}"
        if month in selected_months and key not in entries:
            raise ExplorerError(
                FailureCode.TIME_UNAVAILABLE,
                f"时间序列缺少 {year} 年 {month} 月 {day} 日数据",
            )
        day += 1
        if day > _days_in_month(year, month, calendar_name):
            day = 1
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1


def _time_durations(dataset: xr.Dataset, source_frequency: str) -> np.ndarray:
    bounds_name = str(dataset.time.attrs.get("bounds", ""))
    if bounds_name and bounds_name in dataset:
        bounds = dataset[bounds_name]
        bound_dimension = next(
            (dimension for dimension in bounds.dims if dimension != "time"),
            None,
        )
        if bound_dimension and bounds.sizes[bound_dimension] >= 2:
            delta = bounds.isel({bound_dimension: 1}) - bounds.isel({bound_dimension: 0})
            result = _timedelta_array_seconds(delta.values)
            if np.all(result > 0):
                return result
    resolution = _temporal_resolution(source_frequency)
    if resolution == "monthly":
        return dataset.time.dt.days_in_month.values.astype(float) * 86_400
    if resolution == "annual":
        years = dataset.time.dt.year.values.astype(int)
        calendar_name = str(dataset.time.dt.calendar)
        return np.array(
            [
                sum(_days_in_month(int(year), month, calendar_name) for month in range(1, 13))
                * 86_400
                for year in years
            ],
            dtype=float,
        )
    fixed = _fixed_frequency_seconds(source_frequency)
    if fixed is not None:
        return np.full(dataset.sizes["time"], fixed, dtype=float)
    values = dataset.time.values
    if len(values) < 2:
        raise ExplorerError(FailureCode.TIME_UNAVAILABLE, "无法推断单个时间点的持续时间")
    differences = np.array(
        [_difference_seconds(left, right) for left, right in pairwise(values)],
        dtype=float,
    )
    median = float(np.median(differences))
    return np.concatenate((differences, [median]))


def _fixed_frequency_seconds(frequency: str) -> float | None:
    value = frequency.casefold()
    if value in {"day", "daily"}:
        return 86_400.0
    digits = "".join(character for character in value.split("hr", 1)[0] if character.isdigit())
    if "hr" in value and digits:
        return float(int(digits) * 3_600)
    return None


def _timedelta_array_seconds(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values)
    if np.issubdtype(array.dtype, np.timedelta64):
        return array / np.timedelta64(1, "s")
    return np.array([_timedelta_seconds(value) for value in array], dtype=float)


def _difference_seconds(left: object, right: object) -> float:
    return _timedelta_seconds(right - left)  # type: ignore[operator]


def _timedelta_seconds(value: object) -> float:
    if isinstance(value, np.timedelta64):
        return float(value / np.timedelta64(1, "s"))
    total_seconds = getattr(value, "total_seconds", None)
    if callable(total_seconds):
        return float(total_seconds())
    raise TypeError(f"无法解析时间间隔: {value!r}")


def _days_in_month(year: int, month: int, calendar_name: str) -> int:
    name = calendar_name.casefold()
    if name == "360_day":
        return 30
    if month != 2:
        return calendar_module.monthrange(year, month)[1]
    if name in {"noleap", "365_day"}:
        return 28
    if name in {"all_leap", "366_day"}:
        return 29
    return calendar_module.monthrange(year, month)[1]


def _aggregate_period(
    period: _PeriodDefinition,
    variable: str,
    statistic: str,
    source_units: str,
    chunk_size: int,
    cancelled: Callable[[], bool] | None,
) -> np.ndarray:
    numerator: np.ndarray | None = None
    denominator: np.ndarray | None = None
    valid_count: np.ndarray | None = None
    extrema: np.ndarray | None = None
    mode_chunks: list[np.ndarray] = []
    for source_slice in period.slices:
        with xr.open_dataset(source_slice.path, decode_times=True) as dataset:
            data = dataset[variable]
            y_name, x_name = _spatial_coordinates(data)
            ordered = _orient_grid(data, y_name, x_name).transpose("time", y_name, x_name)
            indices = np.asarray(source_slice.indices, dtype=int)
            durations = np.asarray(source_slice.durations, dtype=float)
            for start in range(0, len(indices), chunk_size):
                if cancelled and cancelled():
                    raise InterruptedError("TIF 处理已取消")
                chunk_indices = indices[start : start + chunk_size]
                chunk_durations = durations[start : start + chunk_size]
                frames = ordered.isel(time=chunk_indices).values.astype("float64")
                _validate_raster_frame(frames[0])
                valid = np.isfinite(frames)
                if valid_count is None:
                    shape = frames.shape[1:]
                    if statistic == "mode" and (
                        period.time_steps * int(np.prod(shape)) * 8 > 2 * 1024**3
                    ):
                        raise ExplorerError(
                            FailureCode.VALIDATION_FAILED,
                            "众数计算需要的内存超过 2 GiB, 请改用平均、最大或最小值",
                        )
                    valid_count = np.zeros(shape, dtype="uint32")
                valid_count += valid.sum(axis=0, dtype="uint32")
                if statistic == "mean":
                    weights = chunk_durations[:, None, None]
                    if numerator is None:
                        numerator = np.zeros(frames.shape[1:], dtype="float64")
                        denominator = np.zeros(frames.shape[1:], dtype="float64")
                    numerator += np.sum(np.where(valid, frames * weights, 0.0), axis=0)
                    denominator += np.sum(np.where(valid, weights, 0.0), axis=0)
                elif statistic == "total":
                    factors = _integration_factors(source_units, chunk_durations)[:, None, None]
                    if numerator is None:
                        numerator = np.zeros(frames.shape[1:], dtype="float64")
                    numerator += np.sum(np.where(valid, frames * factors, 0.0), axis=0)
                elif statistic in {"min", "max"}:
                    fill = np.inf if statistic == "min" else -np.inf
                    candidate = np.min(np.where(valid, frames, fill), axis=0)
                    if statistic == "max":
                        candidate = np.max(np.where(valid, frames, fill), axis=0)
                    if extrema is None:
                        extrema = candidate
                    elif statistic == "min":
                        extrema = np.minimum(extrema, candidate)
                    else:
                        extrema = np.maximum(extrema, candidate)
                elif statistic == "mode":
                    mode_chunks.append(frames)
                else:
                    raise ValueError(f"不支持的统计方式: {statistic}")
    if valid_count is None:
        raise ExplorerError(FailureCode.TIME_UNAVAILABLE, "输出周期没有有效时间点")
    if statistic == "mean":
        assert numerator is not None and denominator is not None
        return np.divide(
            numerator,
            denominator,
            out=np.full_like(numerator, np.nan),
            where=denominator > 0,
        )
    if statistic == "total":
        assert numerator is not None
        return np.where(valid_count > 0, numerator, np.nan)
    if statistic in {"min", "max"}:
        assert extrema is not None
        return np.where(valid_count > 0, extrema, np.nan)
    frames = np.concatenate(mode_chunks, axis=0)
    return stats.mode(frames, axis=0, nan_policy="omit", keepdims=False).mode


def _resolve_statistic(variable: str, units: str, requested: str) -> str:
    if requested != "auto":
        return requested
    variable_id = variable.casefold()
    if _is_integrable(units) and (
        variable_id.startswith("pr")
        or variable_id in {"mrro", "mrros", "mrrob", "evspsbl"}
    ):
        return "total"
    if variable_id in {"tasmax", "tmax"}:
        return "max"
    if variable_id in {"tasmin", "tmin"}:
        return "min"
    return "mean"


def _is_integrable(units: str) -> bool:
    normalized = _normalized_units(units)
    return normalized in {
        "kgm-2s-1",
        "kgm^-2s^-1",
        "mms-1",
        "mm/s",
        "mmday-1",
        "mm/day",
        "mmd-1",
        "mm",
    }


def _integration_factors(units: str, durations: np.ndarray) -> np.ndarray:
    normalized = _normalized_units(units)
    if normalized in {"kgm-2s-1", "kgm^-2s^-1", "mms-1", "mm/s"}:
        return durations
    if normalized in {"mmday-1", "mm/day", "mmd-1"}:
        return durations / 86_400.0
    if normalized == "mm":
        return np.ones_like(durations)
    raise ExplorerError(
        FailureCode.VALIDATION_FAILED,
        f"单位 {units!r} 不能安全换算为累计量",
    )


def _normalized_units(units: str) -> str:
    return units.casefold().replace(" ", "").replace("**", "^")


def _output_units(
    variable: str,
    units: str,
    standard_name: str,
    statistic: str,
    convert_units: bool,
) -> str:
    normalized = _normalized_units(units)
    if statistic == "total":
        if not convert_units and normalized in {"kgm-2s-1", "kgm^-2s^-1"}:
            return "kg m-2"
        return "mm"
    if convert_units and _is_temperature(variable, units, standard_name):
        return "degC"
    if convert_units and _is_precipitation(variable, units, standard_name) and normalized in {
        "kgm-2s-1",
        "kgm^-2s^-1",
        "mms-1",
        "mm/s",
    }:
        return "mm/day"
    return units


def _convert_values(
    values: np.ndarray,
    variable: str,
    units: str,
    standard_name: str,
    statistic: str,
    convert_units: bool,
) -> np.ndarray:
    if statistic == "total" or not convert_units:
        return values
    if _is_temperature(variable, units, standard_name):
        return values - 273.15
    normalized = _normalized_units(units)
    if _is_precipitation(variable, units, standard_name) and normalized in {
        "kgm-2s-1",
        "kgm^-2s^-1",
        "mms-1",
        "mm/s",
    }:
        return values * 86_400.0
    return values


def _is_temperature(variable: str, units: str, standard_name: str) -> bool:
    return _normalized_units(units) in {"k", "kelvin"} and (
        "temperature" in standard_name.casefold()
        or variable.casefold() in {"ta", "tas", "tasmax", "tasmin", "ts", "t2m"}
    )


def _is_precipitation(variable: str, units: str, standard_name: str) -> bool:
    variable_id = variable.casefold()
    return _is_integrable(units) and (
        "precipitation" in standard_name.casefold()
        or variable_id.startswith("pr")
        or variable_id in {"mrro", "mrros", "mrrob"}
    )


def _resolve_target_grid(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    source_transform: Affine,
    options: ResamplingOptions,
) -> RasterGridInfo | None:
    if options.method == "none":
        if options.target_resolution is not None or options.template_path is not None:
            raise ValueError("指定目标网格时必须选择重采样方法")
        return None
    if options.method not in {"nearest", "bilinear", "cubic", "average"}:
        raise ValueError(f"不支持的重采样方法: {options.method}")
    if options.target_resolution is not None and options.template_path is not None:
        raise ValueError("目标分辨率和模板 TIF 只能选择一种")
    if options.template_path is not None:
        return inspect_raster_template(options.template_path)
    if options.target_resolution is None or options.target_resolution <= 0:
        raise ValueError("请选择有效的目标分辨率或模板 TIF")

    resolution = float(options.target_resolution)
    west = float(source_transform.c)
    north = float(source_transform.f)
    east = west + float(source_transform.a) * len(longitudes)
    south = north + float(source_transform.e) * len(latitudes)
    width = max(1, int(np.ceil((east - west) / resolution)))
    height = max(1, int(np.ceil((north - south) / resolution)))
    return RasterGridInfo(
        "EPSG:4326",
        from_origin(west, north, resolution, resolution),
        width,
        height,
    )


def _write_resampled_geotiff(
    path: Path,
    values: np.ndarray,
    source_transform: Affine,
    target: RasterGridInfo,
    method: ResamplingMethod,
    variable: str,
    units: str,
    statistic: str,
    metadata: dict[str, object],
) -> None:
    methods = {
        "nearest": Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
        "average": Resampling.average,
    }
    selected = methods.get(method)
    if selected is None:
        raise ValueError(f"不支持的重采样方法: {method}")
    nodata = -9999.0
    source = np.where(np.isfinite(values), values, nodata).astype("float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tif.part")
    temporary.unlink(missing_ok=True)
    try:
        with rasterio.open(
            temporary,
            "w",
            driver="COG",
            height=target.height,
            width=target.width,
            count=1,
            dtype="float32",
            crs=target.crs,
            transform=target.transform,
            nodata=nodata,
            compress="DEFLATE",
            blocksize=256,
            overview_resampling="AVERAGE",
        ) as destination:
            reproject(
                source=source,
                destination=rasterio.band(destination, 1),
                src_transform=source_transform,
                src_crs="EPSG:4326",
                src_nodata=nodata,
                dst_transform=target.transform,
                dst_crs=target.crs,
                dst_nodata=nodata,
                resampling=selected,
                init_dest_nodata=True,
                num_threads=2,
            )
            destination.update_tags(
                variable_id=variable,
                units=units,
                statistic=statistic,
                target_width=target.width,
                target_height=target.height,
                target_crs=target.crs,
                **{key: str(value) for key, value in metadata.items()},
            )
        with rasterio.open(temporary) as validation:
            if (
                validation.crs is None
                or validation.count != 1
                or validation.width != target.width
                or validation.height != target.height
            ):
                raise ExplorerError(FailureCode.VALIDATION_FAILED, "重采样 TIF 输出校验失败")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _resampling_report(
    options: ResamplingOptions,
    target: RasterGridInfo | None,
) -> dict[str, object]:
    if target is None:
        return {"method": "none", "target": "source-grid"}
    return {
        "method": options.method,
        "template_path": str(options.template_path.resolve()) if options.template_path else None,
        "requested_resolution_degrees": options.target_resolution,
        "crs": target.crs,
        "width": target.width,
        "height": target.height,
        "x_resolution": target.x_resolution,
        "y_resolution": target.y_resolution,
    }


def _clip_geotiff(source_path: Path, output_path: Path, region: VectorRegionInfo) -> None:
    temporary = output_path.with_suffix(".tif.part")
    temporary.unlink(missing_ok=True)
    try:
        with rasterio.open(source_path) as source:
            if source.crs is None:
                raise ExplorerError(
                    FailureCode.COORDINATE_ERROR,
                    "待裁剪 TIF 没有坐标参考系",
                )
            geometry = transform_geom(
                "EPSG:4326",
                source.crs,
                region.geometry_wgs84,
                precision=12,
            )
            nodata = float(source.nodata if source.nodata is not None else -9999.0)
            try:
                clipped, clipped_transform = mask(
                    source,
                    [geometry],
                    crop=True,
                    filled=True,
                    nodata=nodata,
                )
            except ValueError as exc:
                raise ExplorerError(
                    FailureCode.COORDINATE_ERROR,
                    "研究区与输出栅格没有重叠范围",
                ) from exc
            values = clipped[0].astype("float32")
            if not np.any(np.isfinite(values) & (values != nodata)):
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    "研究区范围内没有有效栅格像元",
                )
            tags = source.tags()
            crs = source.crs
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            temporary,
            "w",
            driver="COG",
            height=values.shape[0],
            width=values.shape[1],
            count=1,
            dtype="float32",
            crs=crs,
            transform=clipped_transform,
            nodata=nodata,
            compress="DEFLATE",
            blocksize=256,
            overview_resampling="AVERAGE",
        ) as destination:
            destination.write(values, 1)
            destination.update_tags(
                **tags,
                clipped="true",
                clip_source=str(region.path),
                clip_features=region.feature_count,
            )
        with rasterio.open(temporary) as validation:
            if validation.crs is None or validation.count != 1:
                raise ExplorerError(FailureCode.VALIDATION_FAILED, "裁剪 TIF 输出校验失败")
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _clipping_report(region: VectorRegionInfo | None) -> dict[str, object]:
    if region is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "path": str(region.path.resolve()),
        "source_crs": region.source_crs,
        "bounds_wgs84": region.bounds_wgs84,
        "feature_count": region.feature_count,
        "layer_count": region.layer_count,
        "companion_files": [str(path.resolve()) for path in region.companion_files],
    }
