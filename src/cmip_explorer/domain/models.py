from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .enums import BackendKind, ConfirmationScope, DownloadMode, FailureCode, TaskStatus


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


class BackendCapabilities(DomainModel):
    distributed_search: bool = False
    facets: bool = True
    fields_parameter: bool = True
    replica_filter: bool = True
    temporal_filter: bool = False
    spatial_filter: bool = False
    cursor_paging: bool = False


class Backend(DomainModel):
    id: str
    name: str
    kind: BackendKind
    base_url: HttpUrl
    enabled: bool = True
    priority: int = 100
    capabilities: BackendCapabilities = Field(default_factory=BackendCapabilities)


class FacetConstraint(DomainModel):
    name: str
    values: tuple[str, ...]
    exclude: bool = False


class SearchRequest(DomainModel):
    provider_id: str = "esgf"
    product_id: str | None = None
    project: str = "CMIP6"
    text: str | None = None
    facets: tuple[FacetConstraint, ...] = ()
    type: Literal["Dataset", "File"] = "File"
    latest: bool = True
    replicas: Literal["masters", "replicas", "all"] = "all"
    start_year: int | None = None
    end_year: int | None = None
    page_size: int = Field(default=100, ge=1, le=1000)
    sort: str = "logical_key"
    backend_ids: tuple[str, ...] = ()
    parameters: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_years(self) -> SearchRequest:
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.start_year > self.end_year
        ):
            raise ValueError("start_year must not be after end_year")
        return self


class AccessEndpoint(DomainModel):
    url: str
    service: str
    media_type: str | None = None
    secure: bool = False


class TemporalCoverage(DomainModel):
    start: str | None = None
    end: str | None = None
    source: Literal["api", "stac", "filename", "netcdf", "static", "unknown"] = "unknown"
    conflict: bool = False


class Replica(DomainModel):
    data_node: str
    backend_id: str
    replica: bool
    endpoints: tuple[AccessEndpoint, ...] = ()
    checksum: str | None = None
    checksum_type: str | None = None


class LogicalFile(DomainModel):
    logical_key: str
    provider_id: str = "esgf"
    product_id: str | None = None
    master_id: str | None = None
    instance_id: str | None = None
    filename: str
    dataset_id: str | None = None
    project: str = "CMIP6"
    activity_id: str | None = None
    institution_id: str | None = None
    source_id: str | None = None
    experiment_id: str | None = None
    member_id: str | None = None
    table_id: str | None = None
    variable_id: str | None = None
    grid_label: str | None = None
    nominal_resolution: str | None = None
    frequency: str | None = None
    version: str | None = None
    size_bytes: int | None = None
    temporal: TemporalCoverage = Field(default_factory=TemporalCoverage)
    replicas: tuple[Replica, ...] = ()
    series_members: tuple[LogicalFile, ...] = ()
    raw_provenance: dict[str, Any] = Field(default_factory=dict)

    @property
    def download_files(self) -> tuple[LogicalFile, ...]:
        """Return the physical files represented by this search row."""
        return self.series_members or (self,)

    @property
    def file_count(self) -> int:
        return len(self.download_files)


class SearchPage(DomainModel):
    files: tuple[LogicalFile, ...]
    raw_total_by_backend: dict[str, int] = Field(default_factory=dict)
    known_unique_count: int = 0
    exact_total: bool = False
    next_cursors: dict[str, str | int | None] = Field(default_factory=dict)
    facet_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class VariableDefinition(DomainModel):
    project: str
    table_id: str
    variable_id: str
    frequency: str | None = None
    modeling_realm: str | None = None
    standard_name: str | None = None
    long_name: str
    units: str
    cell_methods: str | None = None
    dimensions: str | None = None
    comment: str | None = None
    chinese_name: str | None = None
    chinese_description: str | None = None
    aliases: tuple[str, ...] = ()
    source_version: str

    @property
    def key(self) -> str:
        return f"{self.project}:{self.table_id}:{self.variable_id}"


class Region(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    source_path: str
    source_sha256: str
    source_crs: str
    normalized_crs: str = "EPSG:4326"
    geometry_wkb_hex: str
    bbox: tuple[float, float, float, float]
    repaired: bool = False
    selected_feature_ids: tuple[str, ...] = ()


class RemoteSubsetCapability(DomainModel):
    endpoint: AccessEndpoint
    available: bool
    protocol: Literal["opendap2", "opendap4", "stac-zarr", "stac-reference", "unknown"]
    supports_variable: bool = False
    supports_time: bool = False
    supports_space: bool = False
    tls_valid: bool = False
    reason: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IndexWindow(DomainModel):
    time_start: int
    time_stop: int
    y_start: int
    y_stop: int
    x_start: int
    x_stop: int


class RemoteSubsetRequest(DomainModel):
    file_key: str
    endpoint: AccessEndpoint
    variable_id: str
    windows: tuple[IndexWindow, ...]
    start_year: int
    end_year: int
    region_id: UUID


class UserConfirmation(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    scope: ConfirmationScope
    target_key: str
    failure_code: FailureCode
    estimated_bytes: int
    failure_snapshot: dict[str, Any]
    plan_hash: str
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DownloadTask(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    file_key: str
    mode: DownloadMode
    status: TaskStatus = TaskStatus.QUEUED
    source_url: str
    target_path: str
    expected_size: int | None = None
    checksum: str | None = None
    checksum_type: str | None = None
    confirmation_id: UUID | None = None
    progress_bytes: int = 0
    failure_code: FailureCode | None = None

    @model_validator(mode="after")
    def full_download_has_confirmation(self) -> DownloadTask:
        if self.mode is DownloadMode.FULL_FILE and self.confirmation_id is None:
            raise ValueError("full file download requires confirmation_id")
        return self


class ConversionJob(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    variable_id: str
    source_unit: str
    target_unit: str
    statistic: Literal["mean", "sum", "min", "max"] = "mean"
    temporal_resolution: Literal["annual", "monthly", "daily"] = "annual"
    start_year: int
    end_year: int
    output_format: Literal["COG", "GTiff"] = "COG"
    all_touched: bool = False


class OutputArtifact(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    path: str
    kind: Literal["geotiff", "cog", "netcdf", "manifest", "log"]
    sha256: str
    size_bytes: int
    year: int | None = None


class ProcessingManifest(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    app_version: str
    job_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    search_request: dict[str, Any]
    source_files: tuple[dict[str, Any], ...]
    region: dict[str, Any]
    operations: tuple[dict[str, Any], ...]
    confirmations: tuple[dict[str, Any], ...] = ()
    artifacts: tuple[OutputArtifact, ...]
