from __future__ import annotations

import asyncio

import httpx

from cmip_explorer.domain.models import FacetConstraint, LogicalFile, SearchPage, SearchRequest

from .registry import BackendRegistry


class MultiBackendSearchService:
    def __init__(self, registry: BackendRegistry) -> None:
        self.registry = registry

    async def search(
        self, request: SearchRequest, cursors: dict[str, str | int | None] | None = None
    ) -> SearchPage:
        backends = self.registry.enabled(request.backend_ids)
        if not backends:
            return SearchPage(files=(), warnings=("No search backends are enabled",))
        cursor_map = cursors or {}
        warnings: list[str] = []
        cursor = next((value for value in cursor_map.values() if value is not None), None)
        if cursor_map:
            active = tuple(
                backend for backend in backends if backend.definition.id in cursor_map
            )
            fallback = tuple(backend for backend in backends if backend not in active)
            backends = (*active, *fallback)
        for backend in backends:
            try:
                result = await self._search_with_retry(backend, request, cursor)
            except Exception as exc:
                warnings.append(f"{backend.definition.name}: {type(exc).__name__}: {exc!s}")
                continue
            return result.model_copy(update={"warnings": (*warnings, *result.warnings)})
        return SearchPage(files=(), warnings=tuple(warnings))

    async def historical_companion(
        self, file: LogicalFile, requested_start_year: int
    ) -> LogicalFile | None:
        scenario_start = _coverage_start_year(file) or 2015
        if file.experiment_id == "historical" or requested_start_year >= scenario_start:
            return None
        facets = []
        for name, value in (
            ("source_id", file.source_id),
            ("institution_id", file.institution_id),
            ("experiment_id", "historical"),
            ("member_id", file.member_id),
            ("table_id", file.table_id),
            ("variable_id", file.variable_id),
            ("frequency", file.frequency),
            ("grid_label", file.grid_label),
        ):
            if value:
                facets.append(FacetConstraint(name=name, values=(value,)))
        page = await self.search(
            SearchRequest(
                facets=tuple(facets),
                start_year=requested_start_year,
                end_year=scenario_start - 1,
                page_size=100,
            )
        )
        compatible = [
            candidate
            for candidate in page.files
            if candidate.experiment_id == "historical"
            and candidate.source_id == file.source_id
            and candidate.institution_id == file.institution_id
            and candidate.member_id == file.member_id
            and candidate.table_id == file.table_id
            and candidate.variable_id == file.variable_id
            and candidate.frequency == file.frequency
            and candidate.grid_label == file.grid_label
        ]
        if not compatible:
            return None
        compatible.sort(key=lambda item: (item.version or "", len(item.replicas)), reverse=True)
        return compatible[0]

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> tuple[dict[str, dict[str, int]], tuple[str, ...]]:
        backends = self.registry.enabled(request.backend_ids)
        warnings = []
        for backend in backends:
            try:
                result = await self._facets_with_retry(backend, request, names)
            except Exception as exc:
                warnings.append(f"{backend.definition.name}: {type(exc).__name__}: {exc!s}")
                continue
            return result, tuple(warnings)
        return {name: {} for name in names}, tuple(warnings)

    @staticmethod
    async def _search_with_retry(backend, request, cursor):
        for attempt in range(2):
            try:
                return await backend.search(request, cursor)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 1:
                    raise
            except httpx.HTTPStatusError as exc:
                if attempt == 1 or exc.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
            await asyncio.sleep(0.35 * (attempt + 1))
        raise RuntimeError("unreachable search retry state")

    @staticmethod
    async def _facets_with_retry(backend, request, names):
        for attempt in range(2):
            try:
                return await backend.facets(request, names)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 1:
                    raise
            except httpx.HTTPStatusError as exc:
                if attempt == 1 or exc.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
            await asyncio.sleep(0.35 * (attempt + 1))
        raise RuntimeError("unreachable facet retry state")


def _coverage_start_year(file: LogicalFile) -> int | None:
    try:
        return int(file.temporal.start[:4]) if file.temporal.start else None
    except ValueError:
        return None
