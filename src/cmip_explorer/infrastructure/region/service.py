from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import fiona
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.ops import transform, unary_union

from cmip_explorer.domain.models import Region


@dataclass(frozen=True, slots=True)
class RegionLayer:
    name: str
    geometry_type: str
    crs: str | None
    feature_count: int
    properties: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegionImportResult:
    region: Region
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegionFeature:
    id: str
    properties: dict[str, Any]


class RegionImporter:
    supported_suffixes: ClassVar[frozenset[str]] = frozenset(
        {".shp", ".zip", ".gpkg", ".geojson", ".json"}
    )

    def list_layers(self, path: Path) -> tuple[RegionLayer, ...]:
        self._validate_path(path)
        uri = self._source_uri(path)
        layers = fiona.listlayers(uri)
        result = []
        for layer_name in layers:
            with fiona.open(uri, layer=layer_name) as source:
                result.append(
                    RegionLayer(
                        name=layer_name,
                        geometry_type=source.schema.get("geometry", "Unknown"),
                        crs=source.crs_wkt or (str(source.crs) if source.crs else None),
                        feature_count=len(source),
                        properties=tuple(source.schema.get("properties", {}).keys()),
                    )
                )
        return tuple(result)

    def import_region(
        self,
        path: Path,
        *,
        layer: str | None = None,
        selected_feature_ids: set[str] | None = None,
        source_crs_override: str | None = None,
        repair: bool = True,
        name: str | None = None,
    ) -> RegionImportResult:
        self._validate_path(path)
        uri = self._source_uri(path)
        layers = fiona.listlayers(uri)
        if not layers:
            raise ValueError("the region data source has no layers")
        layer_name = layer or layers[0]
        if layer_name not in layers:
            raise ValueError(f"unknown layer: {layer_name}")

        warnings: list[str] = []
        geometries = []
        chosen_ids: list[str] = []
        with fiona.open(uri, layer=layer_name) as source:
            source_crs = source_crs_override or source.crs_wkt or source.crs
            if not source_crs:
                raise ValueError("region CRS is missing; an explicit CRS is required")
            crs = CRS.from_user_input(source_crs)
            for feature in source:
                feature_id = str(feature.get("id", ""))
                if selected_feature_ids is not None and feature_id not in selected_feature_ids:
                    continue
                if not feature.get("geometry"):
                    warnings.append(f"feature {feature_id or '?'} has no geometry")
                    continue
                geometry = shape(feature["geometry"])
                if geometry.is_empty:
                    warnings.append(f"feature {feature_id or '?'} is empty")
                    continue
                geometries.append(geometry)
                chosen_ids.append(feature_id)
        if not geometries:
            raise ValueError("no non-empty geometry was selected")

        merged = unary_union(geometries)
        was_repaired = False
        if not merged.is_valid:
            if not repair:
                raise ValueError("selected geometry is invalid and repair is disabled")
            merged = make_valid(merged)
            was_repaired = True
            warnings.append("invalid geometry was repaired with GEOS make_valid")
        merged = _polygonal_only(merged)
        if merged.is_empty:
            raise ValueError("selected features do not contain polygon geometry")

        if crs != CRS.from_epsg(4326):
            transformer = Transformer.from_crs(crs, 4326, always_xy=True)
            merged = transform(transformer.transform, merged)
        merged = _wrap_longitudes(merged)
        bbox = _antimeridian_bbox(merged)
        region = Region(
            name=name or path.stem,
            source_path=str(path.resolve()),
            source_sha256=_hash_source(path),
            source_crs=crs.to_string(),
            geometry_wkb_hex=merged.wkb_hex,
            bbox=bbox,
            repaired=was_repaired,
            selected_feature_ids=tuple(chosen_ids),
        )
        return RegionImportResult(region=region, warnings=tuple(warnings))

    def list_features(
        self, path: Path, layer: str | None = None, limit: int = 50_000
    ) -> tuple[RegionFeature, ...]:
        self._validate_path(path)
        uri = self._source_uri(path)
        layers = fiona.listlayers(uri)
        layer_name = layer or (layers[0] if layers else None)
        if layer_name is None or layer_name not in layers:
            raise ValueError(f"unknown layer: {layer_name}")
        features = []
        with fiona.open(uri, layer=layer_name) as source:
            if len(source) > limit:
                raise ValueError(f"layer contains more than {limit} features")
            for feature in source:
                features.append(
                    RegionFeature(
                        id=str(feature.get("id", "")),
                        properties={str(key): value for key, value in feature.properties.items()},
                    )
                )
        return tuple(features)

    def _validate_path(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() not in self.supported_suffixes:
            raise ValueError(f"unsupported region format: {path.suffix}")
        if path.suffix.lower() == ".shp":
            missing = [
                suffix for suffix in (".shx", ".dbf") if not path.with_suffix(suffix).exists()
            ]
            if missing:
                raise ValueError(f"Shapefile is missing components: {', '.join(missing)}")
        if path.suffix.lower() == ".zip":
            _validate_zip(path)

    @staticmethod
    def _source_uri(path: Path) -> str:
        if path.suffix.lower() == ".zip":
            return f"zip://{path.resolve().as_posix()}"
        return str(path.resolve())


def _validate_zip(path: Path) -> None:
    total = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            normalized = Path(member.filename.replace("\\", "/"))
            if normalized.is_absolute() or ".." in normalized.parts:
                raise ValueError("ZIP contains an unsafe path")
            total += member.file_size
            if total > 512 * 1024 * 1024:
                raise ValueError("ZIP expands beyond the 512 MiB safety limit")
            if member.compress_size and member.file_size / member.compress_size > 1000:
                raise ValueError("ZIP contains a suspicious compression ratio")


def _polygonal_only(geometry: Any) -> Polygon | MultiPolygon:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = [part for part in geometry.geoms if isinstance(part, (Polygon, MultiPolygon))]
        return unary_union(polygons) if polygons else Polygon()
    return Polygon()


def _wrap_longitudes(geometry: Polygon | MultiPolygon) -> Polygon | MultiPolygon:
    def wrap(x: Any, y: Any, z: Any = None) -> tuple[Any, Any] | tuple[Any, Any, Any]:
        try:
            wrapped = ((x + 180) % 360) - 180
        except TypeError:
            wrapped = [((value + 180) % 360) - 180 for value in x]
        return (wrapped, y, z) if z is not None else (wrapped, y)

    return transform(wrap, geometry)


def _antimeridian_bbox(geometry: Polygon | MultiPolygon) -> tuple[float, float, float, float]:
    west, south, east, north = geometry.bounds
    if east - west <= 180:
        return west, south, east, north
    xs: list[float] = []
    polygons = geometry.geoms if isinstance(geometry, MultiPolygon) else (geometry,)
    for polygon in polygons:
        xs.extend(point[0] for point in polygon.exterior.coords)
    positive = [value for value in xs if value >= 0]
    negative = [value for value in xs if value < 0]
    if positive and negative:
        return min(positive), south, max(negative), north
    return west, south, east, north


def _hash_source(path: Path) -> str:
    digest = hashlib.sha256()
    paths = [path]
    if path.suffix.lower() == ".shp":
        paths = [
            candidate
            for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg")
            if (candidate := path.with_suffix(suffix)).exists()
        ]
    for candidate in sorted(paths):
        digest.update(candidate.name.lower().encode("utf-8"))
        with candidate.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()
