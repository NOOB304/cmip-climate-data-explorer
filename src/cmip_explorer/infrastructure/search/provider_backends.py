from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from cmip_explorer.domain.models import (
    AccessEndpoint,
    Backend,
    BackendCapabilities,
    LogicalFile,
    Replica,
    SearchPage,
    SearchRequest,
    TemporalCoverage,
)


@dataclass(frozen=True)
class _NexSeriesCacheEntry:
    expires_at: float
    series: tuple[LogicalFile, ...]
    physical_file_count: int
    facet_counts: dict[str, dict[str, int]]
    warnings: tuple[str, ...]


_NEX_SERIES_CACHE: dict[str, _NexSeriesCacheEntry] = {}
_NEX_SERIES_CACHE_TTL_SECONDS = 10 * 60
_NEX_SERIES_CACHE_LIMIT = 6
_NEX_SERIES_CURSOR_PREFIX = "nex-series:"


class HttpProviderBackend:
    def __init__(self, definition: Backend, client: httpx.AsyncClient | None = None) -> None:
        self.definition = definition
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(35, connect=12),
            follow_redirects=True,
            headers={"User-Agent": "CMIP-Climate-Explorer/0.4.1"},
        )

    async def detect_capabilities(self) -> BackendCapabilities:
        return self.definition.capabilities

    async def health_check(self) -> tuple[bool, float, str | None]:
        started = time.perf_counter()
        try:
            response = await self.client.get(str(self.definition.base_url))
            response.raise_for_status()
            return True, (time.perf_counter() - started) * 1000, None
        except Exception as exc:
            return False, (time.perf_counter() - started) * 1000, str(exc)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class NexStacBackend(HttpProviderBackend):
    collection_id = "nasa-nex-gddp-cmip6"

    def __init__(
        self,
        definition: Backend,
        *,
        asset_source: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(definition, client)
        self.asset_source = asset_source

    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        token, offset = self._series_cursor(request, cursor)
        entry = _NEX_SERIES_CACHE.get(token)
        if entry is None or entry.expires_at <= time.monotonic():
            entry = await self._load_series(request)
            self._cache_series(token, entry)
        page_end = offset + request.page_size
        files = entry.series[offset:page_end]
        next_cursor = (
            f"{_NEX_SERIES_CURSOR_PREFIX}{token}:{page_end}"
            if page_end < len(entry.series)
            else None
        )
        return SearchPage(
            files=files,
            raw_total_by_backend={self.definition.id: entry.physical_file_count},
            known_unique_count=len(entry.series),
            exact_total=True,
            next_cursors={self.definition.id: next_cursor},
            facet_counts=entry.facet_counts,
            warnings=entry.warnings,
        )

    def _series_cursor(
        self, request: SearchRequest, cursor: str | int | None
    ) -> tuple[str, int]:
        if isinstance(cursor, str) and cursor.startswith(_NEX_SERIES_CURSOR_PREFIX):
            value = cursor.removeprefix(_NEX_SERIES_CURSOR_PREFIX)
            token, _, raw_offset = value.rpartition(":")
            if token and raw_offset.isdigit():
                return token, int(raw_offset)
        cache_payload = {
            "backend": self.definition.id,
            "base_url": str(self.definition.base_url),
            "asset_source": self.asset_source,
            "request": request.model_dump(mode="json"),
        }
        token = hashlib.sha256(
            json.dumps(cache_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:24]
        return token, 0

    async def _load_series(self, request: SearchRequest) -> _NexSeriesCacheEntry:
        query: dict[str, dict[str, Any]] = {}
        _add_stac_query(query, "cmip6:model", _constraint(request, "source_id"))
        _add_stac_query(query, "cmip6:scenario", _constraint(request, "experiment_id"))
        year_filter: dict[str, int] = {}
        if request.start_year is not None:
            year_filter["gte"] = request.start_year
        if request.end_year is not None:
            year_filter["lte"] = request.end_year
        if year_filter:
            query["cmip6:year"] = year_filter

        response = await self.client.post(
            f"{str(self.definition.base_url).rstrip('/')}/search",
            json={
                "collections": [self.collection_id],
                "limit": 1000,
                "query": query,
            },
        )
        variable_ids = _constraint(request, "variable_id")
        groups: dict[tuple[str, ...], list[LogicalFile]] = {}
        physical_keys: set[str] = set()
        visited_next_links: set[str] = set()
        warnings: list[str] = []
        while True:
            response.raise_for_status()
            payload = response.json()
            features = payload.get("features", ())
            for feature in features:
                for file in self._normalize_feature(feature, variable_ids):
                    if file.logical_key in physical_keys:
                        continue
                    physical_keys.add(file.logical_key)
                    groups.setdefault(_nex_series_group_key(file), []).append(file)
            next_link = next(
                (
                    link
                    for link in payload.get("links", ())
                    if link.get("rel") == "next" and link.get("href")
                ),
                None,
            )
            if next_link is None:
                break
            link_identity = json.dumps(next_link, sort_keys=True, default=str)
            if link_identity in visited_next_links:
                warnings.append("数据源返回了重复分页地址, 已停止继续读取以避免死循环。")
                break
            visited_next_links.add(link_identity)
            response = await self._follow_stac_link(next_link)

        series = tuple(
            sorted(
                (_nex_series_file(key, members) for key, members in groups.items()),
                key=lambda item: (
                    item.source_id or "",
                    item.experiment_id or "",
                    item.variable_id or "",
                    item.member_id or "",
                ),
            )
        )
        return _NexSeriesCacheEntry(
            expires_at=time.monotonic() + _NEX_SERIES_CACHE_TTL_SECONDS,
            series=series,
            physical_file_count=len(physical_keys),
            facet_counts=_nex_series_facet_counts(series),
            warnings=tuple(warnings),
        )

    async def _follow_stac_link(self, link: dict[str, Any]) -> httpx.Response:
        href = urljoin(str(self.definition.base_url), str(link["href"]))
        if str(link.get("method") or "GET").upper() == "POST":
            return await self.client.post(href, json=link.get("body") or {})
        return await self.client.get(href)

    @staticmethod
    def _cache_series(token: str, entry: _NexSeriesCacheEntry) -> None:
        now = time.monotonic()
        expired = [key for key, value in _NEX_SERIES_CACHE.items() if value.expires_at <= now]
        for key in expired:
            _NEX_SERIES_CACHE.pop(key, None)
        while len(_NEX_SERIES_CACHE) >= _NEX_SERIES_CACHE_LIMIT:
            oldest = min(_NEX_SERIES_CACHE, key=lambda key: _NEX_SERIES_CACHE[key].expires_at)
            _NEX_SERIES_CACHE.pop(oldest, None)
        _NEX_SERIES_CACHE[token] = entry

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]:
        year = min(max(request.start_year or 2020, 1950), 2100)
        response = await self.client.post(
            f"{str(self.definition.base_url).rstrip('/')}/search",
            json={
                "collections": [self.collection_id],
                "limit": 1000,
                "query": {"cmip6:year": {"eq": year}},
            },
        )
        response.raise_for_status()
        counts = _nex_facet_counts(response.json().get("features", ()))
        return {name: counts.get(name, {}) for name in names}

    def _normalize_feature(
        self, feature: dict[str, Any], variable_ids: tuple[str, ...]
    ) -> tuple[LogicalFile, ...]:
        properties = feature.get("properties", {})
        assets = feature.get("assets", {})
        selected = variable_ids or tuple(assets)
        files: list[LogicalFile] = []
        for variable_id in selected:
            asset = assets.get(variable_id)
            if not asset or not asset.get("href"):
                continue
            url = self._asset_url(str(asset["href"]))
            filename = Path(urlsplit(url).path).name
            path_parts = urlsplit(url).path.strip("/").split("/")
            member_id = next((part for part in path_parts if part.startswith("r1i")), None)
            size = asset.get("file:size") or asset.get("content_length")
            replica = Replica(
                data_node=urlsplit(url).hostname or self.definition.name,
                backend_id=self.definition.id,
                replica=False,
                endpoints=(
                    AccessEndpoint(
                        url=url,
                        service="HTTPServer",
                        media_type=asset.get("type") or "application/netcdf",
                        secure=True,
                    ),
                ),
            )
            files.append(
                LogicalFile(
                    logical_key=f"{self.definition.id}:{feature.get('id')}:{variable_id}",
                    provider_id=self.definition.id,
                    product_id=self.collection_id,
                    filename=filename,
                    project="NEX-GDDP-CMIP6",
                    source_id=properties.get("cmip6:model"),
                    experiment_id=properties.get("cmip6:scenario"),
                    member_id=member_id,
                    table_id="day",
                    variable_id=variable_id,
                    grid_label="0.25deg",
                    nominal_resolution="约 25 km (0.25°)",
                    frequency="day",
                    size_bytes=int(size) if size is not None else None,
                    temporal=TemporalCoverage(
                        start=properties.get("start_datetime"),
                        end=properties.get("end_datetime"),
                        source="stac",
                    ),
                    replicas=(replica,),
                    raw_provenance={
                        "backend_id": self.definition.id,
                        "stac_collection": self.collection_id,
                        "landing_url": url,
                        "access_note": "可直接下载",
                    },
                )
            )
        return tuple(files)

    def _asset_url(self, url: str) -> str:
        if self.asset_source != "aws":
            return url
        marker = "/NEX/GDDP-CMIP6/"
        if marker not in url:
            return url
        key = "NEX-GDDP-CMIP6/" + url.split(marker, 1)[1]
        return "https://nex-gddp-cmip6.s3.us-west-2.amazonaws.com/" + quote(
            key, safe="/"
        )


class CdsCatalogueBackend(HttpProviderBackend):
    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        product_id = request.product_id or "projections-cmip6"
        response = await self.client.get(
            f"{str(self.definition.base_url).rstrip('/')}/collections/{product_id}"
        )
        response.raise_for_status()
        collection = response.json()
        variable_id = (_constraint(request, "variable_id") or ("catalogue",))[0]
        experiments = _constraint(request, "experiment_id")
        frequencies = _constraint(request, "frequency")
        interval = collection.get("extent", {}).get("temporal", {}).get("interval", [[None, None]])
        start, end = interval[0] if interval else (None, None)
        landing_url = f"https://cds.climate.copernicus.eu/datasets/{product_id}?tab=download"
        file = LogicalFile(
            logical_key=f"cds:{product_id}:{variable_id}",
            provider_id="cds",
            product_id=product_id,
            filename=f"{product_id}_{variable_id}_CDS目录",
            project="Copernicus CDS",
            source_id="Copernicus CDS",
            experiment_id=experiments[0] if experiments else None,
            table_id=frequencies[0] if frequencies else None,
            variable_id=variable_id,
            frequency=frequencies[0] if frequencies else None,
            temporal=TemporalCoverage(start=start, end=end, source="api"),
            raw_provenance={
                "backend_id": self.definition.id,
                "landing_url": landing_url,
                "requires_auth": True,
                "access_note": "需 CDS 账号授权",
                "catalogue_title": collection.get("title"),
            },
        )
        return SearchPage(
            files=(file,),
            raw_total_by_backend={self.definition.id: 1},
            known_unique_count=1,
            exact_total=True,
            next_cursors={self.definition.id: None},
            warnings=("CDS 公共 API 当前只提供目录发现; 取数需登录并接受相应许可。",),
        )

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]:
        product_id = request.product_id or "projections-cmip6"
        collection = await self.client.get(
            f"{str(self.definition.base_url).rstrip('/')}/collections/{product_id}"
        )
        collection.raise_for_status()
        form_url = next(
            link["href"]
            for link in collection.json().get("links", ())
            if link.get("rel") == "form"
        )
        form = await self.client.get(form_url)
        form.raise_for_status()
        fields = {field.get("name"): field for field in form.json()}
        mapping = {
            "source_id": ("model", "source"),
            "experiment_id": ("experiment",),
            "frequency": ("temporal_resolution",),
        }
        result: dict[str, dict[str, int]] = {name: {} for name in names}
        for target, candidates in mapping.items():
            field = next((fields[name] for name in candidates if name in fields), None)
            if field:
                result[target] = {
                    value: 1 for value in _form_values(field.get("details", {}))
                }
        return result


