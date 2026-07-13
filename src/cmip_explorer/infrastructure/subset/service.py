from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cmip_explorer.domain.enums import FailureCode
from cmip_explorer.domain.errors import ExplorerError
from cmip_explorer.domain.models import AccessEndpoint, LogicalFile

from .opendap import OpendapSubsetProvider, SubsetResult


class StrictSubsetService:
    def __init__(
        self,
        provider: OpendapSubsetProvider | None = None,
        *,
        allow_insecure_http: bool = False,
    ) -> None:
        self.provider = provider or OpendapSubsetProvider()
        self.allow_insecure_http = allow_insecure_http

    async def subset(
        self,
        file: LogicalFile,
        *,
        variable_id: str,
        bbox: tuple[float, float, float, float],
        start_year: int,
        end_year: int,
        target: Path,
    ) -> SubsetResult:
        endpoints = sorted(
            _opendap_candidates(file, self.allow_insecure_http),
            key=lambda endpoint: (not endpoint.secure, endpoint.url),
        )
        attempts = []
        for endpoint in endpoints:
            try:
                capability = await self.provider.probe(endpoint, variable_id)
                if not (
                    capability.available
                    and capability.supports_variable
                    and capability.supports_time
                    and capability.supports_space
                ):
                    attempts.append({"endpoint": endpoint.url, "reason": capability.reason})
                    continue
                plan = await self.provider.plan(endpoint, variable_id, bbox, start_year, end_year)
                return await self.provider.fetch(plan, target)
            except Exception as exc:
                attempts.append({"endpoint": endpoint.url, "reason": str(exc)})
        raise ExplorerError(
            FailureCode.REMOTE_SUBSET_UNAVAILABLE,
            "all strict remote subset endpoints failed; no full download was created",
            retriable=True,
            details={"file_key": file.logical_key, "attempts": attempts},
        )


def _opendap_candidates(file: LogicalFile, allow_insecure_http: bool) -> tuple[AccessEndpoint, ...]:
    candidates: dict[str, AccessEndpoint] = {}
    for replica in file.replicas:
        for endpoint in replica.endpoints:
            if endpoint.service.upper() != "OPENDAP":
                continue
            parsed = urlsplit(endpoint.url)
            if parsed.scheme.lower() == "http":
                upgraded_url = urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
                candidates[upgraded_url] = endpoint.model_copy(
                    update={"url": upgraded_url, "secure": True}
                )
                if allow_insecure_http:
                    candidates[endpoint.url] = endpoint
            else:
                candidates[endpoint.url] = endpoint
    return tuple(candidates.values())
