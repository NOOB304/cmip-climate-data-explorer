from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect

from cmip_explorer.domain.enums import ConfirmationScope, DownloadMode, FailureCode, TaskStatus
from cmip_explorer.domain.models import DownloadTask, Region, UserConfirmation
from cmip_explorer.infrastructure.persistence import Database, TaskRepository


def test_confirmation_and_task_are_persisted(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    repository = TaskRepository(database)
    assert "alembic_version" in inspect(database.engine).get_table_names()
    job_id = uuid4()
    repository.create_job(job_id, "test", "a" * 64)
    confirmation = UserConfirmation(
        job_id=job_id,
        scope=ConfirmationScope.FILE,
        target_key="file-key",
        failure_code=FailureCode.REMOTE_SUBSET_UNAVAILABLE,
        estimated_bytes=100,
        failure_snapshot={"reason": "test"},
        plan_hash="a" * 64,
    )
    repository.record_confirmation(confirmation)
    task = DownloadTask(
        job_id=job_id,
        file_key="file-key",
        mode=DownloadMode.FULL_FILE,
        source_url="https://example.test/file.nc",
        target_path=str(tmp_path / "file.nc"),
        confirmation_id=confirmation.id,
    )
    repository.create_task(task)
    repository.transition(task.id, TaskStatus.RESOLVING)
    assert repository.status(task.id) is TaskStatus.RESOLVING
    database.dispose()


def test_running_tasks_become_interrupted(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    repository = TaskRepository(database)
    job_id = uuid4()
    repository.create_job(job_id, "test", "b" * 64)
    task = DownloadTask(
        job_id=job_id,
        file_key="file-key",
        mode=DownloadMode.REMOTE_SUBSET,
        source_url="https://example.test/dap",
        target_path=str(tmp_path / "subset.zarr"),
    )
    repository.create_task(task)
    repository.transition(task.id, TaskStatus.RESOLVING)
    repository.transition(task.id, TaskStatus.PROBING)
    repository.transition(task.id, TaskStatus.DOWNLOADING)
    assert repository.mark_running_tasks_interrupted() == 1
    assert repository.status(task.id) is TaskStatus.INTERRUPTED
    database.dispose()


def test_region_selection_round_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    repository = TaskRepository(database)
    region = Region(
        name="Guizhou",
        source_path="guizhou.geojson",
        source_sha256="c" * 64,
        source_crs="EPSG:4326",
        geometry_wkb_hex=(
            "010300000001000000050000000000000000005A400000000000003940"
            "0000000000805A4000000000000039400000000000805A400000000000"
            "8039400000000000005A4000000000008039400000000000005A400000"
            "000000003940"
        ),
        bbox=(104.0, 25.0, 106.0, 25.5),
        selected_feature_ids=("17", "23"),
    )
    repository.save_region(region)
    loaded = repository.list_regions()[0]
    assert loaded.selected_feature_ids == ("17", "23")
    database.dispose()
