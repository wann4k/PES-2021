"""Tema gelap ala menu PES."""

ACCENT = "#2ef2a0"
ACCENT_DARK = "#14b877"
BG = "#0d1117"
PANEL = "#151b24"
PANEL_2 = "#1c2430"
BORDER = "#2a3442"
TEXT = "#e6edf3"
MUTED = "#8b98a9"
DANGER = "#ff5c7a"

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWidget#central {{ background: {BG}; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {ACCENT}; padding: 4px; }}

QToolBar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #101820, stop:1 #0f2a22);
    border: none; border-bottom: 1px solid {BORDER}; padding: 6px; spacing: 4px;
}}
QToolBar QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 6px 10px; font-weight: 600;
}}
QToolBar QToolButton:hover {{ background: {PANEL_2}; border-color: {BORDER}; }}
QToolBar QToolButton:pressed {{ background: {ACCENT_DARK}; color: #001b10; }}

QPushButton {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {PANEL_2}; }}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_DARK});
    color: #00170d; border: none;
}}
QPushButton#primary:hover {{ background: {ACCENT}; }}
QPushButton#danger {{ color: {DANGER}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DARK}; }}

QListView, QTreeView, QTableWidget {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; outline: none;
    alternate-background-color: {PANEL_2};
}}
QListView::item {{ border-radius: 8px; padding: 6px; margin: 3px; }}
QListView::item:hover, QTreeView::item:hover {{ background: {PANEL_2}; }}
QListView::item:selected, QTreeView::item:selected {{
    background: rgba(46, 242, 160, 0.18); border: 1px solid {ACCENT}; color: {TEXT};
}}
QHeaderView::section {{ background: {PANEL_2}; border: none; padding: 5px; font-weight: 600; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{
    background: transparent; padding: 8px 16px; margin-right: 2px; color: {MUTED};
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QProgressBar {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; text-align: center;
    height: 16px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 10px;
    font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {ACCENT}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QSplitter::handle {{ background: {BG}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_DARK}; color: #00170d; }}
QLabel#title {{ font-size: 15pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#badge {{
    background: rgba(46, 242, 160, 0.15); color: {ACCENT}; border: 1px solid {ACCENT_DARK};
    border-radius: 9px; padding: 2px 8px; font-weight: 700; font-size: 8pt;
}}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
"""
