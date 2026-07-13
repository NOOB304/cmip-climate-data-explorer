from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QProgressBar

from cmip_explorer.application import WorkflowService
from cmip_explorer.config import AppPaths
from cmip_explorer.domain.enums import DownloadMode
from cmip_explorer.domain.models import (
    AccessEndpoint,
    DownloadTask,
    LogicalFile,
    Replica,
    TemporalCoverage,
)
from cmip_explorer.domain.models import SearchPage as ResultPage
from cmip_explorer.infrastructure.catalog import VariableOption, install_packaged_catalog
from cmip_explorer.infrastructure.persistence import Database, TaskRepository
from cmip_explorer.infrastructure.providers import PROVIDERS, ProviderVariable
from cmip_explorer.ui import MainWindow
from cmip_explorer.ui.pages.search import SearchPage as UISearchPage


def test_main_window_contains_complete_workbench_navigation(qtbot, tmp_path: Path) -> None:
    paths = AppPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        logs=tmp_path / "logs",
        outputs=tmp_path / "outputs",
        database=tmp_path / "data" / "app.db",
        catalog=tmp_path / "data" / "catalog.db",
    )
    paths.ensure()
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(paths, repository)
    window = MainWindow(paths, repository, workflow)
    qtbot.addWidget(window)
    window.show()
    assert window.navigation.count() == 6
    assert window.navigation.item(0).text() == "数据下载"
    assert window.navigation.item(1).text() == "下载任务"
    assert window.navigation.item(2).text() == "文件处理"
    assert all(
        window.navigation.item(index).text() not in {"研究区", "处理计划"}
        for index in range(window.navigation.count())
    )
    settings_page = window.stack.widget(5)
    assert settings_page.update_button.text() == "检查更新"
    assert settings_page.version_label.text() == "当前版本 0.3.1"
    assert window.minimumWidth() >= 1180
    database.dispose()


def test_selecting_a_result_row_does_not_silently_queue_its_checkbox(qtbot, tmp_path: Path) -> None:
    paths = AppPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        logs=tmp_path / "logs",
        outputs=tmp_path / "outputs",
        database=tmp_path / "data" / "app.db",
        catalog=tmp_path / "data" / "catalog.db",
    )
    paths.ensure()
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    window.show()
    search = window.stack.widget(0)
    search._show_results(ResultPage(files=(LogicalFile(logical_key="one", filename="one.nc"),)))
    search.table_widget.selectRow(0)
    assert search.table_widget.item(0, 0).checkState() is Qt.CheckState.Unchecked
    assert search.selected_count.text() == "已选 0 个文件"
    database.dispose()


def test_selecting_one_series_queues_every_physical_file(
    qtbot, tmp_path: Path, monkeypatch
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
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(paths, repository)
    window = MainWindow(paths, repository, workflow)
    qtbot.addWidget(window)
    search = window.stack.widget(0)

    def physical(year: int) -> LogicalFile:
        return LogicalFile(
            logical_key=f"pr-model-ssp245-{year}",
            filename=f"pr_day_model_ssp245_{year}.nc",
            source_id="model",
            experiment_id="ssp245",
            variable_id="pr",
            temporal=TemporalCoverage(
                start=f"{year}-01-01", end=f"{year}-12-31", source="stac"
            ),
            replicas=(
                Replica(
                    data_node="example.test",
                    backend_id="test",
                    replica=False,
                    endpoints=(
                        AccessEndpoint(
                            url=f"https://example.test/pr-{year}.nc",
                            service="HTTPServer",
                        ),
                    ),
                ),
            ),
        )

    members = (physical(2020), physical(2021))
    series = members[0].model_copy(
        update={
            "logical_key": "series-pr-model-ssp245",
            "filename": "pr_model_ssp245_2020-2021.series",
            "replicas": (),
            "series_members": members,
            "temporal": TemporalCoverage(
                start="2020-01-01", end="2021-12-31", source="stac"
            ),
        }
    )
    search._show_results(ResultPage(files=(series,)))
    search.table_widget.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert search.selected_count.text().startswith("已选 1 个系列")
    assert "2 个文件" in search.selected_count.text()
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(search, "_start_worker", lambda *_args: None)

    search._download_selected()

    tasks = repository.list_tasks()
    assert len(tasks) == 2
    assert {Path(task.target_path).name for task in tasks} == {
        "pr_day_model_ssp245_2020.nc",
        "pr_day_model_ssp245_2021.nc",
    }
    database.dispose()


def test_main_window_renders_multi_gibibyte_download_progress(qtbot, tmp_path: Path) -> None:
    paths = AppPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        logs=tmp_path / "logs",
        outputs=tmp_path / "outputs",
        database=tmp_path / "data" / "app.db",
        catalog=tmp_path / "data" / "catalog.db",
    )
    paths.ensure()
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    job_id = uuid4()
    repository.create_job(job_id, "large download", "c" * 64)
    repository.create_task(
        DownloadTask(
            job_id=job_id,
            file_key="five-gib-file",
            mode=DownloadMode.REMOTE_SUBSET,
            source_url="https://example.test/five-gib-file.nc",
            target_path=str(paths.outputs / "five-gib-file.nc"),
            expected_size=5 * 1024**3,
            progress_bytes=3 * 1024**3,
        )
    )

    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    task_page = window.stack.widget(1)
    progress = task_page.table.cellWidget(0, 1)

    assert isinstance(progress, QProgressBar)
    assert progress.maximum() == 1000
    assert progress.value() == 600
    assert "3.0 GiB / 5.0 GiB" in progress.format()
    database.dispose()


