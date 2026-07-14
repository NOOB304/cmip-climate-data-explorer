APP_STYLESHEET = """
QWidget {
    color: #172126;
    background: #f4f6f7;
    font-family: "Noto Sans CJK SC", "Segoe UI";
    font-size: 10pt;
    letter-spacing: 0;
}
QMainWindow, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {
    background: #f4f6f7;
}
QLabel {
    background: transparent;
}
QWidget#SectionBody {
    background: transparent;
}
QFrame#Sidebar {
    background: #19262c;
    border: 0;
}
QFrame#BrandBar {
    background: #152127;
    border: 0;
    border-bottom: 1px solid #2b3a40;
}
QLabel#Brand {
    color: #f8fbfc;
    background: transparent;
    font-size: 12pt;
    font-weight: 600;
}
QLabel#SidebarCaption, QLabel#SidebarVersion {
    color: #91a4ac;
    background: transparent;
    font-size: 9pt;
}
QLabel#SidebarAuthor {
    color: #d6dfe3;
    background: transparent;
    font-size: 9pt;
    font-weight: 500;
}
QLabel#ConnectionDot {
    color: #4cc38a;
    background: transparent;
    font-size: 12pt;
}
QListWidget#Navigation {
    background: #19262c;
    color: #d9e2e5;
    border: 0;
    outline: 0;
    padding: 8px;
}
QListWidget#Navigation::item {
    height: 43px;
    padding-left: 10px;
    border-radius: 5px;
}
QListWidget#Navigation::item:hover {
    background: #23343b;
}
QListWidget#Navigation::item:selected {
    background: #0f777a;
    color: #ffffff;
}
QLabel#PageTitle {
    font-size: 20pt;
    font-weight: 650;
    color: #142025;
    background: transparent;
}
QLabel#PageSubtitle {
    color: #64747b;
    background: transparent;
    font-size: 9.5pt;
}
QLabel#SectionTitle {
    font-size: 11pt;
    font-weight: 650;
    color: #223238;
    background: transparent;
}
QLabel#SectionDescription, QLabel#MutedText {
    color: #718087;
    background: transparent;
    font-size: 9pt;
}
QLabel#SelectionCount {
    background: #e0f1f1;
    color: #0d6669;
    border: 1px solid #9ecfd0;
    border-radius: 5px;
    padding: 6px 10px;
    font-weight: 600;
}
QLabel#ActivityBanner {
    background: #e6f3f3;
    color: #155e61;
    border-left: 3px solid #15898b;
    padding: 8px 10px;
    font-weight: 600;
}
QLabel#MetricLabel {
    color: #718087;
    background: transparent;
    font-size: 9pt;
}
QLabel#MetricValue {
    color: #172126;
    background: transparent;
    font-size: 13pt;
    font-weight: 650;
}
QFrame#Panel, QFrame#FilterPanel, QFrame#MetricStrip, QFrame#DetailsPanel {
    background: #ffffff;
    border: 1px solid #d9e0e3;
    border-radius: 6px;
}
QFrame#CompactSection {
    background: #ffffff;
    border: 1px solid #d9e0e3;
    border-radius: 5px;
}
QToolButton {
    color: #223238;
    background: transparent;
    border: 0;
    padding: 4px 2px;
    font-weight: 600;
}
QToolButton:hover {
    color: #0f777a;
}
QFrame#SettingsSection {
    background: transparent;
    border: 0;
    border-bottom: 1px solid #d9e0e3;
}
QFrame#ActionBar {
    background: #ffffff;
    border-top: 1px solid #d9e0e3;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #cbd4d8;
    border-radius: 5px;
    padding: 6px 8px;
    min-height: 22px;
    selection-background-color: #15898b;
    selection-color: #ffffff;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #9caeb5;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #15898b;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #87959b;
    background: #eef1f2;
    border-color: #d9e0e3;
}
QComboBox::drop-down {
    width: 26px;
    border: 0;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #b9c6ca;
    selection-background-color: #d8eeee;
    selection-color: #173336;
    outline: 0;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #b9c6ca;
    border-radius: 5px;
    padding: 6px 12px;
    min-height: 22px;
}
QPushButton:hover {
    background: #edf3f4;
    border-color: #8ca1a8;
}
QPushButton:pressed {
    background: #e2e9eb;
}
QPushButton:disabled {
    color: #96a2a7;
    background: #eef1f2;
    border-color: #dce2e4;
}
QPushButton#PrimaryButton {
    color: #ffffff;
    background: #128487;
    border-color: #128487;
    font-weight: 650;
}
QPushButton#PrimaryButton:hover {
    background: #0e7477;
    border-color: #0e7477;
}
QPushButton#PrimaryButton:disabled {
    color: #8d999e;
    background: #e7ecee;
    border-color: #d5dde0;
}
QPushButton#DangerButton {
    color: #a43a32;
    background: #fff7f6;
    border-color: #e1a8a3;
}
QPushButton#DangerButton:hover {
    color: #ffffff;
    background: #b84439;
    border-color: #b84439;
}
QPushButton#StopButton {
    color: #ffffff;
    background: #b84439;
    border-color: #b84439;
    font-weight: 650;
}
QPushButton#StopButton:hover {
    background: #a33830;
}
QPushButton#StopButton:disabled {
    color: #9aa5a9;
    background: #edf0f1;
    border-color: #d9e0e3;
}
QCheckBox {
    spacing: 7px;
    background: transparent;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QCheckBox#Switch::indicator {
    width: 38px;
    height: 20px;
    border-radius: 10px;
    border: 1px solid #aebbc0;
    background: #c5cdd0;
}
QCheckBox#Switch::indicator:checked {
    background: #128487;
    border-color: #128487;
}
QTabBar {
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: #4e5d63;
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 9px 14px;
    min-height: 24px;
}
QTabBar::tab:hover {
    color: #0f6f72;
    background: #eef4f4;
}
QTabBar::tab:selected {
    color: #0f777a;
    border-bottom-color: #128487;
    font-weight: 650;
}
QTableWidget, QTableView {
    background: #ffffff;
    alternate-background-color: #f8fafb;
    border: 1px solid #d9e0e3;
    border-radius: 5px;
    gridline-color: #e5eaec;
    selection-background-color: #d7eeee;
    selection-color: #153538;
    outline: 0;
}
QTableWidget::item, QTableView::item {
    padding: 6px;
    border: 0;
}
QTableWidget::item:selected, QTableView::item:selected {
    background: #d7eeee;
    color: #153538;
    border-top: 1px solid #53aaac;
    border-bottom: 1px solid #53aaac;
}
QHeaderView::section {
    background: #eef2f3;
    color: #34454c;
    border: 0;
    border-right: 1px solid #d9e0e3;
    border-bottom: 1px solid #ccd6da;
    padding: 7px;
    font-weight: 650;
}
QProgressBar {
    border: 0;
    border-radius: 4px;
    text-align: center;
    color: #304147;
    background: #e3e9eb;
    min-height: 9px;
}
QProgressBar::chunk {
    background: #128487;
    border-radius: 4px;
}
QStatusBar {
    background: #ffffff;
    color: #5f6f76;
    border-top: 1px solid #d9e0e3;
}
QMenu {
    background: #ffffff;
    border: 1px solid #cbd4d8;
    padding: 4px;
}
QMenu::item {
    padding: 7px 24px 7px 10px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #dceeee;
    color: #123f41;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #b8c4c8;
    min-height: 32px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #b8c4c8;
    min-width: 32px;
    border-radius: 4px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QSplitter::handle {
    background: #d9e0e3;
    width: 1px;
    height: 1px;
}
QToolTip {
    color: #f4f7f8;
    background: #24343a;
    border: 1px solid #405159;
    padding: 5px;
}
"""
