import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import xarray as xr

from cmip_explorer.application import WorkflowService
from cmip_explorer.application.workflow import _should_auto_convert
from cmip_explorer.config import AppPaths
from cmip_explorer.domain.enums import TaskStatus
from cmip_explorer.domain.models import AccessEndpoint, LogicalFile, Replica
from cmip_explorer.infrastructure.download import DownloadCancelled, HttpRangeDownloader
from cmip_explorer.infrastructure.persistence import Database, TaskRepository


async def test_direct_download_reports_progress_and_completes(tmp_path: Path) -> None:
    payload = b"netcdf-test-payload"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        return httpx.Response(200, content=payload)

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
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    workflow = WorkflowService(
        paths,
        repository,
        downloader=HttpRangeDownloader(client=client, chunk_size=4, reconnect_delays=()),
        storage_root=tmp_path / "climate-data",
        auto_convert_to_tif=False,
    )
    file = LogicalFile(
        logical_key="test-file",
        filename="tas_Amon_TestModel_ssp245_r1i1p1f1_gn_202001-202012.nc",
        source_id="TestModel",
        experiment_id="ssp245",
        variable_id="tas",
        frequency="mon",
        size_bytes=len(payload),
        replicas=(
            Replica(
                data_node="example.test",
                backend_id="test",
                replica=False,
                endpoints=(
                    AccessEndpoint(
                        url="https://example.test/file.nc",
                        service="HTTPServer",
                        secure=True,
                    ),
                ),
            ),
        ),
    )
    try:
        job = workflow.create_job("test", {"file": file.logical_key})
        result = await workflow.download_file(job, file)
        task = repository.list_tasks()[0]
        assert result.read_bytes() == payload
        assert task.progress_bytes == len(payload)
        assert task.status == TaskStatus.COMPLETED.value
        assert result.parent == tmp_path / "climate-data" / "NetCDF" / "TestModel" / "ssp245"
    finally:
        await client.aclose()
        database.dispose()