def test_variable_picker_groups_tables_and_does_not_force_a_frequency(
    qtbot, tmp_path: Path
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
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    search = window.stack.widget(0)
    search.variable_search.setText("pr")
    search._update_variable_matches()
    search._facet_timer.stop()

    options = [
        search.variable_match.itemData(index)
        for index in range(1, search.variable_match.count())
    ]
    precipitation = [
        option
        for option in options
        if isinstance(option, VariableOption) and option.variable_id == "pr"
    ]
    assert len(precipitation) == 1
    index = next(
        index
        for index in range(1, search.variable_match.count())
        if search.variable_match.itemData(index) == precipitation[0]
    )
    search.variable_match.setCurrentIndex(index)
    search._facet_timer.stop()

    request = search._request()
    assert request is not None
    facets = {constraint.name: constraint.values for constraint in request.facets}
    assert facets == {"variable_id": ("pr",)}
    assert "数据表" in search.columns
    database.dispose()


def test_data_source_tabs_switch_provider_specific_controls(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(UISearchPage, "_load_provider_variables", lambda _self: None)
    paths = AppPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        logs=tmp_path / "logs",
        outputs=tmp_path / "outputs",
        database=tmp_path / "data" / "app.db",
        catalog=tmp_path / "data" / "catalog.db",
    )
    paths.ensure()
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    window.show()
    search = window.stack.widget(0)

    assert search.provider_tabs.count() == len(PROVIDERS)
    power_index = next(
        index
        for index in range(search.provider_tabs.count())
        if search.provider_tabs.tabData(index) == "power"
    )
    search.provider_tabs.setCurrentIndex(power_index)

    assert search.product.isVisibleTo(search)
    assert search.latitude.isVisibleTo(search)
    assert search.longitude.isVisibleTo(search)
    assert not search.source.isVisibleTo(search)
    database.dispose()


def test_provider_request_uses_selected_product_and_location(qtbot, tmp_path: Path) -> None:
    paths = AppPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        logs=tmp_path / "logs",
        outputs=tmp_path / "outputs",
        database=tmp_path / "data" / "app.db",
        catalog=tmp_path / "data" / "catalog.db",
    )
    paths.ensure()
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    search = window.stack.widget(0)
    search._provider_id = "power"
    search.product.blockSignals(True)
    search.product.clear()
    search.product.addItem("日数据", "daily")
    search.product.blockSignals(False)
    search.variable_match.clear()
    search.variable_match.addItem(
        "2 米气温 (T2M)", ProviderVariable("T2M", "Temperature", "2 米气温")
    )
    search.variable_match.setCurrentIndex(0)
    search.latitude.setValue(30.5)
    search.longitude.setValue(114.3)

    request = search._request()

    assert request is not None
    assert request.provider_id == "power"
    assert request.product_id == "daily"
    assert request.parameters["latitude"] == "30.5"
    assert request.parameters["longitude"] == "114.3"
    database.dispose()
