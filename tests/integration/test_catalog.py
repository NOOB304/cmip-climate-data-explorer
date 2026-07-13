from pathlib import Path

from cmip_explorer.infrastructure.catalog import VariableCatalog


def _catalog_path() -> Path:
    return Path(__file__).parents[2] / "src" / "cmip_explorer" / "resources" / "catalog.db"


def test_official_catalog_contains_full_table_variable_keys() -> None:
    catalog = VariableCatalog(_catalog_path())
    results = catalog.search("tas", limit=30)
    assert len(results) >= 10
    assert any(item.table_id == "Amon" and item.variable_id == "tas" for item in results)


def test_chinese_alias_finds_near_surface_temperature() -> None:
    catalog = VariableCatalog(_catalog_path())
    results = catalog.search("近地表气温", limit=10)
    assert results
    assert results[0].variable_id == "tas"
    assert results[0].chinese_name == "近地表气温"


def test_grouped_search_returns_one_precipitation_option_with_all_frequencies() -> None:
    catalog = VariableCatalog(_catalog_path())
    matches = catalog.search_grouped("pr", limit=100)
    precipitation = [item for item in matches if item.variable_id == "pr"]

    assert len(precipitation) == 1
    assert {"mon", "day", "3hr", "6hr"}.issubset(precipitation[0].frequencies)
    assert {"Amon", "day", "3hr"}.issubset(precipitation[0].table_ids)


def test_equivalent_air_temperature_codes_are_grouped_but_surface_temperature_is_separate() -> None:
    catalog = VariableCatalog(_catalog_path())
    matches = catalog.search_grouped("Air Temperature", limit=100)
    upper_air = [item for item in matches if "ta" in item.variable_ids]

    assert len(upper_air) == 1
    assert {"ta", "ta27", "ta7h"}.issubset(upper_air[0].variable_ids)
    assert "tas" not in upper_air[0].variable_ids
