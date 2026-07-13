from __future__ import annotations

from enum import StrEnum


class BackendKind(StrEnum):
    LEGACY_SOLR = "legacy_solr"
    ORNL_BRIDGE = "ornl_bridge"
    STAC = "stac"
    CATALOGUE = "catalogue"
    GENERATED_API = "generated_api"
    CMR = "cmr"


class DownloadMode(StrEnum):
    REMOTE_SUBSET = "remote_subset"
    FULL_FILE = "full_file"
    DIRECT_FILE = "direct_file"


class ConfirmationScope(StrEnum):
    FILE = "file"
    LOGICAL_DATASET = "logical_dataset"
    JOB_REMAINDER = "job_remainder"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    PROBING = "probing"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    VERIFYING = "verifying"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class FailureCode(StrEnum):
    REMOTE_SUBSET_UNAVAILABLE = "remote_subset_unavailable"
    REMOTE_SUBSET_TIMEOUT = "remote_subset_timeout"
    REMOTE_SUBSET_INVALID_RESPONSE = "remote_subset_invalid_response"
    COORDINATE_ERROR = "coordinate_error"
    VARIABLE_MISSING = "variable_missing"
    TIME_UNAVAILABLE = "time_unavailable"
    SPATIAL_UNAVAILABLE = "spatial_unavailable"
    SERVICE_ERROR = "service_error"
    TLS_ERROR = "tls_error"
    UNSUPPORTED_GRID = "unsupported_grid"
    CHECKSUM_CONFLICT = "checksum_conflict"
    VALIDATION_FAILED = "validation_failed"
    DISK_SPACE_INSUFFICIENT = "disk_space_insufficient"
    DOWNLOAD_NOT_CONFIRMED = "download_not_confirmed"