class PowerBackend(HttpProviderBackend):
    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        product_id = (request.product_id or "daily").lower()
        variable_id = (_constraint(request, "variable_id") or ("T2M",))[0]
        latitude = request.parameters.get("latitude", "39.9")
        longitude = request.parameters.get("longitude", "116.4")
        start_year = request.start_year or 1981
        end_year = request.end_year or start_year
        if product_id == "daily":
            start, end = f"{start_year}0101", f"{end_year}1231"
        else:
            start, end = str(start_year), str(end_year)
        params = {
            "parameters": variable_id,
            "community": "AG",
            "longitude": longitude,
            "latitude": latitude,
            "start": start,
            "end": end,
            "format": "CSV",
        }
        url = str(
            httpx.URL(
                f"{str(self.definition.base_url).rstrip('/')}/{product_id}/point",
                params=params,
            )
        )
        filename = (
            f"NASA_POWER_{variable_id}_{product_id}_{latitude}_{longitude}_"
            f"{start_year}-{end_year}.csv"
        ).replace("-", "_")
        file = _generated_file(
            backend=self.definition,
            request=request,
            logical_key=f"power:{product_id}:{variable_id}:{latitude}:{longitude}:{start}:{end}",
            filename=filename,
            variable_id=variable_id,
            frequency=product_id,
            url=url,
            source_id="NASA POWER",
            temporal_start=f"{start_year}-01-01",
            temporal_end=f"{end_year}-12-31",
            note="可直接生成 CSV",
        )
        return _single_file_page(self.definition.id, file)

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]:
        return {name: {} for name in names}


