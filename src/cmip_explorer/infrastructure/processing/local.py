from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import xarray as xr
from rasterio.transform import from_origin
from scipy import stats

from cmip_explorer.domain.enums import FailureCode
from cmip_explorer.domain.errors import ExplorerError

from .pipeline import _orient_grid, _rectify_curvilinear, _spatial_coordinates


@dataclass(frozen=True, slots=True)
class LocalDatasetInfo:
    variables: tuple[str, ...]
    variable_labels: dict[str, str]
    start_year: int | None
    end_year: int | None
    monthly: bool
    frequency_label: str
    temporal_resolution: str
    can_aggregate_annually: bool
    units: dict[str, str]


def inspect_netcdf(paths: list[Path]) -> LocalDatasetInfo:
    if not paths:
        raise ValueError("请至少选择一个 NetCDF 文件")
    datasets = [xr.open_dataset(path, decode_times=True) for path in paths]
    try:
        common = set(datasets[0].data_vars)
        for dataset in datasets[1:]:
            common &= set(dataset.data_vars)
        variables = tuple(
            sorted(name for name in common if _has_spatial_dimensions(datasets[0][name]))
        )
        if not variables:
            raise ExplorerError(FailureCode.VARIABLE_MISSING, "文件中没有可转换的空间变量")
        labels = {
            name: str(
                datasets[0][name].attrs.get("long_name")
                or datasets[0][name].attrs.get("standard_name")
                or name
            )
            for name in variables
        }
        units = {name: str(datasets[0][name].attrs.get("units", "")) for name in variables}
        years: list[int] = []
        counts_per_year: list[int] = []
        monthly_candidate = True
        has_subdaily_time = False
        for dataset in datasets:
            if "time" not in dataset.coords:
                continue
            values = dataset.time.dt.year.values.astype(int)
            years.extend(values.tolist())
            if values.size:
                for year in np.unique(values):
                    positions = np.flatnonzero(values == year)
                    months = dataset.time.dt.month.values[positions].astype(int)
                    count = len(positions)
                    counts_per_year.append(count)
                    monthly_candidate = monthly_candidate and (
                        1 < count <= 12 and len(np.unique(months)) == count
                    )
                hours = dataset.time.dt.hour.values.astype(int)
                has_subdaily_time = has_subdaily_time or bool(np.any(hours != hours[0]))
        monthly = bool(counts_per_year) and monthly_candidate
        if not counts_per_year:
            frequency_label = "无时间维度"
            temporal_resolution = "static"
        elif monthly:
            frequency_label = "月数据"
            temporal_resolution = "monthly"
        elif max(counts_per_year) == 1:
            frequency_label = "年数据"
            temporal_resolution = "annual"
        elif has_subdaily_time or max(counts_per_year) > 366:
            frequency_label = "小时级数据"
            temporal_resolution = "subdaily"
        else:
            frequency_label = "日数据"
            temporal_resolution = "daily"
        return LocalDatasetInfo(
            variables=variables,
            variable_labels=labels,
            start_year=min(years) if years else None,
            end_year=max(years) if years else None,
            monthly=monthly,
            frequency_label=frequency_label,
            temporal_resolution=temporal_resolution,
            can_aggregate_annually=temporal_resolution in {"monthly", "daily"},
            units=units,
        )
    finally:
        for dataset in datasets:
            dataset.close()


