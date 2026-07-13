from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str, object)
    finished = Signal()


class AsyncRunnable(QRunnable):
    def __init__(self, factory: Callable[[], Awaitable[Any]]) -> None:
        super().__init__()
        self.factory = factory
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(self.factory())
        except Exception as exc:
            _safe_emit(self.signals.error, traceback.format_exc(), exc)
        else:
            _safe_emit(self.signals.result, result)
        finally:
            _safe_emit(self.signals.finished)


class SyncRunnable(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:
            _safe_emit(self.signals.error, traceback.format_exc(), exc)
        else:
            _safe_emit(self.signals.result, result)
        finally:
            _safe_emit(self.signals.finished)


def _safe_emit(signal: Signal, *values: object) -> None:
    with suppress(RuntimeError):
        signal.emit(*values)
