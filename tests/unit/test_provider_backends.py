from __future__ import annotations

import httpx

from cmip_explorer.domain.enums import BackendKind
from cmip_explorer.domain.models import (
    Backend,
    BackendCapabilities,
    FacetConstraint,
    SearchRequest,
)
from cmip_explorer.infrastructure.search.provider_backends import (
    NexStacBackend,
    NoaaNceiBackend,
    PowerBackend,
)


def _definition(backend_id: str, url: str) -> Backend:
    return Backend(
        id=backend_id,
        name=backend_id,
        kind=BackendKind.STAC,
        base_url=url,
        capabilities=BackendCapabilities(),
    )


async def test_nex_stac_groups_selected_assets_into_downloadable_series() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "id": "Model.ssp245.2020",
                "properties": {
                    "cmip6:model": "Model",
                    "cmip6:scenario": "ssp245",
                    "start_datetime": "2020-01-01T12:00:00Z",
                    "end_datetime": "2020-12-31T12:00:00Z",
                },
                "assets": {
                    "pr": {
                        "href": (
                            "https://blob.test/NEX/GDDP-CMIP6/Model/ssp245/"
                            "r1i1p1f1/pr/pr_day_Model_ssp245_2020.nc"
                        ),
                        "type": "application/netcdf",
                    },
                    "tas": {"href": "https://blob.test/tas.nc"},
                },
            }
        ],
        "links": [{"rel": "next", "href": "https://stac.test/next"}],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    ) as client:
        backend = NexStacBackend(
            _definition("aws", "https://stac.test"), asset_source="aws", client=client
        )
        page = await backend.search(
            SearchRequest(
                provider_id="aws",
                product_id="nex-gddp-cmip6",
                facets=(FacetConstraint(name="variable_id", values=("pr",)),),
                start_year=2020,
                end_year=2020,
            )
        )

    assert len(page.files) == 1
    file = page.files[0]
    assert file.provider_id == "aws"
    assert file.variable_id == "pr"
    assert file.file_count == 1
    physical_file = file.series_members[0]
    assert physical_file.replicas[0].endpoints[0].service == "HTTPServer"
    assert "nex-gddp-cmip6.s3.us-west-2.amazonaws.com" in (
        physical_file.replicas[0].endpoints[0].url
    )
    assert page.next_cursors["aws"] is None


async def test_nex_stac_combines_all_year_pages_and_paginates_by_series() -> None:
    def feature(model: str, year: int) -> dict:
        return {
            "id": f"{model}.ssp245.{year}",
            "properties": {
                "cmip6:model": model,
                "cmip6:scenario": "ssp245",
                "start_datetime": f"{year}-01-01T12:00:00Z",
                "end_datetime": f"{year}-12-31T12:00:00Z",
            },
            "assets": {
                "pr": {
                    "href": (
                        f"https://blob.test/NEX/GDDP-CMIP6/{model}/ssp245/"
                        f"r1i1p1f1/pr/pr_day_{model}_ssp245_{year}.nc"
                    ),
                    "type": "application/netcdf",
                    "file:size": 100,
                }
            },
        }

    first_payload = {
        "type": "FeatureCollection",
        "features": [feature("Model-A", 2020), feature("Model-B", 2020)],
        "links": [{"rel": "next", "href": "https://series.test/page-2"}],
    }
    second_payload = {
        "type": "FeatureCollection",
        "features": [feature("Model-A", 2021), feature("Model-B", 2021)],
        "links": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = second_payload if request.url.path == "/page-2" else first_payload
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = NexStacBackend(
            _definition("series", "https://series.test"),
            asset_source="planetary",
            client=client,
        )
        request = SearchRequest(
            provider_id="series",
            product_id="nex-gddp-cmip6",
            facets=(FacetConstraint(name="variable_id", values=("pr",)),),
            start_year=2020,
            end_year=2021,
            page_size=1,
        )
        first_page = await backend.search(request)
        second_page = await backend.search(request, first_page.next_cursors["series"])

    assert first_page.known_unique_count == 2
    assert first_page.raw_total_by_backend["series"] == 4
    assert first_page.files[0].file_count == 2
    assert first_page.files[0].temporal.start == "2020-01-01T12:00:00Z"
    assert first_page.files[0].temporal.end == "2021-12-31T12:00:00Z"
    assert first_page.files[0].size_bytes == 200
    assert second_page.files[0].file_count == 2
    assert second_page.next_cursors["series"] is None


async def test_generated_api_backends_create_direct_csv_downloads() -> None:
    power = PowerBackend(_definition("power", "https://power.test/api/temporal"))
    noaa = NoaaNceiBackend(_definition("noaa", "https://noaa.test/data/v1"))
    try:
        power_page = await power.search(
            SearchRequest(
                provider_id="power",
                product_id="daily",
                facets=(FacetConstraint(name="variable_id", values=("T2M",)),),
                start_year=2024,
                end_year=2024,
                parameters={"latitude": "39.9", "longitude": "116.4"},
            )
        )
        noaa_page = await noaa.search(
            SearchRequest(
                provider_id="noaa",
                product_id="daily-summaries",
                facets=(FacetConstraint(name="variable_id", values=("PRCP",)),),
                start_year=2024,
                end_year=2024,
                parameters={"station": "USW00094728"},
            )
        )
    finally:
        await power.close()
        await noaa.close()

    power_url = power_page.files[0].replicas[0].endpoints[0].url
    noaa_url = noaa_page.files[0].replicas[0].endpoints[0].url
    assert "parameters=T2M" in power_url
    assert "latitude=39.9" in power_url
    assert "dataset=daily-summaries" in noaa_url
    assert "stations=USW00094728" in noaa_url