def process_local_netcdf(
    paths: list[Path],
    variable: str,
    output_dir: Path,
    *,
    months: tuple[int, ...] = tuple(range(1, 13)),
    statistic: str = "mean",
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[Path, ...]:
    datasets = [xr.open_dataset(path, decode_times=True) for path in paths]
    try:
        data = _combine_variable(datasets, variable)
        annual = _to_annual(data, months, statistic)
        annual, _metadata = _rectify_curvilinear(annual, (-180.0, -90.0, 180.0, 90.0), None)
        y_name, x_name = _spatial_coordinates(annual)
        annual = _orient_grid(annual, y_name, x_name)
        latitudes = annual[y_name].values.astype(float)
        longitudes = annual[x_name].values.astype(float)
        transform = _grid_transform(latitudes, longitudes)
        output_dir.mkdir(parents=True, exist_ok=True)
        years = [int(year) for year in annual.year.values]
        artifacts: list[Path] = []
        selected_months = tuple(sorted(set(months)))
        month_suffix = (
            ""
            if selected_months == tuple(range(1, 13))
            else "_m" + "-".join(f"{month:02d}" for month in selected_months)
        )
        for index, year in enumerate(years, start=1):
            output = output_dir / f"{_safe(variable)}_{year}_{statistic}{month_suffix}.tif"
            values = annual.sel(year=year).values.astype("float32")
            _validate_raster_frame(values)
            _write_geotiff(
                output,
                values,
                transform,
                variable,
                str(data.attrs.get("units", "")),
                statistic,
                {"year": year, "selected_months": ",".join(map(str, selected_months))},
            )
            artifacts.append(output)
            if progress:
                progress(index, len(years), output.name)
        return tuple(artifacts)
    finally:
        for dataset in datasets:
            dataset.close()


def convert_netcdf_to_geotiffs(
    path: Path,
    output_dir: Path,
    variable: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[Path, ...]:
    """Convert every source timestep without temporal aggregation."""
    return convert_local_netcdf_to_geotiffs(
        [path],
        output_dir,
        variable,
        progress=progress,
        cancelled=cancelled,
    )


def convert_local_netcdf_to_geotiffs(
    paths: list[Path],
    output_dir: Path,
    variable: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[Path, ...]:
    """Convert selected local NetCDF files without temporal aggregation."""
    if not paths:
        raise ValueError("请至少选择一个 NetCDF 文件")
    datasets = [xr.open_dataset(path, decode_times=True) for path in paths]
    try:
        candidates = sorted(
            name
            for name in set.intersection(*(set(dataset.data_vars) for dataset in datasets))
            if _has_spatial_dimensions(datasets[0][name])
        )
        selected = variable if variable in candidates else (candidates[0] if candidates else None)
        if selected is None:
            raise ExplorerError(FailureCode.VARIABLE_MISSING, "文件中没有可转换的空间变量")
        data = _combine_variable(datasets, selected)
        y_name, x_name = _spatial_coordinates(data)
        data = _orient_grid(data, y_name, x_name)
        latitudes = data[y_name].values.astype(float)
        longitudes = data[x_name].values.astype(float)
        if latitudes.ndim != 1 or longitudes.ndim != 1:
            raise ExplorerError(
                FailureCode.UNSUPPORTED_GRID,
                "自动转换暂不支持曲线网格, 请在“文件处理”中转换该文件",
            )
        transform = _grid_transform(latitudes, longitudes)
        output_dir.mkdir(parents=True, exist_ok=True)
        if "time" in data.dims:
            indices = range(data.sizes["time"])
            labels = [_time_label(value) for value in data.time.values]
        else:
            indices = range(1)
            labels = ["static"]
        if len(labels) != len(set(labels)):
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "多个时间点会生成相同的 TIF 文件名, 请检查输入文件的时间坐标",
            )
        artifacts: list[Path] = []
        total = len(labels)
        for offset, label in zip(indices, labels, strict=True):
            if cancelled and cancelled():
                raise InterruptedError("GeoTIFF conversion was cancelled")
            frame = data.isel(time=offset) if "time" in data.dims else data
            values = frame.values.astype("float32")
            _validate_raster_frame(values)
            output = output_dir / f"{_safe(selected)}_{label}.tif"
            _write_geotiff(
                output,
                values,
                transform,
                selected,
                str(data.attrs.get("units", "")),
                "source-timestep",
                {"source_time": label},
            )
            artifacts.append(output)
            if progress:
                progress(len(artifacts), total, output.name)
        return tuple(artifacts)
    finally:
        for dataset in datasets:
            dataset.close()


def _to_annual(data: xr.DataArray, months: tuple[int, ...], statistic: str) -> xr.DataArray:
    if "time" not in data.dims:
        return data.expand_dims(year=[0])
    if not months:
        raise ValueError("请至少选择一个月份")
    selected = data.where(data.time.dt.month.isin(months), drop=True)
    if selected.sizes.get("time", 0) == 0:
        raise ExplorerError(FailureCode.TIME_UNAVAILABLE, "所选月份没有数据")
    grouped = selected.groupby("time.year")
    if statistic == "mean":
        if _looks_monthly(data.time):
            weights = selected.time.dt.days_in_month.astype("float64")
            numerator = (selected * weights).groupby("time.year").sum(
                "time", skipna=True, min_count=1
            )
            denominator = weights.where(selected.notnull()).groupby("time.year").sum(
                "time", skipna=True, min_count=1
            )
            result = numerator / denominator
        else:
            result = grouped.mean("time", skipna=True)
    elif statistic == "sum":
        result = grouped.sum("time", skipna=True, min_count=1)
    elif statistic == "max":
        result = grouped.max("time", skipna=True)
    elif statistic == "min":
        result = grouped.min("time", skipna=True)
    elif statistic == "mode":
        frames = []
        years = []
        for year, frame in grouped:
            mode = stats.mode(
                frame.values,
                axis=frame.get_axis_num("time"),
                nan_policy="omit",
                keepdims=False,
            ).mode
            template = frame.isel(time=0, drop=True)
            frames.append(xr.DataArray(mode, coords=template.coords, dims=template.dims))
            years.append(int(year))
        result = xr.concat(frames, dim=xr.IndexVariable("year", years))
    else:
        raise ValueError(f"不支持的年度合成方式: {statistic}")
    result.attrs = dict(data.attrs)
    return result


def _combine_variable(datasets: list[xr.Dataset], variable: str) -> xr.DataArray:
    arrays: list[xr.DataArray] = []
    for dataset in datasets:
        if variable not in dataset:
            raise ExplorerError(FailureCode.VARIABLE_MISSING, f"{variable} 不在所选文件中")
        arrays.append(dataset[variable])
    if len(arrays) == 1:
        data = arrays[0]
    else:
        if any("time" not in array.dims for array in arrays):
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "多个无时间维度的文件不能合并, 请一次处理一个文件",
            )
        try:
            data = xr.concat(arrays, dim="time", join="exact").sortby("time")
        except (ValueError, TypeError) as exc:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "所选文件的经纬度网格或变量维度不一致, 不能一起处理",
            ) from exc
    if "time" in data.dims:
        data = data.sortby("time")
        keys = np.array([str(value) for value in data.time.values])
        if len(keys) != len(np.unique(keys)):
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "所选文件包含重复时间, 请移除重叠文件后重试",
            )
    return data


