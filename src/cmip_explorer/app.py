from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from pathlib import Path

from cmip_explorer.application import WorkflowService
from cmip_explorer.config import APP_DISPLAY_NAME, AppPaths
from cmip_explorer.diagnostics import run_self_test
from cmip_explorer.infrastructure.catalog import install_packaged_catalog
from cmip_explorer.infrastructure.persistence import Database, TaskRepository
from cmip_explorer.logging_config import configure_logging, install_exception_hook
from cmip_explorer.settings import AppSettings


def initialize_application() -> tuple[AppPaths, Database, int]:
    paths = AppPaths.default()
    paths.ensure()
    install_packaged_catalog(paths.catalog)
    database = Database(paths.database)
    database.initialize()
    interrupted = TaskRepository(database).mark_running_tasks_interrupted()
    return paths, database, interrupted


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from cmip_explorer.ui import MainWindow

    paths, database, interrupted = initialize_application()
    log_path = configure_logging(paths.logs)
    install_exception_hook()
    logger = logging.getLogger("cmip_explorer.app")
    logger.info("application starting; log=%s", log_path)
    if "--self-test" in sys.argv:
        index = sys.argv.index("--self-test")
        output = (
            Path(sys.argv[index + 1]) if len(sys.argv) > index + 1 else paths.data / "self-test"
        )
        try:
            report = run_self_test(output, paths, database)
            logger.info("self-test passed; report=%s", report)
            database.dispose()
            return 0
        except Exception as exc:
            output.mkdir(parents=True, exist_ok=True)
            (output / "self-test-report.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.exception("self-test failed")
            database.dispose()
            return 1
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setOrganizationName("CMIPClimateExplorer")
    repository = TaskRepository(database)
    settings = AppSettings.load(paths.data / "settings.json")
    workflow = WorkflowService(
        paths,
        repository,
        allow_insecure_http=settings.allow_insecure_http,
        storage_root=settings.storage_path,
        auto_convert_to_tif=settings.auto_convert_to_tif,
    )
    window = MainWindow(paths, repository, workflow)
    if interrupted:
        logger.warning("marked %s running tasks interrupted", interrupted)
        window.statusBar().showMessage(f"已恢复 {interrupted} 个中断任务", 12000)
    window.show()
    result = app.exec()
    asyncio.run(workflow.close())
    database.dispose()
    logger.info("application stopped; exit_code=%s", result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
