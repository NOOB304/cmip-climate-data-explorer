from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from cmip_explorer.domain.enums import DownloadMode, FailureCode, TaskStatus
from cmip_explorer.domain.errors import FullDownloadConfirmationRequired
from cmip_explorer.domain.models import DownloadTask, Region, UserConfirmation
from cmip_explorer.domain.state_machine import assert_transition

from .database import Database
from .tables import ArtifactRow, ConfirmationRow, JobRow, RegionRow, TaskEventRow, TaskRow


class TaskSummary:
    def __init__(
        self,
        task_id: str,
        file_key: str,
        mode: str,
        status: str,
        progress_bytes: int,
        expected_size: int | None,
        target_path: str,
    ) -> None:
        self.task_id = task_id
        self.file_key = file_key
        self.mode = mode
        self.status = status
        self.progress_bytes = progress_bytes
        self.expected_size = expected_size
        self.target_path = target_path


class TaskDetails(TaskSummary):
    def __init__(
        self,
        task_id: str,
        file_key: str,
        mode: str,
        status: str,
        progress_bytes: int,
        expected_size: int | None,
        target_path: str,
        source_url: str,
        checksum: str | None,
        checksum_type: str | None,
        confirmation_id: str | None,
    ) -> None:
        super().__init__(
            task_id,
            file_key,
            mode,
            status,
            progress_bytes,
            expected_size,
            target_path,
        )
        self.source_url = source_url
        self.checksum = checksum
        self.checksum_type = checksum_type
        self.confirmation_id = confirmation_id


class TaskRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_job(self, job_id: UUID, name: str, plan_hash: str) -> None:
        with self.database.session() as session:
            session.add(
                JobRow(
                    id=str(job_id),
                    name=name,
                    plan_hash=plan_hash,
                    created_at=datetime.now(UTC),
                )
            )

    def record_confirmation(self, confirmation: UserConfirmation) -> None:
        with self.database.session() as session:
            session.add(
                ConfirmationRow(
                    id=str(confirmation.id),
                    job_id=str(confirmation.job_id),
                    scope=confirmation.scope.value,
                    target_key=confirmation.target_key,
                    failure_code=confirmation.failure_code.value,
                    estimated_bytes=confirmation.estimated_bytes,
                    failure_snapshot_json=json.dumps(
                        confirmation.failure_snapshot, ensure_ascii=False, sort_keys=True
                    ),
                    plan_hash=confirmation.plan_hash,
                    confirmed_at=confirmation.confirmed_at,
                )
            )

    def create_task(self, task: DownloadTask) -> None:
        if task.mode is DownloadMode.FULL_FILE and not self._confirmation_matches(task):
            raise FullDownloadConfirmationRequired(
                code=task.failure_code or FailureCode.DOWNLOAD_NOT_CONFIRMED,
                message="full file download lacks a matching persisted confirmation",
                details={"task_id": str(task.id), "file_key": task.file_key},
            )
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = TaskRow(
                id=str(task.id),
                job_id=str(task.job_id),
                file_key=task.file_key,
                mode=task.mode.value,
                status=task.status.value,
                source_url=task.source_url,
                target_path=task.target_path,
                expected_size=task.expected_size,
                progress_bytes=task.progress_bytes,
                checksum=task.checksum,
                checksum_type=task.checksum_type,
                confirmation_id=str(task.confirmation_id) if task.confirmation_id else None,
                failure_code=task.failure_code.value if task.failure_code else None,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            session.add(
                TaskEventRow(
                    task_id=str(task.id),
                    event_type="created",
                    payload_json="{}",
                    created_at=now,
                )
            )

    def _confirmation_matches(self, task: DownloadTask) -> bool:
        if task.confirmation_id is None:
            return False
        with self.database.session() as session:
            confirmation = session.get(ConfirmationRow, str(task.confirmation_id))
            if confirmation is None or confirmation.job_id != str(task.job_id):
                return False
            if confirmation.scope == "file":
                return confirmation.target_key == task.file_key
            return True

    def transition(self, task_id: UUID, target: TaskStatus, payload: dict | None = None) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            task = session.get(TaskRow, str(task_id))
            if task is None:
                raise KeyError(task_id)
            current = TaskStatus(task.status)
            assert_transition(current, target)
            task.status = target.value
            task.updated_at = now
            task.version += 1
            session.add(
                TaskEventRow(
                    task_id=str(task_id),
                    event_type="state_changed",
                    payload_json=json.dumps(
                        {"from": current.value, "to": target.value, **(payload or {})},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    created_at=now,
                )
            )

    def mark_running_tasks_interrupted(self) -> int:
        running = {
            TaskStatus.RESOLVING.value,
            TaskStatus.PROBING.value,
            TaskStatus.DOWNLOADING.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.PROCESSING.value,
        }
        with self.database.session() as session:
            rows = session.scalars(select(TaskRow).where(TaskRow.status.in_(running))).all()
            now = datetime.now(UTC)
            for task in rows:
                task.status = TaskStatus.INTERRUPTED.value
                task.updated_at = now
                task.version += 1
                session.add(
                    TaskEventRow(
                        task_id=task.id,
                        event_type="interrupted_on_startup",
                        payload_json="{}",
                        created_at=now,
                    )
                )
            return len(rows)

    def status(self, task_id: UUID) -> TaskStatus:
        with self.database.session() as session:
            status = session.scalar(select(TaskRow.status).where(TaskRow.id == str(task_id)))
            if status is None:
                raise KeyError(task_id)
            return TaskStatus(status)

    def active_task_id(self, file_key: str) -> UUID | None:
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
        with self.database.session() as session:
            task_id = session.scalar(
                select(TaskRow.id)
                .where(TaskRow.file_key == file_key, TaskRow.status.in_(active))
                .order_by(TaskRow.updated_at.desc())
                .limit(1)
            )
            return UUID(task_id) if task_id else None

    def queued_task_ids(self) -> tuple[UUID, ...]:
        with self.database.session() as session:
            values = session.scalars(
                select(TaskRow.id).where(TaskRow.status == TaskStatus.QUEUED.value)
            ).all()
            return tuple(UUID(value) for value in values)

    def list_tasks(self, job_id: UUID | None = None) -> tuple[TaskSummary, ...]:
        with self.database.session() as session:
            statement = select(TaskRow).order_by(TaskRow.updated_at.desc())
            if job_id is not None:
                statement = statement.where(TaskRow.job_id == str(job_id))
            rows = session.scalars(statement).all()
            return tuple(
                TaskSummary(
                    task_id=row.id,
                    file_key=row.file_key,
                    mode=row.mode,
                    status=row.status,
                    progress_bytes=row.progress_bytes,
                    expected_size=row.expected_size,
                    target_path=row.target_path,
                )
                for row in rows
            )

    def delete_tasks(self, task_ids: tuple[UUID, ...]) -> int:
        if not task_ids:
            return 0
        values = tuple(str(task_id) for task_id in task_ids)
        with self.database.session() as session:
            session.execute(delete(TaskEventRow).where(TaskEventRow.task_id.in_(values)))
            result = session.execute(delete(TaskRow).where(TaskRow.id.in_(values)))
            return int(result.rowcount or 0)

    def task_details(self, task_id: UUID) -> TaskDetails:
        with self.database.session() as session:
            row = session.get(TaskRow, str(task_id))
            if row is None:
                raise KeyError(task_id)
            return TaskDetails(
                task_id=row.id,
                file_key=row.file_key,
                mode=row.mode,
                status=row.status,
                progress_bytes=row.progress_bytes,
                expected_size=row.expected_size,
                target_path=row.target_path,
                source_url=row.source_url,
                checksum=row.checksum,
                checksum_type=row.checksum_type,
                confirmation_id=row.confirmation_id,
            )

    def update_progress(self, task_id: UUID, progress_bytes: int) -> None:
        with self.database.session() as session:
            task = session.get(TaskRow, str(task_id))
            if task is None:
                raise KeyError(task_id)
            task.progress_bytes = progress_bytes
            task.updated_at = datetime.now(UTC)
            task.version += 1

    def update_source_url(self, task_id: UUID, source_url: str) -> None:
        with self.database.session() as session:
            task = session.get(TaskRow, str(task_id))
            if task is None:
                raise KeyError(task_id)
            task.source_url = source_url
            task.updated_at = datetime.now(UTC)
            task.version += 1

    def record_event(self, task_id: UUID, event_type: str, payload: dict | None = None) -> None:
        with self.database.session() as session:
            if session.get(TaskRow, str(task_id)) is None:
                raise KeyError(task_id)
            session.add(
                TaskEventRow(
                    task_id=str(task_id),
                    event_type=event_type,
                    payload_json=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    created_at=datetime.now(UTC),
                )
            )

    def save_region(self, region: Region) -> None:
        with self.database.session() as session:
            row = session.get(RegionRow, str(region.id)) or RegionRow(id=str(region.id))
            row.name = region.name
            row.source_path = region.source_path
            row.source_sha256 = region.source_sha256
            row.source_crs = region.source_crs
            row.normalized_crs = region.normalized_crs
            row.geometry_wkb = bytes.fromhex(region.geometry_wkb_hex)
            row.bbox_json = json.dumps(region.bbox)
            row.repaired = region.repaired
            row.selected_feature_ids_json = json.dumps(region.selected_feature_ids)
            row.created_at = datetime.now(UTC)
            session.add(row)

    def list_regions(self) -> tuple[Region, ...]:
        with self.database.session() as session:
            rows = session.scalars(select(RegionRow).order_by(RegionRow.created_at.desc())).all()
            return tuple(
                Region(
                    id=UUID(row.id),
                    name=row.name,
                    source_path=row.source_path,
                    source_sha256=row.source_sha256,
                    source_crs=row.source_crs,
                    normalized_crs=row.normalized_crs,
                    geometry_wkb_hex=row.geometry_wkb.hex(),
                    bbox=tuple(json.loads(row.bbox_json)),
                    repaired=row.repaired,
                    selected_feature_ids=tuple(json.loads(row.selected_feature_ids_json)),
                )
                for row in rows
            )

    def record_artifact(
        self,
        job_id: UUID,
        path: str,
        kind: str,
        sha256: str,
        size_bytes: int,
        year: int | None = None,
    ) -> None:
        with self.database.session() as session:
            session.add(
                ArtifactRow(
                    id=str(uuid4()),
                    job_id=str(job_id),
                    path=path,
                    kind=kind,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    year=year,
                )
            )
