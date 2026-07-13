from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "app.jsonl"
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=4, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
    return path


def install_exception_hook() -> None:
    previous = sys.excepthook

    def report(
        exception_type: type[BaseException],
        value: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        logging.getLogger("cmip_explorer.crash").critical(
            "uncaught exception", exc_info=(exception_type, value, traceback)
        )
        previous(exception_type, value, traceback)

    sys.excepthook = report
