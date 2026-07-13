import json
import zipfile
from pathlib import Path

import pytest
from shapely import from_wkb

from cmip_explorer.infrastructure.region import RegionImporter


def test_geojson_region_is_normalized(tmp_path: Path) -> None:
    path = tmp_path / "region.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "region",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "test"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[106, 26], [107, 26], [107, 27], [106, 27], [106, 26]]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = RegionImporter().import_region(path)
    geometry = from_wkb(bytes.fromhex(result.region.geometry_wkb_hex))
    assert geometry.is_valid
    assert result.region.bbox == (106.0, 26.0, 107.0, 27.0)
    assert len(result.region.source_sha256) == 64


def test_unsafe_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.shp", b"bad")
    with pytest.raises(ValueError, match="unsafe path"):
        RegionImporter().list_layers(path)


def test_feature_listing_and_explicit_selection(tmp_path: Path) -> None:
    path = tmp_path / "regions.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "west"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[100, 20], [101, 20], [101, 21], [100, 21], [100, 20]]
                            ],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "east"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[105, 25], [106, 25], [106, 26], [105, 26], [105, 25]]
                            ],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    importer = RegionImporter()
    features = importer.list_features(path)
    assert [feature.properties["name"] for feature in features] == ["west", "east"]
    selected = importer.import_region(path, selected_feature_ids={features[1].id})
    assert selected.region.bbox == (105.0, 25.0, 106.0, 26.0)
    with pytest.raises(ValueError, match="no non-empty geometry"):
        importer.import_region(path, selected_feature_ids=set())