class CmrBackend(HttpProviderBackend):
    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        short_name = (_constraint(request, "variable_id") or ("GPM_3IMERGDF",))[0]
        page_number = int(cursor or 1)
        params: dict[str, Any] = {
            "short_name": short_name,
            "downloadable": "true",
            "page_size": request.page_size,
            "page_num": page_number,
        }
        if request.start_year is not None or request.end_year is not None:
            start = request.start_year or 1900
            end = request.end_year or 2100
            params["temporal"] = (
                f"{start:04d}-01-01T00:00:00Z,{end:04d}-12-31T23:59:59Z"
            )
        response = await self.client.get(
            f"{str(self.definition.base_url).rstrip('/')}/granules.umm_json",
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        files = tuple(
            self._normalize_granule(item, short_name)
            for item in payload.get("items", ())
        )
        total = int(response.headers.get("CMR-Hits", len(files)))
        next_page = page_number + 1 if page_number * request.page_size < total else None
        return SearchPage(
            files=files,
            raw_total_by_backend={self.definition.id: total},
            known_unique_count=len(files),
            exact_total=True,
            next_cursors={self.definition.id: next_page},
            warnings=("受保护的 NASA 文件需在浏览器中使用 Earthdata 账号登录后下载。",),
        )

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]:
        return {name: {} for name in names}

    def _normalize_granule(self, item: dict[str, Any], short_name: str) -> LogicalFile:
        umm = item.get("umm", {})
        url = _cmr_data_url(umm.get("RelatedUrls", ()))
        temporal = umm.get("TemporalExtent", {}).get("RangeDateTime", {})
        granule_ur = str(umm.get("GranuleUR") or item.get("meta", {}).get("concept-id"))
        filename = Path(urlsplit(url).path).name if url else granule_ur.replace(":", "_")
        size = _cmr_size(umm)
        replicas = ()
        if url:
            replicas = (
                Replica(
                    data_node=urlsplit(url).hostname or "earthdata.nasa.gov",
                    backend_id=self.definition.id,
                    replica=False,
                    endpoints=(
                        AccessEndpoint(
                            url=url,
                            service="HTTPServer",
                            media_type=None,
                            secure=True,
                        ),
                    ),
                ),
            )
        return LogicalFile(
            logical_key=f"cmr:{item.get('meta', {}).get('concept-id') or granule_ur}",
            provider_id="cmr",
            product_id=short_name,
            filename=filename,
            project="NASA Earthdata",
            source_id="NASA Earthdata",
            variable_id=short_name,
            frequency=_cmr_frequency(short_name),
            size_bytes=size,
            temporal=TemporalCoverage(
                start=temporal.get("BeginningDateTime"),
                end=temporal.get("EndingDateTime"),
                source="api",
            ),
            replicas=replicas,
            raw_provenance={
                "backend_id": self.definition.id,
                "landing_url": url,
                "requires_auth": True,
                "access_note": "需 Earthdata 登录",
            },
        )


