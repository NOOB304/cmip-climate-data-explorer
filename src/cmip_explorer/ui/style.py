APP_STYLESHEET = """
QWidget {
    color: #172127;
    background: #f5f7f8;
    font-family: "Noto Sans CJK SC", "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {
    background: #f5f7f8;
}
QFrame#Sidebar {
    background: #202a2f;
    border: 0;
}
QLabel#Brand {
    color: #ffffff;
    font-size: 14pt;
    font-weight: 600;
    padding: 18px 16px 12px 16px;
}
QListWidget#Navigation {
    background: #202a2f;
    color: #dce5e8;
    border: 0;
    outline: 0;
    padding: 6px;
}
QListWidget#Navigation::item {
    height: 38px;
    padding-left: 10px;
    border-radius: 4px;
}
QListWidget#Navigation::item:selected {
    background: #306a6d;
    color: white;
}
QLabel#PageTitle {
    font-size: 18pt;
    font-weight: 600;
    color: #172127;
}
QLabel#SectionTitle {
    font-size: 11pt;
    font-weight: 600;
    color: #314249;
}
QLabel#SelectionCount, QLabel#ActivityBanner {
    background: #dceff0;
    color: #174f52;
    border: 1px solid #9ac9cc;
    border-radius: 4px;
    padding: 7px 10px;
    font-weight: 600;
}
QLineEdit, QComboBox, QSpinBox, QTableWidget, QTextEdit, QPlainTextEdit {
    background: white;
    border: 1px solid #c8d1d5;
    border-radius: 4px;
    padding: 6px;
    selection-background-color: #347c80;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #347c80;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #aebbc0;
    border-radius: 4px;
    padding: 6px 12px;
    min-height: 20px;
}
QPushButton:hover { background: #edf2f3; }
QPushButton:disabled { color: #89969b; background: #eef1f2; }
QPushButton#PrimaryButton {
    color: white;
    background: #347c80;
    border-color: #347c80;
    font-weight: 600;
}
QPushButton#DangerButton {
    color: white;
    background: #b34336;
    border-color: #b34336;
}
QHeaderView::section {
    background: #e7ecee;
    color: #2c3b41;
    border: 0;
    border-right: 1px solid #d4dcdf;
    border-bottom: 1px solid #c8d1d5;
    padding: 7px;
    font-weight: 600;
}
QTableWidget {
    gridline-color: #e1e6e8;
    selection-background-color: #8dc6c9;
    selection-color: #102c2e;
}
QStatusBar { background: #e7ecee; color: #415158; }
QProgressBar {
    border: 1px solid #c8d1d5;
    border-radius: 3px;
    text-align: center;
    background: white;
}
QProgressBar::chunk { background: #347c80; }
"""
