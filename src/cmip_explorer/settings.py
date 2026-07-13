from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_documents_dir


def preferred_storage_directory() -> Path:
    drive_d = Path("D:/")
    if drive_d.exists():
        return drive_d / "CMIP Climate Explorer"
    return Path(user_documents_dir()) / "CMIP Climate Explorer"


@dataclass(frozen=True, slots=True)
class AppSettings:
    download_concurrency: int = 2
    cache_quota_gb: int = 20
    allow_insecure_http: bool = False
    update_channel: str = "stable"
    storage_directory: str = ""
    auto_convert_to_tif: bool = True

    @property
    def storage_path(self) -> Path:
        value = self.storage_directory.strip()
        return Path(value) if value else preferred_storage_directory()

    @classmethod
    def load(cls, path: Path) -> AppSettings:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            defaults = cls()
            return cls(
                download_concurrency=max(
                    1,
                    min(8, int(payload.get("download_concurrency", defaults.download_concurrency))),
                ),
                cache_quota_gb=max(
                    1, min(1000, int(payload.get("cache_quota_gb", defaults.cache_quota_gb)))
                ),
                allow_insecure_http=bool(
                    payload.get("allow_insecure_http", defaults.allow_insecure_http)
                ),
                update_channel=(
                    str(payload.get("update_channel", defaults.update_channel))
                    if payload.get("update_channel", defaults.update_channel)
                    in {"stable", "preview"}
                    else "stable"
                ),
                storage_directory=str(payload.get("storage_directory", "")),
                auto_convert_to_tif=bool(
                    payload.get("auto_convert_to_tif", defaults.auto_convert_to_tif)
                ),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
