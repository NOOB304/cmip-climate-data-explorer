from types import SimpleNamespace

import httpx
import pytest

from cmip_explorer.domain.models import LogicalFile, SearchPage, SearchRequest
from cmip_explorer.infrastructure.search.service import MultiBackendSearchService


class StubBackend:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0

    async def search(self, _request, _cursor):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return SearchPage(files=())


class RegistryStub:
    def __init__(self, *backends) -> None:
        self.backends = backends

    def enabled(self, _selected=()):
        return self.backends


class SearchBackendStub:
    def __init__(self, backend_id: str, failure: Exception | None = None) -> None:
        self.definition = SimpleNamespace(id=backend_id, name=backend_id)
        self.failure = failure
        self.search_calls = 0
        self.facet_calls = 0

    async def search(self, request, _cursor):
        self.search_calls += 1
        if self.failure:
            raise self.failure
        experiment = next(
            (
                constraint.values[0]
                for constraint in request.facets
                if constraint.name == "experiment_id"
            ),
            "historical",
        )
        file = LogicalFile(
            logical_key=f"{self.definition.id}-{experiment}",
            filename=f"tas_{experiment}.nc",
            experiment_id=experiment,
        )
        return SearchPage(
            files=(file,),
            raw_total_by_backend={self.definition.id: 10},
            next_cursors={self.definition.id: None},
        )

    async def facets(self, _request, names):
        self.facet_calls += 1
        return {
            name: ({"historical": 10, "ssp245": 8} if name == "experiment_id" else {})
            for name in names
        }


class PagedBackendStub:
    def __init__(self, backend_id: str, pages: dict[object, SearchPage | Exception]) -> None:
        self.definition = SimpleNamespace(id=backend_id, name=backend_id)
        self.pages = pages
        self.cursors: list[object] = []

    async def search(self, _request, cursor):
        self.cursors.append(cursor)
        result = self.pages[cursor]
        if isinstance(result, Exception):
            raise result
        return result


async def test_transient_search_error_is_retried_once() -> None:
    backend = StubBackend([httpx.ReadTimeout("slow node")])
    result = await MultiBackendSearchService._search_with_retry(backend, SearchRequest(), None)
    assert result.files == ()
    assert backend.calls == 2


async def test_client_error_is_not_retried() -> None:
    request = httpx.Request("GET", "https://example.test/search")
    response = httpx.Response(400, request=request)
    backend = StubBackend([httpx.HTTPStatusError("bad query", request=request, response=response)])
    with pytest.raises(httpx.HTTPStatusError):
        await MultiBackendSearchService._search_with_retry(backend, SearchRequest(), None)
    assert backend.calls == 1


async def test_distributed_search_uses_one_index_node_instead_of_merging_duplicates() -> None:
    primary = SearchBackendStub("primary")
    duplicate = SearchBackendStub("duplicate")
    service = MultiBackendSearchService(RegistryStub(primary, duplicate))

    result = await service.search(SearchRequest())

    assert len(result.files) == 1
    assert primary.search_calls == 1
    assert duplicate.search_calls == 0
    assert result.raw_total_by_backend == {"primary": 10}


async def test_empty_raw_page_is_skipped_before_results_are_shown() -> None:
    file = LogicalFile(logical_key="visible", filename="visible.nc")
    backend = PagedBackendStub(
        "primary",
        {
            None: SearchPage(
                files=(),
                next_cursors={"primary": 100},
            ),
            100: SearchPage(
                files=(file,),
                next_cursors={"primary": None},
            ),
        },
    )

    result = await MultiBackendSearchService(RegistryStub(backend)).search(SearchRequest())

    assert result.files == (file,)
    assert backend.cursors == [None, 100]
    assert "已跳过 1 个" in result.warnings[0]


async def test_pagination_cursor_never_falls_back_to_another_node() -> None:
    primary = PagedBackendStub("primary", {100: RuntimeError("node failed")})
    fallback = PagedBackendStub(
        "fallback",
        {
            None: SearchPage(
                files=(LogicalFile(logical_key="wrong", filename="wrong.nc"),),
                next_cursors={"fallback": None},
            )
        },
    )
    service = MultiBackendSearchService(RegistryStub(primary, fallback))

    with pytest.raises(RuntimeError, match="已保留原页面"):
        await service.search(SearchRequest(), {"primary": 100})

    assert primary.cursors == [100]
    assert fallback.cursors == []