class NoaaNceiBackend(HttpProviderBackend):
    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        product_id = request.product_id or "daily-summaries"
        variable_id = (_constraint(request, "variable_id") or ("PRCP",))[0]
        station = request.parameters.get("station", "").strip()
        if not station:
            raise ValueError("NOAA NCEI 查询需要填写站点编号, 例如 USW00094728")
        start_year = request.start_year or 1981
        end_year = request.end_year or start_year
        params = {
            "dataset": product_id,
            "stations": station,
            "startDate": f"{start_year:04d}-01-01",
            "endDate": f"{end_year:04d}-12-31",
            "format": "csv",
            "units": "metric",
            "dataTypes": variable_id,
            "includeStationName": "true",
        }
        url = str(httpx.URL(str(self.definition.base_url), params=params))
        filename = (
            f"NOAA_{product_id}_{station}_{variable_id}_{start_year}-{end_year}.csv"
        )
        file = _generated_file(
            backend=self.definition,
            request=request,
            logical_key=f"noaa:{product_id}:{station}:{variable_id}:{start_year}:{end_year}",
            filename=filename,
            variable_id=variable_id,
            frequency="hourly" if product_id == "global-hourly" else "day",
            url=url,
            source_id=f"NOAA {station}",
            temporal_start=f"{start_year}-01-01",
            temporal_end=f"{end_year}-12-31",
            note="可直接生成 CSV",
        )
        return _single_file_page(self.definition.id, file)

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]:
        return {name: {} for name in names}


