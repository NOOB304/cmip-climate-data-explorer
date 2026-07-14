from __future__ import annotations

import asyncio

import httpx

from cmip_explorer.domain.models import FacetConstraint, LogicalFile, SearchPage, SearchRequest

from .normalizer import merge_logical_files
from .registry import BackendRegistry


class MultiBackendSearchService:
    max_empty_page_skips = 20
    max_page_scans = 20

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
        if cursor_map:
            backends = tuple(
                backend for backend in backends if backend.definition.id in cursor_map
            )
            if not backends:
                raise RuntimeError("分页来源已经不可用 请重新查询")
        last_error: Exception | None = None
        for backend in backends:
            cursor = cursor_map.get(backend.definition.id)
            try:
                result, skipped = await self._search_visible_page(backend, request, cursor)
            except Exception as exc:
                last_error = exc
                warnings.append(f"{backend.definition.name}: {type(exc).__name__}: {exc!s}")
                continue
            if skipped:
                warnings.append(
                    f"{backend.definition.name}: 已跳过 {skipped} 个无匹配记录的原始分页"
                )
            return result.model_copy(update={"warnings": (*warnings, *result.warnings)})
        if cursor_map and last_error is not None:
            raise RuntimeError("当前分页节点暂时不可用 已保留原页面 请稍后重试") from last_error
        return SearchPage(files=(), warnings=tuple(warnings))

    async def _search_visible_page(self, backend, request, cursor):
        skipped = 0
        scanned = 0
        seen_cursors: set[str] = set()
        collected: list[LogicalFile] = []
        first_result: SearchPage | None = None
        while True:
            result = await self._search_with_retry(backend, request, cursor)
            first_result = first_result or result
            collected.extend(result.files)
            merged = merge_logical_files(collected)
            next_cursor = result.next_cursors.get(backend.definition.id)
            scanned += 1
            if merged and (len(merged) >= request.page_size or next_cursor is None):
                return _merge_scanned_pages(first_result, result, merged), skipped
            if next_cursor is None:
                return _merge_scanned_pages(first_result, result, merged), skipped
            if scanned >= self.max_page_scans:
                if merged:
                    partial = _merge_scanned_pages(first_result, result, merged)
                    warning = (
                        f"{backend.definition.name}: 当前页已扫描 {scanned} 个原始分页 "
                        "其余匹配项可继续翻页查看"
                    )
                    return partial.model_copy(
                        update={"warnings": (*partial.warnings, warning)}
                    ), skipped
                raise RuntimeError("连续分页均无匹配记录 请缩小查询条件后重试")
            marker = repr(next_cursor)
            if marker in seen_cursors or next_cursor == cursor:
                raise RuntimeError("数据源返回了重复分页位置")
            if not result.files:
                skipped += 1
            if skipped >= self.max_empty_page_skips and not merged:
                raise RuntimeError("连续分页均无匹配记录 请缩小查询条件后重试")
            seen_cursors.add(marker)
            cursor = next_cursor

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


def _merge_scanned_pages(
    first: SearchPage, last: SearchPage, files: tuple[LogicalFile, ...]
) -> SearchPage:
    return first.model_copy(
        update={
            "files": files,
            "known_unique_count": len(files),
            "exact_total": first.exact_total and last.exact_total,
            "next_cursors": last.next_cursors,
            "warnings": tuple(dict.fromkeys((*first.warnings, *last.warnings))),
        }
    )
