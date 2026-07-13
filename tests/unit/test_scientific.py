import numpy as np
import pandas as pd
import pytest
import xarray as xr

from cmip_explorer.domain.errors import ExplorerError
from cmip_explorer.infrastructure.processing.scientific import (
    annual_aggregate,
    convert_units,
    stitch_time_series,
)


def test_monthly_annual_mean_is_weighted_by_days() -> None:
    time = pd.date_range("2001-01-01", periods=12, freq="MS")
    values = np.arange(12, dtype=float)
    data = xr.DataArray(values, dims=("time",), coords={"time": time}, attrs={"units": "K"})
    result = annual_aggregate(data)
    expected = np.average(values, weights=time.days_in_month)
    assert np.isclose(result.sel(year=2001), expected)


def test_kelvin_to_celsius_is_explicit() -> None:
    data = xr.DataArray([273.15, 274.15], attrs={"units": "K"})
    result = convert_units(data, "degC")
    assert np.allclose(result, [0.0, 1.0])
    assert result.attrs["units"] == "degC"


def test_stitch_rejects_missing_months() -> None:
    time = pd.date_range("2001-01-01", periods=11, freq="MS")
    dataset = xr.Dataset(
        {"tas": (("time", "lat", "lon"), np.ones((11, 2, 2)), {"units": "K"})},
        coords={"time": time, "lat": [1.0, 0.0], "lon": [100.0, 101.0]},
    )
    with pytest.raises(ExplorerError, match="missing months"):
        stitch_time_series([dataset], "tas", 2001, 2001)


def test_stitch_rejects_changed_spatial_grid() -> None:
    first_time = pd.date_range("2000-01-01", periods=12, freq="MS")
    second_time = pd.date_range("2001-01-01", periods=12, freq="MS")

    def dataset(time, longitude):
        return xr.Dataset(
            {"tas": (("time", "lat", "lon"), np.ones((12, 2, 2)), {"units": "K"})},
            coords={"time": time, "lat": [1.0, 0.0], "lon": longitude},
        )

    with pytest.raises(ExplorerError, match="different lon coordinates"):
        stitch_time_series(
            [dataset(first_time, [100.0, 101.0]), dataset(second_time, [100.5, 101.5])],
            "tas",
            2000,
            2001,
        )