def _looks_monthly(time: xr.DataArray) -> bool:
    years = time.dt.year.values.astype(int)
    months = time.dt.month.values.astype(int)
    if not len(years):
        return False
    for year in np.unique(years):
        year_months = months[years == year]
        if not 1 <= len(year_months) <= 12 or len(np.unique(year_months)) != len(year_months):
            return False
    return True


def _has_spatial_dimensions(data: xr.DataArray) -> bool:
    names = {name.lower() for name in (*data.dims, *data.coords)}
    has_y = bool(names & {"lat", "latitude", "y"})
    has_x = bool(names & {"lon", "longitude", "x"})
    return has_y and has_x


def _grid_transform(latitudes: np.ndarray, longitudes: np.ndarray):
    if len(latitudes) < 2 or len(longitudes) < 2:
        raise ExplorerError(FailureCode.COORDINATE_ERROR, "经纬度网格至少需要两个像元")
    dx = float(np.median(np.abs(np.diff(longitudes))))
    dy = float(np.median(np.abs(np.diff(latitudes))))
    return from_origin(longitudes[0] - dx / 2, latitudes[0] + dy / 2, dx, dy)


def _write_geotiff(
    path: Path,
    values: np.ndarray,
    transform,
    variable: str,
    units: str,
    statistic: str,
    metadata: dict[str, object] | None = None,
    *,
    crs: str = "EPSG:4326",
) -> None:
    nodata = -9999.0
    values = np.where(np.isfinite(values), values, nodata).astype("float32")
    temporary = path.with_suffix(".tif.part")
    temporary.unlink(missing_ok=True)
    with rasterio.open(
        temporary,
        "w",
        driver="COG",
        height=values.shape[-2],
        width=values.shape[-1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="DEFLATE",
        blocksize=256,
        overview_resampling="AVERAGE",
    ) as destination:
        destination.write(values, 1)
        destination.update_tags(
            variable_id=variable,
            units=units,
            statistic=statistic,
            **{key: str(value) for key, value in (metadata or {}).items()},
        )
    with rasterio.open(temporary) as validation:
        if validation.crs is None or validation.count != 1:
            raise ExplorerError(FailureCode.VALIDATION_FAILED, "GeoTIFF 输出校验失败")
    os.replace(temporary, path)


def _validate_raster_frame(values: np.ndarray) -> None:
    if values.ndim != 2:
        raise ExplorerError(
            FailureCode.UNSUPPORTED_GRID,
            "变量除时间和经纬度外还有其他维度, 当前不能直接转换为 TIF",
        )


def _safe(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-." else "-" for character in value
    )


def _time_label(value: object) -> str:
    text = str(value)
    date_text, separator, time_text = text.replace("T", " ").partition(" ")
    date_digits = "".join(character for character in date_text if character.isdigit())[:8]
    time_digits = "".join(character for character in time_text if character.isdigit())[:6]
    if date_digits and separator and time_digits and time_digits != "000000":
        return f"{date_digits}T{time_digits}"
    return date_digits or _safe(text)