async def test_direct_download_automatically_converts_each_timestep(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    xr.Dataset(
        {
            "tas": (
                ("time", "lat", "lon"),
                np.arange(8, dtype="float32").reshape(2, 2, 2),
            )
        },
        coords={
            "time": np.array(["2020-01-15", "2020-02-15"], dtype="datetime64[ns]"),
            "lat": [1.0, 0.0],
            "lon": [100.0, 101.0],
        },
    ).to_netcdf(source)
    payload = source.read_bytes()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        return httpx.Response(200, content=payload)

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
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    storage = tmp_path / "climate-data"
    workflow = WorkflowService(
        paths,
        repository,
        downloader=HttpRangeDownloader(client=client),
        storage_root=storage,
        auto_convert_to_tif=True,
    )
    file = LogicalFile(
        logical_key="tas-monthly",
        filename="tas_Amon_TestModel_ssp245_202001-202002.nc",
        source_id="TestModel",
        experiment_id="ssp245",
        variable_id="tas",
        frequency="mon",
        size_bytes=len(payload),
        replicas=(
            Replica(
                data_node="example.test",
                backend_id="test",
                replica=False,
                endpoints=(
                    AccessEndpoint(
                        url="https://example.test/tas.nc",
                        service="HTTPServer",
                        secure=True,
                    ),
                ),
            ),
        ),
    )
    try:
        job = workflow.create_job("auto conversion", {"file": file.logical_key})
        await workflow.download_file(job, file)
        outputs = sorted((storage / "GeoTIFF").rglob("*.tif"))
        assert len(outputs) == 2
        assert repository.list_tasks()[0].status == TaskStatus.COMPLETED.value
    finally:
        await client.aclose()
        database.dispose()


async def test_network_failure_reconnects_inside_the_same_task(tmp_path: Path) -> None:
    payload = b"reconnected-download"
    get_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        get_count += 1
        if get_count == 1:
            raise httpx.ReadError("simulated connection drop", request=request)
        return httpx.Response(200, content=payload)

    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    workflow = WorkflowService(
        paths,
        repository,
        downloader=HttpRangeDownloader(client=client, chunk_size=4, reconnect_delays=()),
        storage_root=tmp_path / "climate-data",
        auto_convert_to_tif=False,
        reconnect_delays=(0.0,),
    )
    file = _test_file(len(payload))
    try:
        job = workflow.create_job("reconnect", {"file": file.logical_key})
        task_id, created = workflow.enqueue_download(job, file)
        result = await workflow.run_download_task(task_id, file)
        assert created is True
        assert result.read_bytes() == payload
        assert get_count == 2
        assert len(repository.list_tasks()) == 1
        assert repository.list_tasks()[0].task_id == str(task_id)
        assert repository.list_tasks()[0].status == TaskStatus.COMPLETED.value
    finally:
        await client.aclose()
        database.dispose()


def test_duplicate_enqueue_and_cancel_keep_one_stable_task(tmp_path: Path) -> None:
    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(
        paths,
        repository,
        storage_root=tmp_path / "climate-data",
        auto_convert_to_tif=False,
    )
    file = _test_file(100)
    try:
        job = workflow.create_job("stable queue", {"file": file.logical_key})
        first_id, first_created = workflow.enqueue_download(job, file)
        second_id, second_created = workflow.enqueue_download(job, file)
        assert first_created is True
        assert second_created is False
        assert first_id == second_id
        assert repository.download_candidates(first_id) == (
            "https://example.test/file.nc",
        )
        assert len(repository.list_tasks()) == 1
        assert workflow.cancel_task(first_id) is True
        assert len(repository.list_tasks()) == 1
        assert repository.list_tasks()[0].status == TaskStatus.CANCELED.value
    finally:
        database.dispose()


def test_default_workflow_uses_one_visible_retry_layer(tmp_path: Path) -> None:
    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(paths, repository)
    try:
        assert workflow.downloader.reconnect_delays == ()
        assert workflow.reconnect_delays == (2.0, 5.0, 10.0, 20.0, 30.0)
        assert workflow.download_concurrency == 2
    finally:
        database.dispose()


async def test_workflow_ranks_faster_mirror_first_and_caches_probe(
    tmp_path: Path,
) -> None:
    class ProbeDownloader:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def probe_speed(self, url: str) -> float:
            self.calls.append(url)
            return 10_000.0 if "fast" in url else 100.0

    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    downloader = ProbeDownloader()
    workflow = WorkflowService(
        paths,
        repository,
        downloader=downloader,  # type: ignore[arg-type]
        storage_root=tmp_path / "climate-data",
    )
    file = _test_file(100)
    job = workflow.create_job("mirror ranking", {"file": file.logical_key})
    task_id, _created = workflow.enqueue_download(job, file)
    candidates = (
        "https://slow.example.test/data.nc",
        "https://fast.example.test/data.nc",
    )
    try:
        first = await workflow._rank_download_candidates(task_id, candidates)
        second = await workflow._rank_download_candidates(task_id, candidates)
        assert first == second == (candidates[1], candidates[0])
        assert len(downloader.calls) == 2
    finally:
        database.dispose()


def test_retry_wait_summary_exposes_attempt_and_resume_time(tmp_path: Path) -> None:
    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(paths, repository)
    try:
        job = workflow.create_job("retry status", {})
        task_id, _ = workflow.enqueue_download(job, _test_file(100))
        repository.transition(task_id, TaskStatus.RESOLVING)
        repository.transition(task_id, TaskStatus.PROBING)
        repository.transition(task_id, TaskStatus.DOWNLOADING)
        retry_at = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
        repository.transition(
            task_id,
            TaskStatus.RETRY_WAIT,
            {
                "reconnect_attempt": 2,
                "retry_maximum": 5,
                "retry_at": retry_at,
            },
        )
        task = repository.list_tasks()[0]
        assert task.retry_attempt == 2
        assert task.retry_maximum == 5
        assert task.retry_at == retry_at
    finally:
        database.dispose()


def test_cancel_all_stops_every_queued_item_without_new_records(tmp_path: Path) -> None:
    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(paths, repository, storage_root=tmp_path / "climate-data")
    first = _test_file(100)
    second = first.model_copy(
        update={"logical_key": "second-queued", "filename": "second.nc"}
    )
    try:
        job = workflow.create_job("cancel all", {"files": [first.logical_key, second.logical_key]})
        workflow.enqueue_download(job, first)
        workflow.enqueue_download(job, second)
        assert workflow.cancel_all_tasks() == 2
        tasks = repository.list_tasks()
        assert len(tasks) == 2
        assert {task.status for task in tasks} == {TaskStatus.CANCELED.value}
    finally:
        database.dispose()


async def test_shutdown_stops_active_download_and_cancels_queued_items(
    tmp_path: Path,
) -> None:
    payload = b"shutdown-safe"
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        started.set()
        await release.wait()
        return httpx.Response(200, content=payload)

    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    workflow = WorkflowService(
        paths,
        repository,
        downloader=HttpRangeDownloader(client=client, chunk_size=4),
        storage_root=tmp_path / "climate-data",
        auto_convert_to_tif=False,
        reconnect_delays=(0.0,),
    )
    first = _test_file(len(payload))
    second = first.model_copy(
        update={
            "logical_key": "second-file",
            "filename": "tas_Amon_TestModel_ssp245_202101-202112.nc",
        }
    )
    try:
        job = workflow.create_job("shutdown", {"files": [first.logical_key, second.logical_key]})
        first_id, _ = workflow.enqueue_download(job, first)
        second_id, _ = workflow.enqueue_download(job, second)
        running = asyncio.create_task(workflow.run_download_task(first_id, first))
        await started.wait()
        workflow.request_shutdown()
        release.set()
        with suppress(DownloadCancelled):
            await running
        assert repository.status(first_id) is TaskStatus.INTERRUPTED
        assert repository.status(second_id) is TaskStatus.CANCELED
        assert len(repository.list_tasks()) == 2
    finally:
        await client.aclose()
        database.dispose()


def _test_paths(tmp_path: Path) -> AppPaths:
    return AppPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        logs=tmp_path / "logs",
        outputs=tmp_path / "outputs",
        database=tmp_path / "data" / "app.db",
        catalog=tmp_path / "data" / "catalog.db",
    )


