from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractSpinBox, QMessageBox, QProgressBar

import cmip_explorer.ui.pages.settings as settings_module
from cmip_explorer import __version__
from cmip_explorer.application import WorkflowService
from cmip_explorer.config import AppPaths
from cmip_explorer.domain.enums import DownloadMode
from cmip_explorer.domain.models import (
    AccessEndpoint,
    DownloadTask,
    LogicalFile,
    Replica,
    SearchRequest,
    TemporalCoverage,
)
from cmip_explorer.domain.models import SearchPage as ResultPage
from cmip_explorer.infrastructure.catalog import VariableOption, install_packaged_catalog
from cmip_explorer.infrastructure.persistence import Database, TaskRepository
from cmip_explorer.infrastructure.providers import PROVIDERS, ProviderVariable
from cmip_explorer.ui import MainWindow
from cmip_explorer.ui.pages.search import SearchPage as UISearchPage
from cmip_explorer.ui.pages.search import _coverage
from cmip_explorer.ui.pages.tasks import _friendly_download_error, _status_label


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
    assert settings_page.version_label.text() == f"当前版本 {__version__}"
    assert window.sidebar_version.text() == (
        f"v{__version__}  |  Developed by Wei Heng"
    )
    settings_page._show_update_progress(50 * 1024**2, 100 * 1024**2)
    assert settings_page.update_progress.maximum() == 1000
    assert settings_page.update_progress.value() == 500
    assert settings_page.update_progress.format() == "50.0 MiB / 100.0 MiB (50.0%)"
    settings_page._show_update_retry(2, 8, 5)
    retry_status = settings_page.update_status.text()
    assert "网络连接中断" in retry_status
    assert "5 秒后自动重连" in retry_status
    assert "2/8" in retry_status
    assert window.minimumWidth() >= 1180
    database.dispose()


