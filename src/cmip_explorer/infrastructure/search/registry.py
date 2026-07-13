from __future__ import annotations

from collections.abc import Iterable

from cmip_explorer.domain.enums import BackendKind
from cmip_explorer.domain.models import Backend, BackendCapabilities

from .backends import LegacySolrBackend, OrnlBridgeBackend
from .interfaces import SearchBackend
from .provider_backends import (
    CdsCatalogueBackend,
    CmrBackend,
    NexStacBackend,
    NoaaNceiBackend,
    OpenMeteoBackend,
    PowerBackend,
)


class BackendRegistry:
    def __init__(self, backends: Iterable[SearchBackend] = ()) -> None:
        self._backends = {backend.definition.id: backend for backend in backends}

    def register(self, backend: SearchBackend) -> None:
        if backend.definition.id in self._backends:
            raise ValueError(f"backend already registered: {backend.definition.id}")
        self._backends[backend.definition.id] = backend

    def get(self, backend_id: str) -> SearchBackend:
        return self._backends[backend_id]

    def enabled(self, selected: tuple[str, ...] = ()) -> tuple[SearchBackend, ...]:
        wanted = set(selected)
        return tuple(
            backend
            for backend in sorted(
                self._backends.values(), key=lambda value: value.definition.priority
            )
            if backend.definition.enabled and (not wanted or backend.definition.id in wanted)
        )

    async def close(self) -> None:
        for backend in self._backends.values():
            await backend.close()


def default_registry(provider_id: str = "esgf") -> BackendRegistry:
    if provider_id != "esgf":
        return _provider_registry(provider_id)
    common = BackendCapabilities(
        distributed_search=True,
        facets=True,
        fields_parameter=True,
        replica_filter=True,
        temporal_filter=True,
        spatial_filter=True,
    )
    definitions = (
        Backend(
            id="dkrz",
            name="DKRZ",
            kind=BackendKind.LEGACY_SOLR,
            base_url="https://esgf-data.dkrz.de/esg-search/search",
            priority=10,
            capabilities=common,
        ),
        Backend(
            id="ipsl",
            name="IPSL",
            kind=BackendKind.LEGACY_SOLR,
            base_url="https://esgf-node.ipsl.upmc.fr/esg-search/search",
            priority=30,
            capabilities=common,
        ),
        Backend(
            id="ceda",
            name="CEDA",
            kind=BackendKind.LEGACY_SOLR,
            base_url="https://esgf.ceda.ac.uk/esg-search/search",
            priority=20,
            capabilities=common,
        ),
        Backend(
            id="ornl",
            name="ORNL Bridge",
            kind=BackendKind.ORNL_BRIDGE,
            base_url="https://esgf-node.ornl.gov/esgf-1-5-bridge/",
            priority=40,
            capabilities=common.model_copy(
                update={"fields_parameter": False, "replica_filter": False, "spatial_filter": False}
            ),
        ),
    )
    adapters: list[SearchBackend] = []
    for definition in definitions:
        if definition.kind is BackendKind.ORNL_BRIDGE:
            adapters.append(OrnlBridgeBackend(definition))
        else:
            adapters.append(LegacySolrBackend(definition))
    return BackendRegistry(adapters)


def _provider_registry(provider_id: str) -> BackendRegistry:
    capabilities = BackendCapabilities(
        distributed_search=False,
        facets=provider_id in {"cds", "aws", "planetary", "openmeteo"},
        fields_parameter=False,
        replica_filter=False,
        temporal_filter=True,
        spatial_filter=provider_id == "cmr",
        cursor_paging=provider_id in {"aws", "planetary", "cmr"},
    )
    definitions = {
        "cds": Backend(
            id="cds",
            name="Copernicus CDS",
            kind=BackendKind.CATALOGUE,
            base_url="https://cds.climate.copernicus.eu/api/catalogue/v1",
            priority=10,
            capabilities=capabilities,
        ),
        "aws": Backend(
            id="aws",
            name="AWS Open Data",
            kind=BackendKind.STAC,
            base_url="https://planetarycomputer.microsoft.com/api/stac/v1",
            priority=10,
            capabilities=capabilities,
        ),
        "planetary": Backend(
            id="planetary",
            name="Planetary Computer",
            kind=BackendKind.STAC,
            base_url="https://planetarycomputer.microsoft.com/api/stac/v1",
            priority=10,
            capabilities=capabilities,
        ),
        "power": Backend(
            id="power",
            name="NASA POWER",
            kind=BackendKind.GENERATED_API,
            base_url="https://power.larc.nasa.gov/api/temporal",
            priority=10,
            capabilities=capabilities,
        ),
        "openmeteo": Backend(
            id="openmeteo",
            name="Open-Meteo",
            kind=BackendKind.GENERATED_API,
            base_url="https://open-meteo.com",
            priority=10,
            capabilities=capabilities,
        ),
        "cmr": Backend(
            id="cmr",
            name="NASA Earthdata CMR",
            kind=BackendKind.CMR,
            base_url="https://cmr.earthdata.nasa.gov/search",
            priority=10,
            capabilities=capabilities,
        ),
        "noaa": Backend(
            id="noaa",
            name="NOAA NCEI",
            kind=BackendKind.GENERATED_API,
            base_url="https://www.ncei.noaa.gov/access/services/data/v1",
            priority=10,
            capabilities=capabilities,
        ),
    }
    try:
        definition = definitions[provider_id]
    except KeyError as exc:
        raise ValueError(f"unknown data provider: {provider_id}") from exc
    if provider_id == "cds":
        backend: SearchBackend = CdsCatalogueBackend(definition)
    elif provider_id in {"aws", "planetary"}:
        backend = NexStacBackend(definition, asset_source=provider_id)
    elif provider_id == "power":
        backend = PowerBackend(definition)
    elif provider_id == "openmeteo":
        backend = OpenMeteoBackend(definition)
    elif provider_id == "cmr":
        backend = CmrBackend(definition)
    else:
        backend = NoaaNceiBackend(definition)
    return BackendRegistry((backend,))
