from __future__ import annotations

from importlib.resources import as_file, files

from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

_FONT_FAMILY: str | None = None


def install_ui_font() -> str:
    global _FONT_FAMILY
    if _FONT_FAMILY is not None:
        return _FONT_FAMILY
    resource = files("cmip_explorer.resources").joinpath("fonts/NotoSansCJKsc-Regular.otf")
    with as_file(resource) as path:
        font_id = QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    _FONT_FAMILY = families[0] if families else "Segoe UI"
    application = QApplication.instance()
    if application is not None:
        application.setFont(QFont(_FONT_FAMILY, 10))
    return _FONT_FAMILY


def load_app_icon() -> QIcon:
    resource = files("cmip_explorer.resources").joinpath("app-icon.png")
    with as_file(resource) as path:
        return QIcon(str(path))
