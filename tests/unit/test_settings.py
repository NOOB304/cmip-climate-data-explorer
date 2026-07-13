from pathlib import Path

from cmip_explorer.settings import AppSettings


def test_settings_round_trip_and_invalid_fallback(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    expected = AppSettings(4, 50, True, "preview")
    expected.save(path)
    assert AppSettings.load(path) == expected
    path.write_text("not-json", encoding="utf-8")
    assert AppSettings.load(path) == AppSettings()
