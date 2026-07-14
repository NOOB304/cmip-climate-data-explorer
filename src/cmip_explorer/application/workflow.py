from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx

from cmip_explorer.config import AppPaths
from cmip_explorer.domain.enums import (
    ConfirmationScope,
    DownloadMode,
    FailureCode,
    TaskStatus,
)
from cmip_explorer.domain.errors import ExplorerError
from cmip_explorer.domain.models import (
    AccessEndpoint,
    DownloadTask,
    LogicalFile,
    Region,
    Replica,
    UserConfirmation,
)
from cmip_explorer.infrastructure.download import (
    DownloadCancelled,
    DownloadControl,
    HttpRangeDownloader,
)
from cmip_explorer.infrastructure.persistence import TaskRepository
from cmip_explorer.infrastructure.processing import (
    ProcessingOptions,
    ProcessingResult,
    convert_netcdf_to_geotiffs,
    process_to_geotiffs,
)
from cmip_explorer.infrastructure.subset import StrictSubsetService, SubsetResult


@dataclass(frozen=True, slots=True)
class JobContext:
    id: UUID
    name: str
    plan_hash: str
    root: Path


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed_tasks: int
    removed_files: int
    freed_bytes: int


class WorkflowService:
    def __init__(
        self,
        paths: AppPaths,
        repository: TaskRepository,
        subset_service: StrictSubsetService | None = None,
        downloader: HttpRangeDownloader | None = None,
        allow_insecure_http: bool = False,
        storage_root: Path | None = None,
        auto_convert_to_tif: bool = True,
        reconnect_delays: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 30.0),
    ) -> None:
        self.paths = paths
        self.repository = repository
        self.subset_service = subset_service or StrictSubsetService()
        # The workflow owns retries and mirror switching for climate files.
        # Keep the transport retry-free here so the two layers cannot multiply.
        self.downloader = downloader or HttpRangeDownloader(reconnect_delays=())
        self.allow_insecure_http = allow_insecure_http
        self.storage_root = storage_root or paths.outputs
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.auto_convert_to_tif = auto_convert_to_tif
        self.reconnect_delays = reconnect_delays
        self.shutdown_requested = False
        self._download_controls: dict[UUID, DownloadControl] = {}

    def create_job(self, name: str, plan: dict) -> JobContext:
        serialized = json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
        plan_hash = hashlib.sha256(serialized).hexdigest()
        job_id = uuid4()
        root = self.paths.data / "jobs" / str(job_id)
        root.mkdir(parents=True, exist_ok=True)
        self.repository.create_job(job_id, name, plan_hash)
        return JobContext(job_id, name, plan_hash, root)

    async def strict_subset(
        self,
        job: JobContext,
        file: LogicalFile,
        region: Region,
        variable_id: str,
        start_year: int,
        end_year: int,
    ) -> SubsetResult:
        source_url = next(
            (
                endpoint.url
                for replica in file.replicas
                for endpoint in replica.endpoints
                if endpoint.service.upper() == "OPENDAP"
            ),
            file.logical_key,
        )
        target = job.root / "subsets" / file.filename
        task = DownloadTask(
            job_id=job.id,
            file_key=file.logical_key,
            mode=DownloadMode.REMOTE_SUBSET,
            source_url=source_url,
            target_path=str(target),
        )
        self.repository.create_task(task)
        try:
            self.repository.transition(task.id, TaskStatus.RESOLVING)
            self.repository.transition(task.id, TaskStatus.PROBING)
            self.repository.transition(task.id, TaskStatus.DOWNLOADING)
            result = await self.subset_service.subset(
                file,
                variable_id=variable_id,
                bbox=region.bbox,
                start_year=start_year,
                end_year=end_year,
                target=target,
            )
            self.repository.update_progress(task.id, result.bytes_written)
            self.repository.transition(task.id, TaskStatus.VERIFYING)
            self.repository.transition(task.id, TaskStatus.COMPLETED)
            return result
        except Exception as exc:
            details = exc.details if isinstance(exc, ExplorerError) else {}
            with suppress(Exception):
                self.repository.transition(
                    task.id,
                    TaskStatus.FAILED,
                    {"reason": str(exc), "details": details},
                )
            raise

    def confirm_full_download(
        self,
        job: JobContext,
        file: LogicalFile,
        failure: ExplorerError,
        estimated_bytes: int,
        scope: ConfirmationScope = ConfirmationScope.FILE,
    ) -> UserConfirmation:
        confirmation = UserConfirmation(
            job_id=job.id,
            scope=scope,
            target_key=file.logical_key,
            failure_code=failure.code,
            estimated_bytes=estimated_bytes,
            failure_snapshot=failure.details,
            plan_hash=job.plan_hash,
        )
        self.repository.record_confirmation(confirmation)
        return confirmation

    async def full_download(
        self,
        job: JobContext,
        file: LogicalFile,
        confirmation: UserConfirmation,
    ) -> Path:
        candidates = _http_download_candidates(file, self.allow_insecure_http)
        if not candidates:
            raise ExplorerError(
                FailureCode.SERVICE_ERROR,
                "no secure HTTPServer endpoint is available",
                {"allow_insecure_http": self.allow_insecure_http},
            )
        target = job.root / "full" / file.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(target.parent).free
        reserve_bytes = 512 * 1024 * 1024
        if file.size_bytes is not None and file.size_bytes + reserve_bytes > free_bytes:
            raise ExplorerError(
                FailureCode.DISK_SPACE_INSUFFICIENT,
                "insufficient disk space for the confirmed full download",
                {
                    "required_bytes": file.size_bytes,
                    "reserved_bytes": reserve_bytes,
                    "free_bytes": free_bytes,
                    "target": str(target),
                },
            )
        checksum, checksum_type = _preferred_checksum(file)
        task = DownloadTask(
            job_id=job.id,
            file_key=file.logical_key,
            mode=DownloadMode.FULL_FILE,
            source_url=candidates[0],
            target_path=str(target),
            expected_size=file.size_bytes,
            checksum=checksum,
            checksum_type=checksum_type,
            confirmation_id=confirmation.id,
        )
        self.repository.create_task(task)
        self.repository.record_event(
            task.id, "download_candidates", {"urls": list(candidates)}
        )
        control = DownloadControl()
        self._download_controls[task.id] = control
        self.repository.transition(task.id, TaskStatus.RESOLVING)
        self.repository.transition(task.id, TaskStatus.PROBING)
        self.repository.transition(task.id, TaskStatus.DOWNLOADING)
        failures: list[dict[str, str]] = []
        for index, url in enumerate(candidates):
            self.repository.update_source_url(task.id, url)
            self.repository.record_event(
                task.id,
                "mirror_attempt",
                {"url": url, "candidate": index + 1, "total": len(candidates)},
            )
            try:
                result = await self.downloader.download(
                    url,
                    target,
                    expected_size=file.size_bytes,
                    expected_checksum=checksum,
                    checksum_type=checksum_type or "SHA256",
                    progress=lambda written, _total: self.repository.update_progress(
                        task.id, written
                    ),
                    control=control,
                )
            except DownloadCancelled:
                if self.repository.status(task.id) is not TaskStatus.CANCELED:
                    self.repository.transition(task.id, TaskStatus.CANCELED)
                self._download_controls.pop(task.id, None)
                raise
            except Exception as exc:
                failures.append({"url": url, "error_type": type(exc).__name__, "error": str(exc)})
                self.repository.record_event(task.id, "mirror_failed", failures[-1])
                if index + 1 < len(candidates):
                    self.repository.transition(task.id, TaskStatus.RETRY_WAIT)
                    self.repository.transition(task.id, TaskStatus.RESOLVING)
                    self.repository.transition(task.id, TaskStatus.PROBING)
                    self.repository.transition(task.id, TaskStatus.DOWNLOADING)
                    continue
                self.repository.transition(
                    task.id,
                    TaskStatus.FAILED,
                    {"reason": str(exc), "mirror_failures": failures},
                )
                self._download_controls.pop(task.id, None)
                raise ExplorerError(
                    FailureCode.SERVICE_ERROR,
                    "all confirmed full-download mirrors failed",
                    {"attempts": failures},
                ) from exc
            self.repository.transition(task.id, TaskStatus.VERIFYING)
            self.repository.transition(task.id, TaskStatus.COMPLETED)
            self._download_controls.pop(task.id, None)
            return result.path
        raise RuntimeError("unreachable mirror selection state")

    def enqueue_download(self, job: JobContext, file: LogicalFile) -> tuple[UUID, bool]:
        existing = self.repository.active_task_id(file.logical_key)
        if existing is not None:
            return existing, False
        candidates = _http_download_candidates(file, self.allow_insecure_http)
        if not candidates:
            raise ExplorerError(FailureCode.SERVICE_ERROR, "没有找到可用的文件下载地址")
        target = (
            self.storage_root
            / _download_category(file.filename)
            / _path_segment(file.source_id or "未知模型")
            / _path_segment(file.experiment_id or "未知情景")
            / file.filename
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        reserve_bytes = 512 * 1024 * 1024
        free_bytes = shutil.disk_usage(target.parent).free
        if file.size_bytes is not None and file.size_bytes + reserve_bytes > free_bytes:
            raise ExplorerError(
                FailureCode.DISK_SPACE_INSUFFICIENT,
                "存储位置空间不足",
                {"required_bytes": file.size_bytes, "free_bytes": free_bytes},
            )
        checksum, checksum_type = _preferred_checksum(file)
        task = DownloadTask(
            job_id=job.id,
            file_key=file.logical_key,
            mode=DownloadMode.DIRECT_FILE,
            source_url=candidates[0],
            target_path=str(target),
            expected_size=file.size_bytes,
            checksum=checksum,
            checksum_type=checksum_type,
        )
        self.repository.create_task(task)
        self.repository.record_event(
            task.id, "download_candidates", {"urls": list(candidates)}
        )
        return task.id, True

    async def download_file(self, job: JobContext, file: LogicalFile) -> Path:
        task_id, _created = self.enqueue_download(job, file)
        return await self.run_download_task(task_id, file)

    async def run_download_task(self, task_id: UUID, file: LogicalFile) -> Path:
        status = self.repository.status(task_id)
        if status is TaskStatus.CANCELED:
            raise DownloadCancelled()
        if status is TaskStatus.COMPLETED:
            return Path(self.repository.task_details(task_id).target_path)
        if status is TaskStatus.FAILED:
            self.repository.transition(task_id, TaskStatus.QUEUED, {"manual_retry": True})
            status = TaskStatus.QUEUED
        if status is TaskStatus.INTERRUPTED:
            self.repository.transition(task_id, TaskStatus.DOWNLOADING, {"recovered": True})
        elif status is TaskStatus.QUEUED:
            self.repository.transition(task_id, TaskStatus.RESOLVING)
            self.repository.transition(task_id, TaskStatus.PROBING)
            self.repository.transition(task_id, TaskStatus.DOWNLOADING)
        else:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                f"当前任务状态不能开始下载: {status.value}",
            )

        details = self.repository.task_details(task_id)
        target = Path(details.target_path)
        candidates = _http_download_candidates(file, self.allow_insecure_http)
        if details.source_url not in candidates:
            candidates = (details.source_url, *candidates)
        control = DownloadControl()
        self._download_controls[task_id] = control
        failures: list[dict[str, str]] = []
        try:
            if (
                target.exists()
                and details.expected_size is not None
                and target.stat().st_size == details.expected_size
            ):
                self.repository.update_progress(task_id, details.expected_size)
                return self._finalize_download(task_id, file, target, control)
            for mirror_index, url in enumerate(candidates):
                result = None
                for attempt in range(1, len(self.reconnect_delays) + 2):
                    self._raise_if_stopped(task_id, control)
                    self.repository.update_source_url(task_id, url)
                    self.repository.record_event(
                        task_id,
                        "mirror_attempt",
                        {
                            "url": url,
                            "candidate": mirror_index + 1,
                            "total": len(candidates),
                            "attempt": attempt,
                        },
                    )
                    try:
                        result = await self.downloader.download(
                            url,
                            target,
                            expected_size=details.expected_size,
                            expected_checksum=details.checksum,
                            checksum_type=details.checksum_type or "SHA256",
                            progress=lambda written, _total: self.repository.update_progress(
                                task_id, written
                            ),
                            control=control,
                        )
                        break
                    except DownloadCancelled:
                        self._finish_stopped_task(task_id)
                        raise
                    except Exception as exc:
                        failure = {
                            "url": url,
                            "error_type": type(exc).__name__,
                            "error": _error_text(exc),
                            "attempt": str(attempt),
                        }
                        self.repository.record_event(task_id, "connection_failed", failure)
                        retry = attempt <= len(self.reconnect_delays) and _is_retryable(exc)
                        if retry:
                            delay = self.reconnect_delays[attempt - 1]
                            retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                            self.repository.transition(
                                task_id,
                                TaskStatus.RETRY_WAIT,
                                {
                                    "attempt": attempt,
                                    "reconnect_attempt": attempt,
                                    "retry_maximum": len(self.reconnect_delays),
                                    "retry_in_seconds": delay,
                                    "retry_at": retry_at.isoformat(),
                                },
                            )
                            await self._wait_for_reconnect(task_id, control, delay)
                            self.repository.transition(task_id, TaskStatus.DOWNLOADING)
                            continue
                        failures.append(failure)
                        break
                if result is None:
                    if mirror_index + 1 < len(candidates):
                        if self.repository.status(task_id) is TaskStatus.DOWNLOADING:
                            self.repository.transition(task_id, TaskStatus.RETRY_WAIT)
                        self.repository.transition(task_id, TaskStatus.RESOLVING)
                        self.repository.transition(task_id, TaskStatus.PROBING)
                        self.repository.transition(task_id, TaskStatus.DOWNLOADING)
                        continue
                    reason = failures[-1]["error"] if failures else "未知网络错误"
                    self.repository.transition(
                        task_id,
                        TaskStatus.FAILED,
                        {"reason": reason, "connection_failures": failures},
                    )
                    raise ExplorerError(
                        FailureCode.SERVICE_ERROR,
                        "自动重连和备用节点均失败, 可稍后点击“重新连接”继续",
                        {"attempts": failures},
                    )

                return self._finalize_download(task_id, file, result.path, control)
        finally:
            self._download_controls.pop(task_id, None)
        raise RuntimeError("unreachable mirror selection state")

    def _finalize_download(
        self,
        task_id: UUID,
        file: LogicalFile,
        target: Path,
        control: DownloadControl,
    ) -> Path:
        self.repository.transition(task_id, TaskStatus.VERIFYING)
        if self.shutdown_requested:
            self._finish_stopped_task(task_id)
            raise DownloadCancelled()
        if self.auto_convert_to_tif and _should_auto_convert(file):
            self.repository.transition(task_id, TaskStatus.PROCESSING)
            tif_root = (
                self.storage_root
                / "GeoTIFF"
                / _path_segment(file.source_id or "未知模型")
                / _path_segment(file.experiment_id or "未知情景")
                / target.stem
            )
            try:
                convert_netcdf_to_geotiffs(
                    target,
                    tif_root,
                    file.variable_id,
                    cancelled=lambda: control.cancel_requested or self.shutdown_requested,
                )
            except InterruptedError:
                self._finish_stopped_task(task_id)
                raise DownloadCancelled() from None
            except Exception as exc:
                self.repository.transition(
                    task_id,
                    TaskStatus.FAILED,
                    {"reason": str(exc), "phase": "GeoTIFF conversion"},
                )
                raise
        elif self.auto_convert_to_tif:
            self.repository.record_event(
                task_id,
                "automatic_conversion_skipped",
                {
                    "frequency": file.frequency,
                    "reason": "high-frequency data is kept as NetCDF to avoid excessive files",
                },
            )
        self.repository.transition(task_id, TaskStatus.COMPLETED)
        return target

    def _raise_if_stopped(self, task_id: UUID, control: DownloadControl) -> None:
        if control.cancel_requested or self.shutdown_requested:
            self._finish_stopped_task(task_id)
            raise DownloadCancelled()

    def _finish_stopped_task(self, task_id: UUID) -> None:
        status = self.repository.status(task_id)
        if status in {TaskStatus.CANCELED, TaskStatus.INTERRUPTED}:
            return
        if self.shutdown_requested and status in {
            TaskStatus.DOWNLOADING,
            TaskStatus.PROCESSING,
        }:
            self.repository.transition(task_id, TaskStatus.INTERRUPTED)
        elif status in {
            TaskStatus.QUEUED,
            TaskStatus.RESOLVING,
            TaskStatus.PROBING,
            TaskStatus.DOWNLOADING,
            TaskStatus.PAUSED,
            TaskStatus.RETRY_WAIT,
            TaskStatus.VERIFYING,
            TaskStatus.PROCESSING,
        }:
            self.repository.transition(task_id, TaskStatus.CANCELED)

    async def _wait_for_reconnect(
        self, task_id: UUID, control: DownloadControl, delay: float
    ) -> None:
        remaining = delay
        while remaining > 0:
            self._raise_if_stopped(task_id, control)
            interval = min(0.2, remaining)
            await asyncio.sleep(interval)
            remaining -= interval

    async def _legacy_download_file(self, job: JobContext, file: LogicalFile) -> Path:
        """Download a user-selected file without the legacy strict-subset confirmation flow."""
        candidates = _http_download_candidates(file, self.allow_insecure_http)
        if not candidates:
            raise ExplorerError(
                FailureCode.SERVICE_ERROR,
                "没有找到可用的 HTTPS 下载地址",
                {"allow_insecure_http": self.allow_insecure_http},
            )
        target = (
            self.storage_root
            / "NetCDF"
            / _path_segment(file.source_id or "未知模型")
            / _path_segment(file.experiment_id or "未知情景")
            / file.filename
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(target.parent).free
        reserve_bytes = 512 * 1024 * 1024
        if file.size_bytes is not None and file.size_bytes + reserve_bytes > free_bytes:
            raise ExplorerError(
                FailureCode.DISK_SPACE_INSUFFICIENT,
                "存储位置空间不足",
                {
                    "required_bytes": file.size_bytes,
                    "reserved_bytes": reserve_bytes,
                    "free_bytes": free_bytes,
                    "target": str(target),
                },
            )
        checksum, checksum_type = _preferred_checksum(file)
        task = DownloadTask(
            job_id=job.id,
            file_key=file.logical_key,
            mode=DownloadMode.DIRECT_FILE,
            source_url=candidates[0],
            target_path=str(target),
            expected_size=file.size_bytes,
            checksum=checksum,
            checksum_type=checksum_type,
        )
        self.repository.create_task(task)
        control = DownloadControl()
        self._download_controls[task.id] = control
        self.repository.transition(task.id, TaskStatus.RESOLVING)
        self.repository.transition(task.id, TaskStatus.PROBING)
        self.repository.transition(task.id, TaskStatus.DOWNLOADING)
        failures: list[dict[str, str]] = []
        try:
            for index, url in enumerate(candidates):
                self.repository.update_source_url(task.id, url)
                self.repository.record_event(
                    task.id,
                    "mirror_attempt",
                    {"url": url, "candidate": index + 1, "total": len(candidates)},
                )
                try:
                    result = await self.downloader.download(
                        url,
                        target,
                        expected_size=file.size_bytes,
                        expected_checksum=checksum,
                        checksum_type=checksum_type or "SHA256",
                        progress=lambda written, _total: self.repository.update_progress(
                            task.id, written
                        ),
                        control=control,
                    )
                except DownloadCancelled:
                    if self.repository.status(task.id) is not TaskStatus.CANCELED:
                        self.repository.transition(task.id, TaskStatus.CANCELED)
                    raise
                except Exception as exc:
                    failures.append(
                        {"url": url, "error_type": type(exc).__name__, "error": str(exc)}
                    )
                    self.repository.record_event(task.id, "mirror_failed", failures[-1])
                    if index + 1 < len(candidates):
                        self.repository.transition(task.id, TaskStatus.RETRY_WAIT)
                        self.repository.transition(task.id, TaskStatus.RESOLVING)
                        self.repository.transition(task.id, TaskStatus.PROBING)
                        self.repository.transition(task.id, TaskStatus.DOWNLOADING)
                        continue
                    self.repository.transition(
                        task.id,
                        TaskStatus.FAILED,
                        {"reason": str(exc), "mirror_failures": failures},
                    )
                    raise ExplorerError(
                        FailureCode.SERVICE_ERROR,
                        "所有下载节点均连接失败",
                        {"attempts": failures},
                    ) from exc
                self.repository.transition(task.id, TaskStatus.VERIFYING)
                if self.auto_convert_to_tif:
                    self.repository.transition(task.id, TaskStatus.PROCESSING)
                    tif_root = (
                        self.storage_root
                        / "GeoTIFF"
                        / _path_segment(file.source_id or "未知模型")
                        / _path_segment(file.experiment_id or "未知情景")
                        / target.stem
                    )
                    try:
                        convert_netcdf_to_geotiffs(
                            result.path,
                            tif_root,
                            file.variable_id,
                        )
                    except Exception as exc:
                        self.repository.transition(
                            task.id,
                            TaskStatus.FAILED,
                            {"reason": str(exc), "phase": "GeoTIFF conversion"},
                        )
                        raise
                self.repository.transition(task.id, TaskStatus.COMPLETED)
                return result.path
        finally:
            self._download_controls.pop(task.id, None)
        raise RuntimeError("unreachable mirror selection state")

    def pause_task(self, task_id: UUID) -> bool:
        control = self._download_controls.get(task_id)
        if control is None or self.repository.status(task_id) is not TaskStatus.DOWNLOADING:
            return False
        control.pause_requested = True
        self.repository.transition(task_id, TaskStatus.PAUSED)
        return True

    def resume_task(self, task_id: UUID) -> bool:
        control = self._download_controls.get(task_id)
        if control is None or self.repository.status(task_id) is not TaskStatus.PAUSED:
            return False
        control.pause_requested = False
        self.repository.transition(task_id, TaskStatus.DOWNLOADING)
        return True

    def cancel_task(self, task_id: UUID) -> bool:
        status = self.repository.status(task_id)
        if status in {TaskStatus.QUEUED, TaskStatus.INTERRUPTED}:
            self.repository.transition(task_id, TaskStatus.CANCELED)
            return True
        control = self._download_controls.get(task_id)
        if control is None or status not in {
            TaskStatus.RESOLVING,
            TaskStatus.PROBING,
            TaskStatus.DOWNLOADING,
            TaskStatus.PAUSED,
            TaskStatus.VERIFYING,
            TaskStatus.PROCESSING,
            TaskStatus.RETRY_WAIT,
        }:
            return False
        control.cancel_requested = True
        control.pause_requested = False
        self.repository.transition(task_id, TaskStatus.CANCELED)
        return True

    def cancel_all_tasks(self) -> int:
        active = {
            TaskStatus.QUEUED.value,
            TaskStatus.RESOLVING.value,
            TaskStatus.PROBING.value,
            TaskStatus.DOWNLOADING.value,
            TaskStatus.PAUSED.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.PROCESSING.value,
            TaskStatus.RETRY_WAIT.value,
            TaskStatus.INTERRUPTED.value,
        }
        changed = 0
        for task in self.repository.list_tasks():
            if task.status in active and self.cancel_task(UUID(task.task_id)):
                changed += 1
        return changed

    def clear_stopped_tasks(self) -> CleanupResult:
        terminal = {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELED.value,
            TaskStatus.INTERRUPTED.value,
        }
        failed = {
            TaskStatus.FAILED.value,
            TaskStatus.CANCELED.value,
            TaskStatus.INTERRUPTED.value,
        }
        tasks = self.repository.list_tasks()
        stopped = tuple(task for task in tasks if task.status in terminal)
        if not stopped:
            return CleanupResult(0, 0, 0)

        active_targets = {
            str(Path(task.target_path).resolve()) for task in tasks if task.status not in terminal
        }
        completed_targets = {
            str(Path(task.target_path).resolve())
            for task in tasks
            if task.status == TaskStatus.COMPLETED.value
        }
        discovered_roots = {
            root
            for task in stopped
            if (root := _download_storage_root(Path(task.target_path).resolve())) is not None
        }
        allowed_roots = tuple(
            dict.fromkeys(
                (
                    self.storage_root.resolve(),
                    self.paths.outputs.resolve(),
                    (self.paths.data / "jobs").resolve(),
                    *sorted(discovered_roots, key=str),
                )
            )
        )
        cleanup_paths: set[Path] = set()
        for task in stopped:
            if task.status not in failed:
                continue
            target = Path(task.target_path).resolve()
            if not _within_roots(target, allowed_roots):
                raise ExplorerError(
                    FailureCode.VALIDATION_FAILED,
                    f"拒绝清理存储目录外的任务文件: {target}",
                )
            target_key = str(target)
            if target_key in active_targets:
                continue
            cleanup_paths.update(
                {
                    target.with_suffix(target.suffix + ".part"),
                    target.with_suffix(target.suffix + ".part.json"),
                }
            )
            if target_key not in completed_targets:
                cleanup_paths.add(target)
                storage_root = _download_storage_root(target)
                converted = (
                    _converted_output_for(target, storage_root)
                    if storage_root is not None
                    else None
                )
                if converted is not None:
                    cleanup_paths.add(converted)

        removed_files = 0
        freed_bytes = 0
        for path in sorted(cleanup_paths, key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                files = tuple(item for item in path.rglob("*") if item.is_file())
                removed_files += len(files)
                freed_bytes += sum(item.stat().st_size for item in files)
                shutil.rmtree(path)
            elif path.exists():
                freed_bytes += path.stat().st_size
                path.unlink()
                removed_files += 1
            _prune_empty_parents(path.parent, allowed_roots)

        removed_tasks = self.repository.delete_tasks(
            tuple(UUID(task.task_id) for task in stopped)
        )
        return CleanupResult(removed_tasks, removed_files, freed_bytes)

    async def retry_task(self, task_id: UUID) -> Path:
        details = self.repository.task_details(task_id)
        if details.status not in {
            TaskStatus.FAILED.value,
            TaskStatus.INTERRUPTED.value,
        }:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "只有失败或意外中断的任务可以重新连接",
            )
        target = Path(details.target_path)
        scenario = target.parent.name
        model = target.parent.parent.name
        variable = target.name.split("_", 1)[0]
        candidates = self.repository.download_candidates(task_id)
        file = LogicalFile(
            logical_key=details.file_key,
            filename=target.name,
            source_id=model,
            experiment_id=scenario,
            variable_id=variable,
            frequency=target.name.split("_", 2)[1] if "_" in target.name else None,
            size_bytes=details.expected_size,
            replicas=(
                Replica(
                    data_node=urlsplit(details.source_url).hostname or "unknown",
                    backend_id="reconnect",
                    replica=False,
                    checksum=details.checksum,
                    checksum_type=details.checksum_type,
                    endpoints=tuple(
                        AccessEndpoint(
                            url=url,
                            service="HTTPServer",
                            secure=url.lower().startswith("https://"),
                        )
                        for url in candidates
                    ),
                ),
            ),
        )
        return await self.run_download_task(task_id, file)

    async def resume_interrupted_task(self, task_id: UUID) -> Path:
        details = self.repository.task_details(task_id)
        if details.status != TaskStatus.INTERRUPTED.value:
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "only interrupted tasks can be recovered",
            )
        if details.mode not in {
            DownloadMode.FULL_FILE.value,
            DownloadMode.DIRECT_FILE.value,
        } or (details.mode == DownloadMode.FULL_FILE.value and not details.confirmation_id):
            raise ExplorerError(
                FailureCode.VALIDATION_FAILED,
                "only confirmed full-file downloads support restart recovery",
            )
        control = DownloadControl()
        self._download_controls[task_id] = control
        self.repository.transition(task_id, TaskStatus.DOWNLOADING, {"recovered": True})
        target = Path(details.target_path)
        try:
            result = await self.downloader.download(
                details.source_url,
                target,
                expected_size=details.expected_size,
                expected_checksum=details.checksum,
                checksum_type=details.checksum_type or "SHA256",
                progress=lambda written, _total: self.repository.update_progress(task_id, written),
                control=control,
            )
        except DownloadCancelled:
            if self.repository.status(task_id) is not TaskStatus.CANCELED:
                self.repository.transition(task_id, TaskStatus.CANCELED)
            raise
        except Exception as exc:
            self.repository.transition(
                task_id, TaskStatus.FAILED, {"reason": str(exc), "recovered": True}
            )
            raise
        finally:
            self._download_controls.pop(task_id, None)
        self.repository.transition(task_id, TaskStatus.VERIFYING)
        self.repository.transition(task_id, TaskStatus.COMPLETED)
        return result.path

    def process(
        self,
        job: JobContext,
        inputs: list[Path],
        region: Region,
        output_dir: Path,
        options: ProcessingOptions,
        provenance: dict | None = None,
    ) -> ProcessingResult:
        result = process_to_geotiffs(
            inputs,
            region.geometry_wkb_hex,
            output_dir,
            options,
            provenance={"job_id": str(job.id), **(provenance or {})},
        )
        manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
        by_name = {item["path"]: item for item in manifest["artifacts"]}
        for artifact in result.artifacts:
            year_match = re.search(r"_(\d{4})_annual-", artifact.name)
            item = by_name[artifact.name]
            self.repository.record_artifact(
                job.id,
                str(artifact.resolve()),
                "cog" if options.output_format.upper() == "COG" else "geotiff",
                item["sha256"],
                item["size_bytes"],
                int(year_match.group(1)) if year_match else None,
            )
        self.repository.record_artifact(
            job.id,
            str(result.manifest.resolve()),
            "manifest",
            _sha256(result.manifest),
            result.manifest.stat().st_size,
        )
        return result

    async def close(self) -> None:
        self.request_shutdown()
        await self.downloader.close()

    def request_shutdown(self) -> None:
        self.shutdown_requested = True
        for task_id in self.repository.queued_task_ids():
            with suppress(Exception):
                self.repository.transition(task_id, TaskStatus.CANCELED)
        for control in list(self._download_controls.values()):
            control.cancel_requested = True
            control.pause_requested = False


