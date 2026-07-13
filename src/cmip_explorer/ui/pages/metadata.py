from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class MetadataPage(QWidget):
    def __init__(self, catalog_path: Path) -> None:
        super().__init__()
        self.catalog_path = catalog_path
        layout = QVBoxLayout(self)
        title = QLabel("元数据")
        title.setObjectName("PageTitle")
        refresh = QPushButton("刷新信息")
        refresh.clicked.connect(self.refresh)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(title)
        layout.addWidget(refresh)
        layout.addWidget(self.text, 1)
        self.refresh()

    def refresh(self) -> None:
        with sqlite3.connect(self.catalog_path) as connection:
            definitions = connection.execute(
                "SELECT count(*) FROM variable_definitions"
            ).fetchone()[0]
            revision = connection.execute(
                "SELECT value FROM catalog_metadata WHERE key='source_revision'"
            ).fetchone()[0]
        self.text.setPlainText(
            "\n".join(
                [
                    f"CMIP6 table-variable 定义: {definitions}",
                    f"CMOR Tables revision: {revision}",
                    "",
                    "默认检索后端:",
                    "- ORNL ESGF 1.5 Bridge",
                    "- DKRZ Legacy Solr",
                    "- IPSL Legacy Solr",
                    "- CEDA Legacy Solr",
                    "",
                    "LLNL 未设为默认端点。",
                ]
            )
        )