def _constraint(request: SearchRequest, name: str) -> tuple[str, ...]:
    return next((constraint.values for constraint in request.facets if constraint.name == name), ())


def _add_stac_query(
    query: dict[str, dict[str, Any]], property_name: str, values: tuple[str, ...]
) -> None:
    if not values:
        return
    query[property_name] = {"eq": values[0]} if len(values) == 1 else {"in": list(values)}


def _nex_facet_counts(
    features: list[dict[str, Any]] | tuple[Any, ...],
) -> dict[str, dict[str, int]]:
    result = {
        "source_id": {},
        "experiment_id": {},
        "frequency": {"day": len(features)},
        "grid_label": {"0.25deg": len(features)},
        "table_id": {"day": len(features)},
    }
    for feature in features:
        properties = feature.get("properties", {})
        for target, source in (
            ("source_id", "cmip6:model"),
            ("experiment_id", "cmip6:scenario"),
        ):
            value = properties.get(source)
            if value:
                result[target][str(value)] = result[target].get(str(value), 0) + 1
    return result


def _nex_series_group_key(file: LogicalFile) -> tuple[str, ...]:
    return tuple(
        value or ""
        for value in (
            file.provider_id,
            file.product_id,
            file.project,
            file.source_id,
            file.experiment_id,
            file.member_id,
            file.table_id,
            file.variable_id,
            file.grid_label,
            file.frequency,
        )
    )


def _nex_series_file(key: tuple[str, ...], members: list[LogicalFile]) -> LogicalFile:
    unique = {member.logical_key: member for member in members}
    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.temporal.start or "", item.filename),
        )
    )
    first = ordered[0]
    starts = [item.temporal.start for item in ordered if item.temporal.start]
    ends = [item.temporal.end for item in ordered if item.temporal.end]
    start = min(starts) if starts else None
    end = max(ends) if ends else None
    size = (
        sum(item.size_bytes for item in ordered if item.size_bytes is not None)
        if all(item.size_bytes is not None for item in ordered)
        else None
    )
    start_label = start[:4] if start else "unknown"
    end_label = end[:4] if end else "unknown"
    name_parts = (
        first.variable_id or "data",
        first.table_id or first.frequency or "series",
        first.source_id or "model",
        first.experiment_id or "scenario",
        f"{start_label}-{end_label}",
        f"{len(ordered)}files",
    )
    provenance = dict(first.raw_provenance)
    provenance.update(
        {
            "access_note": f"系列下载, 共 {len(ordered)} 个年度文件",
            "series_file_count": len(ordered),
            "series_key": "|".join(key),
        }
    )
    return first.model_copy(
        update={
            "logical_key": f"series:{'|'.join(key)}",
            "filename": "_".join(name_parts) + ".series",
            "dataset_id": f"series:{'|'.join(key)}",
            "size_bytes": size,
            "temporal": TemporalCoverage(start=start, end=end, source="stac"),
            "replicas": (),
            "series_members": ordered,
            "raw_provenance": provenance,
        }
    )