def _within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _converted_output_for(target: Path, storage_root: Path) -> Path | None:
    try:
        relative = target.relative_to(storage_root)
    except ValueError:
        return None
    if len(relative.parts) < 2 or relative.parts[0].casefold() != "netcdf":
        return None
    return storage_root / "GeoTIFF" / Path(*relative.parts[1:-1]) / target.stem


def _download_storage_root(target: Path) -> Path | None:
    parts = target.parts
    index = next(
        (position for position, part in enumerate(parts) if part.casefold() == "netcdf"),
        None,
    )
    if index is None or index == 0 or len(parts) - index < 4:
        return None
    return Path(*parts[:index]).resolve()


def _prune_empty_parents(path: Path, roots: tuple[Path, ...]) -> None:
    current = path
    while current not in roots and _within_roots(current, roots):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _preferred_checksum(file: LogicalFile) -> tuple[str | None, str | None]:
    fallback: tuple[str | None, str | None] = (None, None)
    for replica in file.replicas:
        normalized = (replica.checksum_type or "").upper().replace("-", "")
        if replica.checksum and normalized == "SHA256":
            return replica.checksum, "SHA256"
        if replica.checksum and normalized == "MD5":
            fallback = (replica.checksum, "MD5")
    return fallback


def _http_download_candidates(file: LogicalFile, allow_insecure_http: bool) -> tuple[str, ...]:
    native_https: list[str] = []
    upgraded_https: list[str] = []
    insecure_http: list[str] = []
    checksum_available = any(replica.checksum for replica in file.replicas)
    for replica in file.replicas:
        for endpoint in replica.endpoints:
            if endpoint.service.upper() != "HTTPSERVER":
                continue
            parts = urlsplit(endpoint.url)
            if parts.scheme.lower() == "https":
                native_https.append(endpoint.url)
            elif parts.scheme.lower() == "http":
                upgraded_https.append(urlunsplit(("https", *parts[1:])))
                if allow_insecure_http or checksum_available:
                    insecure_http.append(endpoint.url)
    return tuple(dict.fromkeys((*native_https, *upgraded_https, *insecure_http)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_segment(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .")
    return cleaned or "未分类"


def _download_category(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix in {".nc", ".nc4", ".cdf"}:
        return "NetCDF"
    if suffix in {".tif", ".tiff"}:
        return "GeoTIFF"
    if suffix in {".csv", ".json", ".txt"}:
        return "Tables"
    return "Other"


def _error_text(error: Exception) -> str:
    message = str(error).strip()
    if message:
        return message
    if isinstance(error, httpx.ConnectError):
        return "无法连接下载节点"
    if isinstance(error, httpx.TimeoutException):
        return "连接下载节点超时"
    return type(error).__name__


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 425, 429} or error.response.status_code >= 500
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(error, OSError):
        text = str(error).casefold()
        return any(
            marker in text
            for marker in (
                "connection",
                "peer closed",
                "size mismatch",
                "timed out",
                "temporarily",
            )
        )
    return False


def _should_auto_convert(file: LogicalFile) -> bool:
    frequency = (file.frequency or "").casefold()
    table = (file.table_id or "").casefold()
    return frequency in {"mon", "month", "monthly", "yr", "year", "annual", "fx"} or (
        table.endswith("mon") or table.endswith("yr") or table.endswith("fx")
    )
