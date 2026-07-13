from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir, user_data_dir, user_documents_dir, user_log_dir

APP_NAME = "CMIPClimateExplorer"
APP_DISPLAY_NAME = "CMIP Climate Explorer"


@dataclass(frozen=True, slots=True)
class AppPaths:
    data: Path
    cache: Path
    logs: Path
    outputs: Path
    database: Path
    catalog: Path

    @classmethod
    def default(cls) -> AppPaths:
        data = Path(user_data_dir(APP_NAME, appauthor=False))
        preferred_outputs = (
            Path("D:/") / APP_DISPLAY_NAME
            if Path("D:/").exists()
            else Path(user_documents_dir()) / APP_DISPLAY_NAME
        )
        return cls(
            data=data,
            cache=Path(user_cache_dir(APP_NAME, appauthor=False)),
            logs=Path(user_log_dir(APP_NAME, appauthor=False)),
            outputs=preferred_outputs,
            database=data / "app.db",
            catalog=data / "catalog.db",
        )

    def ensure(self) -> None:
        for path in (self.data, self.cache, self.logs, self.outputs):
            path.mkdir(parents=True, exist_ok=True)
