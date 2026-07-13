from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import xarray as xr

from cmip_explorer.domain.enums import FailureCode
from cmip_explorer.domain.errors import ExplorerError
from cmip_explorer.domain.models import AccessEndpoint, IndexWindow, RemoteSubsetCapability

_ARRAY_RE = re.compile(r"\b(?P<type>Float\d+|Int\d+|UInt\d+)\s+(?P<name>\w+)\[(?P<dims>[^;]+)\]")


@dataclass(frozen=True, slots=True)
class SubsetPlan:
    endpoint: AccessEndpoint
    variable_id: str
    time_name: str
    y_name: str
    x_name: str
    y_dim: str
    x_dim: str
    windows: tuple[IndexWindow, ...]
    source_shape: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class SubsetResult:
    path: Path
    bytes_written: int
    plan: SubsetPlan
    variables: tuple[str, ...]


class OpendapSubsetProvider:
    async def probe(self, endpoint: AccessEndpoint, variable_id: str) -> RemoteSubsetCapability:
        if endpoint.service.upper() != "OPENDAP":
            return RemoteSubsetCapability(
                endpoint=endpoint,
                available=False,
                protocol="unknown",
                reason="endpoint is not advertised as OPeNDAP",
            )
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(f"{endpoint.url}.dds")
                response.raise_for_status()
            arrays = {match.group("name") for match in _ARRAY_RE.finditer(response.text)}
            return RemoteSubsetCapability(
                endpoint=endpoint,
                available=variable_id in arrays,
                protocol="opendap2",
                supports_variable=variable_id in arrays,
                supports_time="time" in arrays,
                supports_space=bool(arrays & {"lat", "latitude"})
                and bool(arrays & {"lon", "longitude"}),
                tls_valid=endpoint.secure,
                reason=None
                if variable_id in arrays
                else f"variable {variable_id} is absent from DDS",
            )
        except httpx.TimeoutException as exc:
            raise ExplorerError(
                FailureCode.REMOTE_SUBSET_TIMEOUT,
                "OPeNDAP capability probe timed out",
                retriable=True,
                details={"endpoint": endpoint.url},
            ) from exc
        except httpx.HTTPError as exc:
            raise ExplorerError(
                FailureCode.SERVICE_ERROR,
                "OPeNDAP capability probe failed",
                retriable=True,
                details={"endpoint": endpoint.url, "error": str(exc)},
            ) from exc

    async def plan(
        self,
        endpoint: AccessEndpoint,
        variable_id: str,
        bbox: tuple[float, float, float, float],
        start_year: int,
        end_year: int,
    ) -> SubsetPlan:
        return await asyncio.to_thread(
            self._plan_sync, endpoint, variable_id, bbox, start_year, end_year
        )

    def _plan_sync(
        self,
        endpoint: AccessEndpoint,
        variable_id: str,
        bbox: tuple[float, float, float, float],
        start_year: int,
        end_year: int,
    ) -> SubsetPlan:
        with xr.open_dataset(endpoint.url, engine="pydap", chunks=None) as dataset:
            if variable_id not in dataset:
                raise ExplorerError(
                    FailureCode.VARIABLE_MISSING,
                    f"variable {variable_id} is not present",
                    details={"endpoint": endpoint.url},
                )
            time_name, y_name, x_name = _coordinate_names(dataset, variable_id)
            times = dataset[time_name].load()
            latitudes = dataset[y_name].load()
            longitudes = dataset[x_name].load()
            if latitudes.ndim == 1 and longitudes.ndim == 1:
                y_dim = latitudes.dims[0]
                x_dim = longitudes.dims[0]
                y_indices = _indices_between(latitudes.values, bbox[1], bbox[3])
                x_groups = _longitude_index_groups(longitudes.values, bbox[0], bbox[2])
            elif latitudes.ndim == 2 and longitudes.ndim == 2 and latitudes.dims == longitudes.dims:
                y_dim, x_dim = latitudes.dims
                y_indices, x_indices = _curvilinear_indices(
                    latitudes.values, longitudes.values, bbox
                )
                x_groups = (x_indices,) if len(x_indices) else ()
            else:
                raise ExplorerError(
                    FailureCode.UNSUPPORTED_GRID,
                    "latitude and longitude coordinates have incompatible dimensions",
                    details={"lat_ndim": latitudes.ndim, "lon_ndim": longitudes.ndim},
                )
            time_indices = np.flatnonzero(
                (times.dt.year.values >= start_year) & (times.dt.year.values <= end_year)
            )
            if not len(time_indices):
                raise ExplorerError(
                    FailureCode.TIME_UNAVAILABLE,
                    "requested years do not overlap the file",
                    details={"start_year": start_year, "end_year": end_year},
                )
            if not len(y_indices) or not x_groups:
                raise ExplorerError(
                    FailureCode.SPATIAL_UNAVAILABLE,
                    "research region does not overlap the file grid",
                    details={"bbox": bbox},
                )
            windows = tuple(
                IndexWindow(
                    time_start=int(time_indices.min()),
                    time_stop=int(time_indices.max()),
                    y_start=int(y_indices.min()),
                    y_stop=int(y_indices.max()),
                    x_start=int(indices.min()),
                    x_stop=int(indices.max()),
                )
                for indices in x_groups
            )
            shape = (
                int(dataset.sizes[time_name]),
                int(dataset.sizes[y_dim]),
                int(dataset.sizes[x_dim]),
            )
        return SubsetPlan(
            endpoint,
            variable_id,
            time_name,
            y_name,
            x_name,
            y_dim,
            x_dim,
            windows,
            shape,
        )

    async def fetch(self, plan: SubsetPlan, target: Path) -> SubsetResult:
        return await asyncio.to_thread(self._fetch_sync, plan, target)

    def _fetch_sync(self, plan: SubsetPlan, target: Path) -> SubsetResult:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.unlink(missing_ok=True)
        with xr.open_dataset(plan.endpoint.url, engine="pydap", chunks={}) as dataset:
            pieces = []
            for window in plan.windows:
                selected_names = tuple(dict.fromkeys((plan.variable_id, plan.y_name, plan.x_name)))
                subset = dataset[list(selected_names)].isel(
                    {
                        plan.time_name: slice(window.time_start, window.time_stop + 1),
                        plan.y_dim: slice(window.y_start, window.y_stop + 1),
                        plan.x_dim: slice(window.x_start, window.x_stop + 1),
                    }
                )
                pieces.append(subset)
            selected = pieces[0] if len(pieces) == 1 else xr.concat(pieces, dim=plan.x_dim)
            selected.load()
            _validate_subset(selected, plan)
            selected.to_netcdf(temporary, engine="h5netcdf")
        os.replace(temporary, target)
        return SubsetResult(
            path=target,
            bytes_written=target.stat().st_size,
            plan=plan,
            variables=tuple(selected.data_vars),
        )


