from __future__ import annotations

import time
from typing import Any

import httpx
from pystac_client import Client

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

from .normalizer import merge_logical_files, normalize_solr_document


class LegacySolrBackend:
    def __init__(self, definition: Backend, client: httpx.AsyncClient | None = None) -> None:
        self.definition = definition
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(25.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "CMIP-Climate-Explorer/0.5.2"},
        )

    async def detect_capabilities(self) -> BackendCapabilities:
        params = {"project": "CMIP6", "type": "File", "limit": 0, "facets": "project"}
        response = await self.client.get(str(self.definition.base_url), params=params)
        response.raise_for_status()
        payload = response.json()
        return BackendCapabilities(
            distributed_search=True,
            facets="facet_counts" in payload,
            fields_parameter=True,
            replica_filter=True,
            temporal_filter=True,
            spatial_filter=True,
        )

    async def health_check(self) -> tuple[bool, float, str | None]:
        started = time.perf_counter()
        try:
            response = await self.client.get(
                str(self.definition.base_url),
                params={
                    "project": "CMIP6",
                    "type": "File",
                    "limit": 0,
                    "format": "application/solr+json",
                },
            )
            response.raise_for_status()
            response.json()
            return True, (time.perf_counter() - started) * 1000, None
        except Exception as exc:  # health checks must return diagnostics, not leak failures
            return False, (time.perf_counter() - started) * 1000, str(exc)

    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        offset = int(cursor or 0)
        params = self._params(request)
        params.update(
            {
                "limit": request.page_size,
                "offset": offset,
                "facets": "source_id,experiment_id,table_id,frequency,grid_label",
            }
        )
        response = await self.client.get(str(self.definition.base_url), params=params)
        response.raise_for_status()
        payload = response.json()
        solr_response = payload.get("response", {})
        docs = solr_response.get("docs", [])
        normalized = tuple(normalize_solr_document(doc, self.definition.id) for doc in docs)
        files = tuple(item for item in normalized if _overlaps_requested_years(item, request))
        total = int(solr_response.get("numFound", len(files)))
        next_offset = offset + len(docs) if offset + len(docs) < total else None
        return SearchPage(
            files=merge_logical_files(files),
            raw_total_by_backend={self.definition.id: total},
            known_unique_count=len({item.logical_key for item in files}),
            exact_total=request.start_year is None and request.end_year is None,
            next_cursors={self.definition.id: next_offset},
            facet_counts=_facet_counts(payload),
        )

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]:
        params = self._params(request)
        params.update({"limit": 0, "facets": ",".join(names)})
        response = await self.client.get(str(self.definition.base_url), params=params)
        response.raise_for_status()
        return _facet_counts(response.json())

    def _params(self, request: SearchRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "project": request.project,
            "type": request.type,
            "latest": str(request.latest).lower(),
            "format": "application/solr+json",
            "distrib": "true",
        }
        if request.text:
            params["query"] = request.text
        if request.replicas == "masters":
            params["replica"] = "false"
        elif request.replicas == "replicas":
            params["replica"] = "true"
        for constraint in request.facets:
            key = f"{constraint.name}!" if constraint.exclude else constraint.name
            params[key] = ",".join(constraint.values)
        return params

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class OrnlBridgeBackend(LegacySolrBackend):
    async def detect_capabilities(self) -> BackendCapabilities:
        available = await super().detect_capabilities()
        return available.model_copy(
            update={"fields_parameter": False, "replica_filter": False, "spatial_filter": False}
        )

    def _params(self, request: SearchRequest) -> dict[str, Any]:
        params = super()._params(request)
        params.pop("fields", None)
        return params

    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        page = await super().search(request, cursor)
        if request.replicas == "all":
            return page.model_copy(update={"exact_total": False})
        expected = request.replicas == "replicas"
        files: list[LogicalFile] = []
        for item in page.files:
            replicas = tuple(replica for replica in item.replicas if replica.replica is expected)
            if replicas:
                files.append(item.model_copy(update={"replicas": replicas}))
        warnings = (*page.warnings, "ORNL Bridge replica filtering was verified client-side")
        return page.model_copy(
            update={
                "files": tuple(files),
                "known_unique_count": len(files),
                "exact_total": False,
                "warnings": warnings,
            }
        )


