from pathlib import Path

from cmip_explorer.settings import AppSettings


def test_settings_round_trip_and_invalid_fallback(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    expected = AppSettings(4, 50, True, "preview")
    expected.save(path)
    assert AppSettings.load(path) == expected
    path.write_text("not-json", encoding="utf-8")
    assert AppSettings.load(path) == AppSettings()


def test_settings_clamp_numeric_values_to_supported_ranges(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        '{"download_concurrency": 99, "cache_quota_gb": 9999}',
        encoding="utf-8",
    )
    settings = AppSettings.load(path)
    assert settings.download_concurrency == 20
    assert settings.cache_quota_gb == 2048.0

    path.write_text(
        '{"download_concurrency": 0, "cache_quota_gb": 0}',
        encoding="utf-8",
    )
    settings = AppSettings.load(path)
    assert settings.download_concurrency == 1
    assert settings.cache_quota_gb == 0.1