def _coordinate_names(dataset: xr.Dataset, variable_id: str) -> tuple[str, str, str]:
    variable = dataset[variable_id]
    time_name = _find_coordinate(dataset, variable, "T", {"time"})
    y_name = _find_coordinate(dataset, variable, "Y", {"lat", "latitude"})
    x_name = _find_coordinate(dataset, variable, "X", {"lon", "longitude"})
    if not time_name or not y_name or not x_name:
        raise ExplorerError(
            FailureCode.COORDINATE_ERROR,
            "could not identify time/latitude/longitude coordinates",
            details={"variable_dims": variable.dims},
        )
    return time_name, y_name, x_name


def _find_coordinate(
    dataset: xr.Dataset, variable: xr.DataArray, axis: str, standard_names: set[str]
) -> str | None:
    for name in variable.dims:
        coordinate = dataset.get(name)
        if coordinate is None:
            continue
        if str(coordinate.attrs.get("axis", "")).upper() == axis:
            return name
        if str(coordinate.attrs.get("standard_name", "")).lower() in standard_names:
            return name
        if name.lower() in standard_names:
            return name
    for name, coordinate in dataset.coords.items():
        if not coordinate.dims or not set(coordinate.dims).issubset(variable.dims):
            continue
        if str(coordinate.attrs.get("axis", "")).upper() == axis:
            return name
        if str(coordinate.attrs.get("standard_name", "")).lower() in standard_names:
            return name
        if name.lower() in standard_names:
            return name
    return None


def _indices_between(values: np.ndarray, lower: float, upper: float) -> np.ndarray:
    low, high = sorted((lower, upper))
    return np.flatnonzero((values >= low) & (values <= high))


def _longitude_index_groups(values: np.ndarray, west: float, east: float) -> tuple[np.ndarray, ...]:
    uses_360 = float(np.nanmin(values)) >= 0 and float(np.nanmax(values)) > 180
    if uses_360:
        west %= 360
        east %= 360
    if west <= east:
        indices = np.flatnonzero((values >= west) & (values <= east))
        return (indices,) if len(indices) else ()
    left = np.flatnonzero(values >= west)
    right = np.flatnonzero(values <= east)
    return tuple(group for group in (left, right) if len(group))


def _curvilinear_indices(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    bbox: tuple[float, float, float, float],
) -> tuple[np.ndarray, np.ndarray]:
    west, south, east, north = bbox
    normalized = ((longitudes.astype(float) + 180.0) % 360.0) - 180.0
    west = ((west + 180.0) % 360.0) - 180.0
    east = ((east + 180.0) % 360.0) - 180.0
    longitude_match = (
        (normalized >= west) & (normalized <= east)
        if west <= east
        else (normalized >= west) | (normalized <= east)
    )
    mask = (
        np.isfinite(latitudes)
        & np.isfinite(normalized)
        & (latitudes >= min(south, north))
        & (latitudes <= max(south, north))
        & longitude_match
    )
    rows, columns = np.nonzero(mask)
    return np.unique(rows), np.unique(columns)


def _validate_subset(dataset: xr.Dataset, plan: SubsetPlan) -> None:
    if plan.variable_id not in dataset:
        raise ExplorerError(
            FailureCode.VARIABLE_MISSING, "subset response lost the target variable"
        )
    variable = dataset[plan.variable_id]
    if variable.size == 0:
        raise ExplorerError(FailureCode.VALIDATION_FAILED, "subset response is empty")
    source_size = int(np.prod(plan.source_shape))
    if variable.size >= source_size and any(size > 1 for size in plan.source_shape):
        raise ExplorerError(
            FailureCode.REMOTE_SUBSET_INVALID_RESPONSE,
            "remote response was not smaller than the source grid",
            details={"source_shape": plan.source_shape, "subset_shape": variable.shape},
        )
