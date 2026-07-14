from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from cmip_explorer.domain.models import (
    AccessEndpoint,
    LogicalFile,
    Replica,
    TemporalCoverage,
)

_TIME_RANGE_RE = re.compile(
    r"_(?P<start>\d{4}(?:\d{2}(?:\d{2}(?:\d{4})?)?)?)-"
    r"(?P<end>\d{4}(?:\d{2}(?:\d{2}(?:\d{4})?)?)?)\.nc(?:4)?$",
    re.IGNORECASE,
)


def scalar(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def parse_access_endpoint(raw: str) -> AccessEndpoint:
    parts = raw.rsplit("|", 2)
    url = parts[0]
    media_type = parts[1] if len(parts) > 1 else None
    service = parts[2] if len(parts) > 2 else "UNKNOWN"
    if service.upper() == "OPENDAP" and url.endswith(".html"):
        url = url[:-5]
    return AccessEndpoint(
        url=url,
        service=service,
        media_type=media_type,
        secure=url.lower().startswith("https://"),
    )


def parse_temporal_coverage(doc: dict[str, Any], filename: str) -> TemporalCoverage:
    start = scalar(doc.get("datetime_start"))
    end = scalar(doc.get("datetime_stop"))
    if start or end:
        return TemporalCoverage(
            start=str(start) if start else None, end=str(end) if end else None, source="api"
        )

    match = _TIME_RANGE_RE.search(filename)
    if match:
        return TemporalCoverage(
            start=match.group("start"), end=match.group("end"), source="filename"
        )
    if scalar(doc.get("frequency")) == "fx" or scalar(doc.get("table_id")) in {"fx", "Ofx", "Efx"}:
        return TemporalCoverage(source="static")
    return TemporalCoverage()


def normalize_solr_document(doc: dict[str, Any], backend_id: str) -> LogicalFile:
    title = str(scalar(doc.get("title")) or scalar(doc.get("id")) or "unknown.nc")
    filename = PurePosixPath(title).name
    node = str(scalar(doc.get("data_node")) or _node_from_id(str(scalar(doc.get("id")) or "")))
    endpoints = tuple(parse_access_endpoint(item) for item in string_tuple(doc.get("url")))
    checksum = scalar(doc.get("checksum"))
    checksum_type = scalar(doc.get("checksum_type"))
    replica = Replica(
        data_node=node,
        backend_id=backend_id,
        replica=bool(scalar(doc.get("replica")) or False),
        endpoints=endpoints,
        checksum=str(checksum) if checksum else None,
        checksum_type=str(checksum_type) if checksum_type else None,
    )
    master_id = scalar(doc.get("master_id"))
    instance_id = scalar(doc.get("instance_id"))
    logical_key = str(
        master_id or instance_id or _logical_id(str(scalar(doc.get("id")) or filename))
    )
    size = scalar(doc.get("size"))
    return LogicalFile(
        logical_key=logical_key,
        master_id=str(master_id) if master_id else None,
        instance_id=str(instance_id) if instance_id else None,
        filename=filename,
        dataset_id=_optional_string(doc, "dataset_id"),
        project=_optional_string(doc, "project") or "CMIP6",
        activity_id=_optional_string(doc, "activity_id"),
        institution_id=_optional_string(doc, "institution_id"),
        source_id=_optional_string(doc, "source_id"),
        experiment_id=_optional_string(doc, "experiment_id"),
        member_id=_optional_string(doc, "member_id", "variant_label"),
        table_id=_optional_string(doc, "table_id"),
        variable_id=_optional_string(doc, "variable_id", "variable"),
        grid_label=_optional_string(doc, "grid_label"),
        nominal_resolution=_optional_string(doc, "nominal_resolution"),
        frequency=_optional_string(doc, "frequency"),
        version=_optional_string(doc, "version"),
        size_bytes=int(size) if size is not None else None,
        temporal=parse_temporal_coverage(doc, filename),
        replicas=(replica,),
        raw_provenance={"backend_id": backend_id, "record_id": scalar(doc.get("id"))},
    )


def _optional_string(doc: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = scalar(doc.get(name))
        if value is not None:
            return str(value)
    return None


def _node_from_id(identifier: str) -> str:
    return identifier.rsplit("|", 1)[1] if "|" in identifier else "unknown"


def _logical_id(identifier: str) -> str:
    return identifier.rsplit("|", 1)[0]


def merge_logical_files(files: Iterable[LogicalFile]) -> tuple[LogicalFile, ...]:
    merged: dict[str, LogicalFile] = {}
    for item in files:
        current = merged.get(item.logical_key)
        if current is None:
            merged[item.logical_key] = item
            continue
        replicas = _deduplicate_replicas((*current.replicas, *item.replicas))
        provenance = dict(current.raw_provenance)
        sources = set(provenance.get("backend_ids", []))
        sources.add(str(current.raw_provenance.get("backend_id", "")))
        sources.add(str(item.raw_provenance.get("backend_id", "")))
        provenance["backend_ids"] = sorted(source for source in sources if source)
        merged[item.logical_key] = current.model_copy(
            update={"replicas": replicas, "raw_provenance": provenance}
        )
    return tuple(sorted(merged.values(), key=lambda item: item.logical_key))


def group_time_series(files: Iterable[LogicalFile]) -> tuple[LogicalFile, ...]:
    """Collapse ESGF time-slice files into downloadable dataset series."""
    groups: dict[tuple[str, ...], list[LogicalFile]] = {}
    singles: list[LogicalFile] = []
    for item in files:
        if not item.temporal.start or not item.temporal.end:
            singles.append(item)
            continue
        groups.setdefault(_series_group_key(item), []).append(item)

    result = list(singles)
    for key, members in groups.items():
        unique = {member.logical_key: member for member in members}
        ordered = tuple(
            sorted(
                unique.values(),
                key=lambda member: (
                    member.temporal.start or "",
                    member.temporal.end or "",
                    member.filename,
                ),
            )
        )
        if len(ordered) == 1:
            result.append(ordered[0])
        else:
            result.append(_series_file(key, ordered))
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.variable_id or "",
                item.source_id or "",
                item.experiment_id or "",
                item.member_id or "",
                item.table_id or "",
                item.grid_label or "",
                item.temporal.start or "",
                item.logical_key,
            ),
        )
    )


