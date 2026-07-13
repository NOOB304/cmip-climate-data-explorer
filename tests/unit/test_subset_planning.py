import numpy as np
import xarray as xr

from cmip_explorer.domain.models import AccessEndpoint, LogicalFile, Replica
from cmip_explorer.infrastructure.subset.opendap import (
    _coordinate_names,
    _curvilinear_indices,
    _indices_between,
    _longitude_index_groups,
)
from cmip_explorer.infrastructure.subset.service import _opendap_candidates


def test_coordinate_detection_uses_cf_metadata() -> None:
    dataset = xr.Dataset(
        {"tas": (("t", "y", "x"), np.zeros((2, 2, 3)))},
        coords={
            "t": ("t", [0, 1], {"axis": "T"}),
            "y": ("y", [26.0, 27.0], {"standard_name": "latitude"}),
            "x": ("x", [106.0, 107.0, 108.0], {"standard_name": "longitude"}),
        },
    )
    assert _coordinate_names(dataset, "tas") == ("t", "y", "x")


def test_curvilinear_coordinate_detection_and_spatial_envelope() -> None:
    latitudes = np.array([[24.0, 24.2, 24.4], [26.0, 26.2, 26.4], [28.0, 28.2, 28.4]])
    longitudes = np.array([[102.0, 105.0, 108.0], [102.2, 105.2, 108.2], [102.4, 105.4, 108.4]])
    dataset = xr.Dataset(
        {"tas": (("time", "j", "i"), np.zeros((1, 3, 3)))},
        coords={
            "time": ("time", [0], {"axis": "T"}),
            "lat": (("j", "i"), latitudes, {"standard_name": "latitude"}),
            "lon": (("j", "i"), longitudes, {"standard_name": "longitude"}),
        },
    )
    assert _coordinate_names(dataset, "tas") == ("time", "lat", "lon")
    rows, columns = _curvilinear_indices(latitudes, longitudes, (103.0, 25.0, 109.0, 29.0))
    assert rows.tolist() == [1, 2]
    assert columns.tolist() == [1, 2]


def test_descending_latitude_indices_are_found() -> None:
    indices = _indices_between(np.array([30.0, 29.0, 28.0, 27.0, 26.0]), 26.5, 29.5)
    assert indices.tolist() == [1, 2, 3]


def test_longitude_window_supports_zero_to_360() -> None:
    values = np.array([0.0, 90.0, 180.0, 270.0, 359.0])
    groups = _longitude_index_groups(values, -100.0, -80.0)
    assert len(groups) == 1
    assert groups[0].tolist() == [3]


def test_http_opendap_metadata_is_upgraded_to_https_without_plaintext_fallback() -> None:
    file = LogicalFile(
        logical_key="x",
        filename="x.nc",
        replicas=(
            Replica(
                data_node="node",
                backend_id="test",
                replica=True,
                endpoints=(
                    AccessEndpoint(
                        url="http://node.test/thredds/dodsC/x.nc",
                        service="OPENDAP",
                        secure=False,
                    ),
                ),
            ),
        ),
    )
    candidates = _opendap_candidates(file, allow_insecure_http=False)
    assert [item.url for item in candidates] == ["https://node.test/thredds/dodsC/x.nc"]
