from pathlib import Path
from uuid import uuid4

from cmip_explorer.application import WorkflowService
from cmip_explorer.config import AppPaths
from cmip_explorer.domain.enums import DownloadMode, TaskStatus
from cmip_explorer.domain.models import DownloadTask
from cmip_explorer.infrastructure.persistence import Database, TaskRepository


def test_clear_stopped_tasks_removes_residue_but_preserves_completed_and_active_files(
    tmp_path: Path,
) -> None:
    paths = AppPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        logs=tmp_path / "logs",
        outputs=tmp_path / "outputs",
        database=tmp_path / "data" / "app.db",
        catalog=tmp_path / "data" / "catalog.db",
    )
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    storage = tmp_path / "old-climate-data"
    workflow = WorkflowService(paths, repository, storage_root=tmp_path / "current-climate-data")
    job_id = uuid4()
    repository.create_job(job_id, "cleanup", "d" * 64)

    def add(name: str, status: TaskStatus, target: Path) -> DownloadTask:
        task = DownloadTask(
            job_id=job_id,
            file_key=name,
            mode=DownloadMode.DIRECT_FILE,
            status=status,
            source_url=f"https://example.test/{name}.nc",
            target_path=str(target),
            expected_size=10,
        )
        repository.create_task(task)
        return task

    folder = storage / "NetCDF" / "Model" / "ssp245"
    folder.mkdir(parents=True)
    completed = folder / "completed.nc"
    completed.write_bytes(b"complete")
    completed_task = add("completed", TaskStatus.COMPLETED, completed)

    # A stale failed record may point to a file that another completed record owns.
    duplicate_failed = add("duplicate-failed", TaskStatus.FAILED, completed)
    completed_part = completed.with_suffix(".nc.part")
    completed_part.write_bytes(b"stale")

    failed = folder / "failed.nc"
    failed.write_bytes(b"bad-target")
    failed_part = failed.with_suffix(".nc.part")
    failed_part.write_bytes(b"partial")
    failed_sidecar = failed.with_suffix(".nc.part.json")
    failed_sidecar.write_text("{}", encoding="utf-8")
    failed_task = add("failed", TaskStatus.FAILED, failed)
    converted = storage / "GeoTIFF" / "Model" / "ssp245" / "failed"
    converted.mkdir(parents=True)
    (converted / "failed_2020.tif").write_bytes(b"partial-tif")

    canceled = folder / "canceled.nc"
    canceled_part = canceled.with_suffix(".nc.part")
    canceled_part.write_bytes(b"partial-canceled")
    canceled_task = add("canceled", TaskStatus.CANCELED, canceled)

    interrupted = folder / "interrupted.nc"
    interrupted.write_bytes(b"interrupted")
    interrupted_task = add("interrupted", TaskStatus.INTERRUPTED, interrupted)

    active = folder / "active.nc"
    active_part = active.with_suffix(".nc.part")
    active_part.write_bytes(b"still-downloading")
    active_task = add("active", TaskStatus.DOWNLOADING, active)

    result = workflow.clear_stopped_tasks()

    assert result.removed_tasks == 5
    assert result.removed_files == 7
    assert completed.exists()
    assert not completed_part.exists()
    assert active_part.exists()
    assert not failed.exists()
    assert not failed_part.exists()
    assert not failed_sidecar.exists()
    assert not converted.exists()
    assert not canceled_part.exists()
    assert not interrupted.exists()
    assert repository.list_tasks()[0].task_id == str(active_task.id)
    for removed in (
        completed_task,
        duplicate_failed,
        failed_task,
        canceled_task,
        interrupted_task,
    ):
        try:
            repository.task_details(removed.id)
        except KeyError:
            pass
        else:
            raise AssertionError(f"task {removed.id} was not deleted")
    database.dispose()