def _series_group_key(file: LogicalFile) -> tuple[str, ...]:
    return tuple(
        value or ""
        for value in (
            file.provider_id,
            file.project,
            file.activity_id,
            file.institution_id,
            file.source_id,
            file.experiment_id,
            file.member_id,
            file.table_id,
            file.variable_id,
            file.grid_label,
            file.nominal_resolution,
            file.frequency,
            file.version,
        )
    )


def _series_file(key: tuple[str, ...], members: tuple[LogicalFile, ...]) -> LogicalFile:
    first = members[0]
    starts = [member.temporal.start for member in members if member.temporal.start]
    ends = [member.temporal.end for member in members if member.temporal.end]
    start = min(starts) if starts else None
    end = max(ends) if ends else None
    size = (
        sum(member.size_bytes for member in members if member.size_bytes is not None)
        if all(member.size_bytes is not None for member in members)
        else None
    )
    start_label = start[:4] if start else "unknown"
    end_label = end[:4] if end else "unknown"
    name_parts = (
        first.variable_id or "data",
        first.table_id or first.frequency or "series",
        first.source_id or "model",
        first.experiment_id or "scenario",
        first.member_id or "member",
        first.grid_label or "grid",
        f"{start_label}-{end_label}",
        f"{len(members)}files",
    )
    provenance = dict(first.raw_provenance)
    provenance.update(
        {
            "access_note": f"整套下载 ({len(members)} 个 NC 文件)",
            "series_file_count": len(members),
            "series_key": "|".join(key),
        }
    )
    return first.model_copy(
        update={
            "logical_key": f"series:{'|'.join(key)}",
            "filename": "_".join(name_parts) + ".series",
            "dataset_id": f"series:{'|'.join(key)}",
            "size_bytes": size,
            "temporal": TemporalCoverage(start=start, end=end, source="filename"),
            "replicas": (),
            "series_members": members,
            "raw_provenance": provenance,
        }
    )


def _deduplicate_replicas(replicas: Iterable[Replica]) -> tuple[Replica, ...]:
    result: dict[tuple[str, str, str | None], Replica] = {}
    for replica in replicas:
        result[(replica.data_node, replica.backend_id, replica.checksum)] = replica
    return tuple(result.values())