def _nex_series_facet_counts(
    series: tuple[LogicalFile, ...],
) -> dict[str, dict[str, int]]:
    result = {
        "source_id": {},
        "experiment_id": {},
        "frequency": {},
        "grid_label": {},
        "table_id": {},
    }
    for item in series:
        for name, value in (
            ("source_id", item.source_id),
            ("experiment_id", item.experiment_id),
            ("frequency", item.frequency),
            ("grid_label", item.grid_label),
            ("table_id", item.table_id),
        ):
            if value:
                result[name][value] = result[name].get(value, 0) + 1
    return result


def _form_values(details: dict[str, Any]) -> tuple[str, ...]:
    values = list(details.get("values", ()))
    for group in details.get("groups", ()):
        values.extend(group.get("values", ()))
    return tuple(dict.fromkeys(str(value) for value in values))


def _generated_file(
    *,
    backend: Backend,
    request: SearchRequest,
    logical_key: str,
    filename: str,
    variable_id: str,
    frequency: str,
    url: str,
    source_id: str,
    temporal_start: str,
    temporal_end: str,
    note: str,
) -> LogicalFile:
    replica = Replica(
        data_node=urlsplit(url).hostname or backend.name,
        backend_id=backend.id,
        replica=False,
        endpoints=(
            AccessEndpoint(
                url=url,
                service="HTTPServer",
                media_type="text/csv",
                secure=url.startswith("https://"),
            ),
        ),
    )
    return LogicalFile(
        logical_key=logical_key,
        provider_id=request.provider_id,
        product_id=request.product_id,
        filename=filename,
        project=request.provider_id,
        source_id=source_id,
        experiment_id=request.product_id,
        variable_id=variable_id,
        frequency=frequency,
        temporal=TemporalCoverage(
            start=temporal_start,
            end=temporal_end,
            source="api",
        ),
        replicas=(replica,),
        raw_provenance={
            "backend_id": backend.id,
            "landing_url": url,
            "access_note": note,
        },
    )


def _single_file_page(backend_id: str, file: LogicalFile) -> SearchPage:
    return SearchPage(
        files=(file,),
        raw_total_by_backend={backend_id: 1},
        known_unique_count=1,
        exact_total=True,
        next_cursors={backend_id: None},
    )


def _cmr_data_url(related_urls: list[dict[str, Any]] | tuple[Any, ...]) -> str | None:
    candidates = []
    for related in related_urls:
        url = str(related.get("URL") or "")
        if related.get("Type") != "GET DATA" or not url.startswith("https://"):
            continue
        suffix = Path(urlsplit(url).path).suffix.casefold()
        if suffix in {".nc", ".nc4", ".hdf", ".h5", ".he5", ".tif", ".tiff"}:
            candidates.append(url)
    return candidates[0] if candidates else None


def _cmr_size(umm: dict[str, Any]) -> int | None:
    information = umm.get("DataGranule", {}).get("ArchiveAndDistributionInformation", ())
    for entry in information:
        if entry.get("SizeInBytes") is not None:
            return int(entry["SizeInBytes"])
        if entry.get("Size") is not None:
            units = str(entry.get("SizeUnit") or "MB").upper()
            multiplier = {"KB": 1000, "MB": 1000**2, "GB": 1000**3}.get(units, 1)
            return int(float(entry["Size"]) * multiplier)
    return None


def _cmr_frequency(short_name: str) -> str:
    return {
        "GPM_3IMERGDF": "day",
        "M2T1NXSLV": "1hr",
        "MOD11A1": "day",
        "SPL3SMP_E": "day",
    }.get(short_name, "unknown")
