from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cmip_explorer.application import WorkflowService
from cmip_explorer.config import AppPaths
from cmip_explorer.domain.enums import TaskStatus
from cmip_explorer.domain.models import (
    AccessEndpoint,
    LogicalFile,
    Replica,
    SearchPage,
    TemporalCoverage,
)
from cmip_explorer.infrastructure.catalog import install_packaged_catalog
from cmip_explorer.infrastructure.persistence import Database, TaskRepository
from cmip_explorer.ui import MainWindow


def main() -> int:
    output = Path(sys.argv[1]).resolve()
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    root = Path(tempfile.mkdtemp(prefix="cmip-ui-"))
    paths = AppPaths(
        data=root / "data",
        cache=root / "cache",
        logs=root / "logs",
        outputs=root / "outputs",
        database=root / "data" / "app.db",
        catalog=root / "data" / "catalog.db",
    )
    paths.ensure()
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    repository = TaskRepository(database)
    workflow = WorkflowService(
        paths,
        repository,
        storage_root=root / "climate-data",
    )
    if page == 1:
        sample = _sample_file()
        job = workflow.create_job("界面截图", {"file": sample.logical_key})
        task_id, _ = workflow.enqueue_download(job, sample)
        repository.transition(task_id, TaskStatus.RESOLVING)
        repository.transition(task_id, TaskStatus.PROBING)
        repository.transition(task_id, TaskStatus.DOWNLOADING)
        repository.update_progress(task_id, 86_000_000)
    app = QApplication([])
    window = MainWindow(paths, repository, workflow)
    window.resize(1180, 760)
    window.navigation.setCurrentRow(0 if page == 6 else page)
    if page == 0:
        search = window.stack.currentWidget()
        sample = _sample_file()
        search._show_results(SearchPage(files=(sample,), known_unique_count=1))
        search.table_widget.item(0, 0).setCheckState(Qt.CheckState.Checked)
    elif page == 1:
        window.stack.currentWidget().table.selectRow(0)
    elif page == 6:
        search = window.stack.currentWidget()
        search.activity.show()
        search.search_button.setText("查询中…")
        search.search_button.setEnabled(False)
    window.show()
    app.processEvents()
    output.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(output))
    window.close()
    database.dispose()
    return 0


def _sample_file() -> LogicalFile:
    return LogicalFile(
        logical_key="sample-pr",
        filename="pr_Amon_BCC-CSM2-MR_ssp245_r1i1p1f1_gn_201501-210012.nc",
        source_id="BCC-CSM2-MR",
        experiment_id="ssp245",
        table_id="Amon",
        variable_id="pr",
        grid_label="gn",
        frequency="mon",
        nominal_resolution="100 km",
        size_bytes=405_577_108,
        temporal=TemporalCoverage(start="201501", end="210012", source="filename"),
        replicas=(
            Replica(
                data_node="example.test",
                backend_id="capture",
                replica=False,
                endpoints=(
                    AccessEndpoint(
                        url="https://example.test/pr.nc",
                        service="HTTPServer",
                        secure=True,
                    ),
                ),
            ),
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
