from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from cmip_explorer.domain.models import LogicalFile, Region


class ApplicationState(QObject):
    files_changed = Signal()
    region_changed = Signal()
    message = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.selected_files: list[LogicalFile] = []
        self.region: Region | None = None

    def set_files(self, files: list[LogicalFile]) -> None:
        self.selected_files = files
        self.files_changed.emit()

    def set_region(self, region: Region) -> None:
        self.region = region
        self.region_changed.emit()
