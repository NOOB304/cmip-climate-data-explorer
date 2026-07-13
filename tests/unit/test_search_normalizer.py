from cmip_explorer.domain.models import SearchRequest
from cmip_explorer.infrastructure.search.backends import _overlaps_requested_years
from cmip_explorer.infrastructure.search.normalizer import (
    merge_logical_files,
    normalize_solr_document,
    parse_access_endpoint,
)


def test_opendap_html_suffix_is_removed() -> None:
    endpoint = parse_access_endpoint(
        "https://example.test/thredds/dodsC/file.nc.html|application/opendap-html|OPENDAP"
    )
    assert endpoint.url.endswith("file.nc")
    assert endpoint.service == "OPENDAP"
    assert endpoint.secure is True


def test_filename_time_range_is_used_when_api_dates_are_missing() -> None:
    item = normalize_solr_document(
        {
            "id": "CMIP6.example.tas_Amon_model_ssp245_r1i1p1f1_gn_201501-210012.nc|node",
            "title": "tas_Amon_model_ssp245_r1i1p1f1_gn_201501-210012.nc",
            "master_id": "CMIP6.example.file",
            "data_node": "node",
            "replica": False,
        },
        "test",
    )
    assert item.temporal.start == "201501"
    assert item.temporal.end == "210012"
    assert item.temporal.source == "filename"


def test_mirrors_are_merged_under_one_logical_file() -> None:
    base = {
        "title": "tas_Amon_model_ssp245_r1i1p1f1_gn_201501-210012.nc",
        "master_id": "CMIP6.example.file",
        "replica": False,
    }
    left = normalize_solr_document({**base, "id": "x|node-a", "data_node": "node-a"}, "a")
    right = normalize_solr_document({**base, "id": "x|node-b", "data_node": "node-b"}, "b")
    merged = merge_logical_files((left, right))
    assert len(merged) == 1
    assert {item.data_node for item in merged[0].replicas} == {"node-a", "node-b"}


def test_filename_time_coverage_is_used_for_client_year_filter() -> None:
    item = normalize_solr_document(
        {
            "id": "x|node",
            "title": "tas_Amon_model_ssp245_r1i1p1f1_gn_201501-210012.nc",
            "master_id": "x",
            "data_node": "node",
        },
        "test",
    )
    assert _overlaps_requested_years(item, SearchRequest(start_year=2000, end_year=2100))
    assert not _overlaps_requested_years(item, SearchRequest(start_year=1900, end_year=1950))
