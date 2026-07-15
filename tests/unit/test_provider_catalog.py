from __future__ import annotations

import httpx

from cmip_explorer.infrastructure.providers import (
    ProviderVariable,
    discover_provider_variables,
    filter_provider_variables,
)


def test_provider_variable_filter_matches_chinese_aliases() -> None:
    variables = (
        ProviderVariable("pr", "Precipitation", "降水量", aliases=("降雨",)),
        ProviderVariable("tas", "Air Temperature", "气温"),
    )

    assert filter_provider_variables(variables, "降雨")[0].id == "pr"
    assert filter_provider_variables(variables, "temperature")[0].id == "tas"


async def test_cds_variables_are_discovered_from_current_public_form() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/collections/projections-cmip6"):
            return httpx.Response(
                200,
                json={
                    "links": [
                        {
                            "rel": "form",
                            "href": "https://example.test/forms/cmip6.json",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json=[
                {
                    "name": "variable",
                    "details": {
                        "values": ["near_surface_air_temperature", "new_variable"],
                        "labels": {
                            "near_surface_air_temperature": "Near-surface air temperature",
                            "new_variable": "New Variable",
                        },
                    },
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        variables = await discover_provider_variables(
            "cds", "projections-cmip6", client
        )

    assert variables[0].chinese_name == "近地表气温"
    assert variables[1].chinese_name is None
    assert variables[1].english_name == "New Variable"


async def test_power_variables_keep_unknown_api_parameters_in_english() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "T2M": {"name": "Temperature at 2 Meters", "units": "C"},
                    "FUTURE_CODE": {"name": "Future Parameter", "units": "1"},
                },
            )
        )
    ) as client:
        variables = await discover_provider_variables("power", "daily", client)

    assert variables[0].chinese_name == "2 米气温"
    assert variables[1].display_name == "Future Parameter"


async def test_open_meteo_variables_use_clear_chinese_labels() -> None:
    variables = await discover_provider_variables("openmeteo", "climate")

    by_id = {variable.id: variable for variable in variables}
    assert by_id["temperature_2m_mean"].display_name == "2 米平均气温"
    assert by_id["precipitation_sum"].display_name == "总降水量"
    assert by_id["soil_moisture_0_to_10cm_mean"].units == "m³/m³"


async def test_keyless_social_sources_offer_curated_chinese_variables() -> None:
    world_bank = await discover_provider_variables("worldbank", "china-indicators")
    who = await discover_provider_variables("who", "china-health")
    worldpop = await discover_provider_variables("worldpop", "china-population-1km")

    assert next(item for item in world_bank if item.id == "SP.POP.TOTL").display_name == "总人口"
    assert next(item for item in who if item.id == "WHS6_102").display_name == "每万人医院床位数"
    assert worldpop[0].display_name == "人口空间分布"