class StacBackend:
    def __init__(self, definition: Backend) -> None:
        self.definition = definition

    async def detect_capabilities(self) -> BackendCapabilities:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(str(self.definition.base_url))
            response.raise_for_status()
            payload = response.json()
        conforms = payload.get("conformsTo", [])
        has_search = any("item-search" in item for item in conforms)
        return BackendCapabilities(
            facets=False,
            fields_parameter=False,
            replica_filter=False,
            temporal_filter=has_search,
            spatial_filter=has_search,
            cursor_paging=has_search,
        )

    async def health_check(self) -> tuple[bool, float, str | None]:
        started = time.perf_counter()
        try:
            await self.detect_capabilities()
            return True, (time.perf_counter() - started) * 1000, None
        except Exception as exc:
            return False, (time.perf_counter() - started) * 1000, str(exc)

    async def search(self, request: SearchRequest, cursor: str | int | None = None) -> SearchPage:
        # pystac-client is synchronous; isolate its network work from the UI loop.
        import asyncio

        return await asyncio.to_thread(self._search_sync, request, cursor)

    def _search_sync(self, request: SearchRequest, cursor: str | int | None) -> SearchPage:
        client = Client.open(str(self.definition.base_url))
        query: dict[str, dict[str, Any]] = {}
        for constraint in request.facets:
            query[constraint.name] = {"in": list(constraint.values)}
        datetime_range = None
        if request.start_year is not None or request.end_year is not None:
            start = request.start_year or 1
            end = request.end_year or 9999
            datetime_range = f"{start:04d}-01-01T00:00:00Z/{end:04d}-12-31T23:59:59Z"
        search = client.search(
            collections=[request.project] if request.project else None,
            datetime=datetime_range,
            query=query or None,
            max_items=request.page_size,
        )
        items = list(search.items())
        files = tuple(self._normalize_item(item) for item in items)
        return SearchPage(
            files=merge_logical_files(files),
            raw_total_by_backend={self.definition.id: len(items)},
            known_unique_count=len(files),
            exact_total=False,
            next_cursors={self.definition.id: None},
        )

    def _normalize_item(self, item: Any) -> LogicalFile:
        properties = item.properties
        endpoints = tuple(
            AccessEndpoint(
                url=asset.href,
                service="STAC_ASSET",
                media_type=asset.media_type,
                secure=asset.href.lower().startswith("https://"),
            )
            for asset in item.assets.values()
        )
        replica = Replica(
            data_node=str(self.definition.base_url.host),
            backend_id=self.definition.id,
            replica=False,
            endpoints=endpoints,
        )
        start = properties.get("start_datetime") or properties.get("datetime")
        end = properties.get("end_datetime") or properties.get("datetime")
        return LogicalFile(
            logical_key=item.id,
            filename=properties.get("title") or item.id,
            project=properties.get("project", "CMIP6"),
            source_id=properties.get("source_id"),
            experiment_id=properties.get("experiment_id"),
            member_id=properties.get("member_id"),
            table_id=properties.get("table_id"),
            variable_id=properties.get("variable_id"),
            grid_label=properties.get("grid_label"),
            version=str(properties.get("version")) if properties.get("version") else None,
            temporal=TemporalCoverage(start=start, end=end, source="stac"),
            replicas=(replica,),
            raw_provenance={
                "backend_id": self.definition.id,
                "stac_collection": item.collection_id,
            },
        )

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]:
        return {}

    async def close(self) -> None:
        return None


def _facet_counts(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    fields = payload.get("facet_counts", {}).get("facet_fields", {})
    result: dict[str, dict[str, int]] = {}
    for name, values in fields.items():
        if isinstance(values, dict):
            result[name] = {str(key): int(value) for key, value in values.items()}
            continue
        if isinstance(values, list):
            result[name] = {
                str(values[index]): int(values[index + 1]) for index in range(0, len(values) - 1, 2)
            }
    return result


def _overlaps_requested_years(file: LogicalFile, request: SearchRequest) -> bool:
    if request.start_year is None and request.end_year is None:
        return True
    if file.temporal.source == "static":
        return True
    try:
        start = int(file.temporal.start[:4]) if file.temporal.start else None
        end = int(file.temporal.end[:4]) if file.temporal.end else None
    except ValueError:
        return True
    requested_start = request.start_year or 1
    requested_end = request.end_year or 9999
    if start is None or end is None:
        return True
    return end >= requested_start and start <= requested_end