def test_settings_numeric_fields_hide_buttons_and_clamp_values(
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
    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    settings_page = window.stack.widget(5)
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    assert settings_page.download_concurrency.buttonSymbols() == (
        QAbstractSpinBox.ButtonSymbols.NoButtons
    )
    assert settings_page.cache_quota.buttonSymbols() == (
        QAbstractSpinBox.ButtonSymbols.NoButtons
    )
    settings_page.download_concurrency.setValue(99)
    settings_page._validate_download_concurrency()
    settings_page.cache_quota.setValue(9999)
    settings_page._validate_cache_quota()

    assert settings_page.download_concurrency.value() == 20
    assert settings_page.cache_quota.value() == 2048.0
    assert len(warnings) == 2
    database.dispose()


def test_download_retry_status_shows_attempt_and_countdown() -> None:
    task = SimpleNamespace(
        status="retry_wait",
        retry_attempt=2,
        retry_maximum=5,
        retry_at=(datetime.now(UTC) + timedelta(seconds=8)).isoformat(),
    )
    label = _status_label(task)
    assert "重连 2/5" in label
    assert "秒后" in label
    friendly_error = _friendly_download_error("502 Bad Gateway")
    assert "HTTP 502" in friendly_error
    assert "稍后重新连接" in friendly_error


def test_log_page_defaults_to_user_operations_and_can_show_details(
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
    window._log_records = [
        {
            "timestamp": "2026-07-14T08:00:00+00:00",
            "level": "INFO",
            "logger": "cmip_explorer.operations",
            "message": "已删除本地数据组: D:/data",
        },
        {
            "timestamp": "2026-07-14T08:01:00+00:00",
            "level": "INFO",
            "logger": "httpx",
            "message": "HTTP Request: GET https://example.test",
        },
    ]
    window._render_logs()

    assert window.log_table.columnCount() == 3
    assert window.log_table.rowCount() == 1
    assert window.log_table.item(0, 1).text() == "数据管理"
    window.log_mode_button.setChecked(True)
    assert window.log_table.columnCount() == 4
    assert window.log_table.rowCount() == 2
    assert window.log_mode_button.text() == "返回操作记录"
    database.dispose()


def test_verified_update_runs_silently_and_restarts_application(
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
    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    settings_page = window.stack.widget(5)
    started: list[tuple[str, list[str]]] = []
    timers: list[tuple[int, object]] = []
    monkeypatch.setattr(settings_module.os, "getpid", lambda: 4242)
    monkeypatch.setattr(
        settings_module,
        "QProcess",
        SimpleNamespace(
            startDetached=lambda program, args: (
                started.append((program, args)) is None,
                123,
            )
        ),
    )
    monkeypatch.setattr(
        settings_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: timers.append((delay, callback))),
    )

    installer = tmp_path / "CMIP-Climate-Explorer-0.5.0-x64-Setup.exe"
    settings_page._update_downloaded(installer)

    assert started == [
        (
            "powershell.exe",
            [
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(tmp_path / "apply-update.ps1"),
                "-ParentPid",
                "4242",
                "-Installer",
                str(installer),
                "-LogPath",
                str(tmp_path / "update-install.log"),
            ],
        )
    ]
    assert timers and timers[0][0] == 100
    assert "正在静默安装" in settings_page.update_status.text()
    launcher_script = (tmp_path / "apply-update.ps1").read_text(encoding="utf-8")
    assert "Get-Process -Id $ParentPid" in launcher_script
    assert "'/SP-'" in launcher_script
    assert "'/VERYSILENT'" in launcher_script
    assert "'/SUPPRESSMSGBOXES'" in launcher_script
    assert "'/FORCECLOSEAPPLICATIONS'" in launcher_script
    assert "'/UPDATE=1'" in launcher_script
    assert "'/STAGEDUPDATE=1'" in launcher_script
    assert "-Wait -WindowStyle Hidden" in launcher_script
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

    open_meteo_index = next(
        index
        for index in range(search.provider_tabs.count())
        if search.provider_tabs.tabData(index) == "openmeteo"
    )
    search.provider_tabs.setCurrentIndex(open_meteo_index)
    assert search.source.isVisibleTo(search)
    assert search.latitude.isVisibleTo(search)
    assert search.start_year.minimum() == 1940
    assert search.end_year.maximum() >= 2026
    assert "不是未来预估" in search.provider_info.text()
    database.dispose()


def test_coverage_formats_iso_compact_epoch_and_missing_values() -> None:
    assert _coverage(
        LogicalFile(
            logical_key="iso",
            filename="iso.nc",
            frequency="day",
            temporal=TemporalCoverage(
                start="2015-01-01T12:00:00Z",
                end="2055-12-31T12:00:00Z",
                source="api",
            ),
        )
    ) == "2015-01-01 至 2055-12-31"
    assert _coverage(
        LogicalFile(
            logical_key="compact",
            filename="compact.nc",
            temporal=TemporalCoverage(start="201501", end="205512", source="filename"),
        )
    ) == "2015-01 至 2055-12"
    assert _coverage(
        LogicalFile(
            logical_key="missing",
            filename="missing.nc",
            temporal=TemporalCoverage(),
        )
    ) == "时间范围未提供"


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


def test_search_pagination_supports_previous_and_preserves_results_on_empty_page(
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
    window = MainWindow(paths, repository, WorkflowService(paths, repository))
    qtbot.addWidget(window)
    search = window.stack.widget(0)
    request = SearchRequest()
    first = LogicalFile(logical_key="first", filename="first.nc")
    second = LogicalFile(logical_key="second", filename="second.nc")

    search._show_search_results(
        search._search_sequence,
        ResultPage(files=(first,), next_cursors={"dkrz": 100}),
        request,
        1,
        {},
    )
    assert not search.previous_button.isEnabled()
    assert search.next_button.isEnabled()

    search._show_search_results(
        search._search_sequence,
        ResultPage(files=(second,), next_cursors={"dkrz": 200}),
        request,
        2,
        {"dkrz": 100},
    )
    assert search.previous_button.isEnabled()
    assert search.table_widget.item(0, 12).text() == "second.nc"

    requested = []
    monkeypatch.setattr(
        search,
        "_run_request",
        lambda req, cursors, *, page_number: requested.append(
            (req, cursors, page_number)
        ),
    )
    search.previous_page()
    assert requested == [(request, {}, 1)]

    search._show_search_results(
        search._search_sequence,
        ResultPage(files=(), next_cursors={"dkrz": None}),
        request,
        3,
        {"dkrz": 200},
    )
    assert search._page_number == 2
    assert search.table_widget.item(0, 12).text() == "second.nc"
    assert not search.next_button.isEnabled()
    assert "已保留当前页面" in search.summary.text()
    database.dispose()
