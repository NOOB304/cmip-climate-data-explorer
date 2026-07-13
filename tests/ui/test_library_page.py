from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from cmip_explorer.ui.pages.library import LibraryPage, scan_local_data_groups


def test_local_data_is_grouped_and_checked_groups_can_be_deleted(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    processed = tmp_path / "Processed" / "Model" / "scenario" / "tas_day"
    downloaded = tmp_path / "NetCDF" / "Model" / "scenario"
    processed.mkdir(parents=True)
    downloaded.mkdir(parents=True)
    (processed / "tas_2020.tif").write_bytes(b"a" * 10)
    (processed / "tas_2021.tif").write_bytes(b"b" * 20)
    (processed / "processing_manifest.json").write_text("{}", encoding="utf-8")
    (downloaded / "tas_day_2020.nc").write_bytes(b"c" * 30)
    (downloaded / "station_daily.csv").write_bytes(b"d" * 40)

    groups = scan_local_data_groups(tmp_path)

    assert len(groups) == 2
    processed_group = next(group for group in groups if group.category == "处理结果")
    assert processed_group.path == processed
    assert processed_group.file_count == 2
    assert processed_group.contents == "2 个 TIF"
    assert processed_group.size_bytes == 30

    page = LibraryPage(tmp_path)
    qtbot.addWidget(page)
    assert page.table.rowCount() == 2
    assert page.table.columnCount() == 6
    assert "4 个数据文件" in page.status.text()
    assert all("tas_2020.tif" not in page.table.item(row, 1).text() for row in range(2))
    assert not page.delete_button.isEnabled()

    processed_row = next(
        row
        for row in range(page.table.rowCount())
        if Path(page.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) == processed
    )
    page.table.item(processed_row, 0).setCheckState(Qt.CheckState.Checked)
    assert page.delete_button.isEnabled()
    assert page.delete_button.text() == "删除所选 (1)"
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )

    page._delete()

    assert not processed.exists()
    assert downloaded.exists()
    assert page.table.rowCount() == 1