def _test_file(size: int) -> LogicalFile:
    return LogicalFile(
        logical_key="stable-test-file",
        filename="tas_Amon_TestModel_ssp245_202001-202012.nc",
        source_id="TestModel",
        experiment_id="ssp245",
        variable_id="tas",
        size_bytes=size,
        replicas=(
            Replica(
                data_node="example.test",
                backend_id="test",
                replica=False,
                endpoints=(
                    AccessEndpoint(
                        url="https://example.test/file.nc",
                        service="HTTPServer",
                        secure=True,
                    ),
                ),
            ),
        ),
    )


def test_automatic_conversion_is_limited_to_manageable_frequencies() -> None:
    monthly = LogicalFile(
        logical_key="monthly", filename="tas_Amon_model.nc", frequency="mon"
    )
    three_hourly = LogicalFile(
        logical_key="3hr", filename="pr_3hr_model.nc", frequency="3hr"
    )
    assert _should_auto_convert(monthly) is True
    assert _should_auto_convert(three_hourly) is False


async def test_retry_of_completed_high_frequency_nc_does_not_download_again(
    tmp_path: Path,
) -> None:
    paths = _test_paths(tmp_path)
    paths.ensure()
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(
        paths,
        repository,
        storage_root=tmp_path / "climate-data",
        auto_convert_to_tif=True,
        reconnect_delays=(0.0,),
    )
    file = _test_file(12).model_copy(
        update={"frequency": "3hr", "filename": "tas_3hr_TestModel_ssp245.nc"}
    )
    try:
        job = workflow.create_job("existing nc", {"file": file.logical_key})
        task_id, _ = workflow.enqueue_download(job, file)
        details = repository.task_details(task_id)
        target = Path(details.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"already-done")
        repository.transition(task_id, TaskStatus.RESOLVING)
        repository.transition(task_id, TaskStatus.PROBING)
        repository.transition(task_id, TaskStatus.DOWNLOADING)
        repository.transition(task_id, TaskStatus.FAILED, {"phase": "conversion"})
        result = await workflow.retry_task(task_id)
        assert result == target
        assert result.read_bytes() == b"already-done"
        assert repository.status(task_id) is TaskStatus.COMPLETED
    finally:
        database.dispose()
