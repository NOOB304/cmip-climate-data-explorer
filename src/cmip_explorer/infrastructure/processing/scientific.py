from __future__ import annotations

import numpy as np
import xarray as xr

from cmip_explorer.domain.enums import FailureCode
from cmip_explorer.domain.errors import ExplorerError


def stitch_time_series(
    datasets: list[xr.Dataset], variable_id: str, start_year: int, end_year: int
) -> xr.DataArray:
    arrays = []
    for dataset in datasets:
        if variable_id not in dataset:
            raise ExplorerError(
                FailureCode.VARIABLE_MISSING,
                f"{variable_id} is missing from an input dataset",
            )
        arrays.append(dataset[variable_id])
    _validate_array_compatibility(arrays)
    combined = xr.concat(arrays, dim="time").sortby("time")
    years = combined.time.dt.year.values
    keep = (years >= start_year) & (years <= end_year)
    combined = combined.isel(time=np.flatnonzero(keep))
    if combined.sizes.get("time", 0) == 0:
        raise ExplorerError(FailureCode.TIME_UNAVAILABLE, "no requested timesteps were found")

    numeric = _time_keys(combined.time)
    _, first_indices, counts = np.unique(numeric, return_index=True, return_counts=True)
    if np.any(counts > 1):
        for key in np.unique(numeric[counts_for_values(numeric) > 1]):
            duplicates = combined.isel(time=np.flatnonzero(numeric == key)).load()
            reference = duplicates.isel(time=0)
            if not bool((duplicates == reference).all(skipna=True)):
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    "overlapping input files contain conflicting values",
                    details={"time_key": str(key)},
                )
        combined = combined.isel(time=np.sort(first_indices))
    _validate_time_continuity(combined, start_year, end_year)
    return combined


def annual_aggregate(data: xr.DataArray, statistic: str = "mean") -> xr.DataArray:
    counts = data.time.dt.year.groupby("time.year").count()
    monthly = bool(counts.size and int(counts.max()) <= 12)
    if statistic == "mean" and monthly:
        weights = data.time.dt.days_in_month.astype("float64")
        numerator = (data * weights).groupby("time.year").sum("time", skipna=True)
        denominator = weights.where(data.notnull()).groupby("time.year").sum("time", skipna=True)
        return numerator / denominator
    grouped = data.groupby("time.year")
    if statistic == "mean":
        return grouped.mean("time", skipna=True)
    if statistic == "sum":
        return grouped.sum("time", skipna=True)
    if statistic == "min":
        return grouped.min("time", skipna=True)
    if statistic == "max":
        return grouped.max("time", skipna=True)
    raise ValueError(f"unsupported statistic: {statistic}")


def convert_units(data: xr.DataArray, target_unit: str) -> xr.DataArray:
    source = str(data.attrs.get("units", "")).strip()
    normalized_target = target_unit.strip()
    if source == normalized_target:
        return data
    if source in {"K", "kelvin", "Kelvin"} and normalized_target in {
        "degC",
        "°C",
        "celsius",
    }:
        converted = data - 273.15
        converted.attrs = dict(data.attrs)
        converted.attrs["units"] = "degC"
        converted.attrs["unit_conversion"] = "K - 273.15"
        return converted
    if source in {"kg m-2 s-1", "kg m-2 s^-1"} and normalized_target == "mm/day":
        converted = data * 86400.0
        converted.attrs = dict(data.attrs)
        converted.attrs["units"] = "mm/day"
        converted.attrs["unit_conversion"] = "1 kg m-2 s-1 = 86400 mm/day"
        return converted
    raise ExplorerError(
        FailureCode.VALIDATION_FAILED,
        f"unit conversion is not approved: {source!r} -> {target_unit!r}",
    )


def _time_keys(time: xr.DataArray) -> np.ndarray:
    return np.array([str(value) for value in time.values])


def counts_for_values(values: np.ndarray) -> np.ndarray:
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    return counts[inverse]


def _validate_array_compatibility(arrays: list[xr.DataArray]) -> None:
    reference = arrays[0]
    reference_dims = tuple(dimension for dimension in reference.dims if dimension != "time")
    reference_calendar = str(reference.time.dt.calendar)
    for candidate in arrays[1:]:
        candidate_dims = tuple(dimension for dimension in candidate.dims if dimension != "time")
        if candidate_dims != reference_dims:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "input files use different spatial dimensions",
                details={"reference": reference_dims, "candidate": candidate_dims},
            )
        for dimension in reference_dims:
            if reference.sizes[dimension] != candidate.sizes[dimension]:
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    f"input files use different {dimension} grid sizes",
                )
            if (
                dimension in reference.coords
                and dimension in candidate.coords
                and not np.array_equal(
                    reference[dimension].values, candidate[dimension].values, equal_nan=True
                )
            ):
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    f"input files use different {dimension} coordinates",
                )
        for name in ("units", "cell_methods"):
            if str(reference.attrs.get(name, "")) != str(candidate.attrs.get(name, "")):
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    f"input files use incompatible {name}",
                )
        if str(candidate.time.dt.calendar) != reference_calendar:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "input files use incompatible calendars",
                details={
                    "reference": reference_calendar,
                    "candidate": str(candidate.time.dt.calendar),
                },
            )


def _validate_time_continuity(data: xr.DataArray, start_year: int, end_year: int) -> None:
    years = data.time.dt.year.values.astype(int)
    months = data.time.dt.month.values.astype(int)
    expected_years = np.arange(start_year, end_year + 1)
    if not np.array_equal(np.unique(years), expected_years):
        missing = sorted(set(expected_years.tolist()) - set(np.unique(years).tolist()))
        raise ExplorerError(
            FailureCode.TIME_UNAVAILABLE,
            "input time series does not cover every requested year",
            details={"missing_years": missing},
        )
    counts = np.array([np.count_nonzero(years == year) for year in expected_years])
    if 1 < int(counts.max()) <= 12:
        incomplete = {
            int(year): sorted(set(range(1, 13)) - set(months[years == year].tolist()))
            for year in expected_years
            if set(months[years == year].tolist()) != set(range(1, 13))
        }
        if incomplete:
            raise ExplorerError(
                FailureCode.TIME_UNAVAILABLE,
                "monthly input contains missing months",
                details={"missing_months": incomplete},
            )
