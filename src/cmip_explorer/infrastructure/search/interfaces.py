from __future__ import annotations

from typing import Protocol

from cmip_explorer.domain.models import Backend, BackendCapabilities, SearchPage, SearchRequest


class SearchBackend(Protocol):
    definition: Backend

    async def detect_capabilities(self) -> BackendCapabilities: ...

    async def health_check(self) -> tuple[bool, float, str | None]: ...

    async def search(
        self, request: SearchRequest, cursor: str | int | None = None
    ) -> SearchPage: ...

    async def facets(
        self, request: SearchRequest, names: tuple[str, ...]
    ) -> dict[str, dict[str, int]]: ...

    async def close(self) -> None: ...
