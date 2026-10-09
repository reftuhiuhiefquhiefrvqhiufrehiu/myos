"""Central visual design system for NeonVeil.

Every NeonVeil window, menu, popup and control draws its colours from the
palettes defined here so that the whole desktop shares one coherent look.
Keeping the tokens in a single module means the light and dark appearances can
be tuned in one place instead of scattered hex values across the code base.
"""

from __future__ import annotations

LIGHT: dict[str, str] = {
    "window": "#eef3f5",
    "surface": "#ffffff",
    "surface_alt": "#e8eff1",
    "surface_hover": "#f7fbfc",
    "surface_sunken": "#e2ebee",
    "border": "#a9bcc1",
    "border_strong": "#91a8af",
    "text": "#20333b",
    "text_muted": "#536c74",
    "heading": "#14596a",
    "accent": "#176c67",
    "accent_hover": "#247f79",
    "accent_soft": "#dce9eb",
    "accent_text": "#ffffff",
    "selection": "#c8e1e7",
    "selection_text": "#173d48",
    "menu_bg": "#f8fafb",
    "menu_border": "#668995",
    "focus": "#176c67",
    "danger": "#9a3f35",
    "success": "#2f7a4f",
    "disabled_text": "#73878d",
    "tooltip_bg": "#173d48",
    "taskbar_bg": "#dbe5e8",
    "taskbar_edge": "#f8fcfd",
    "clock_bg": "#edf3f4",
    "clock_text": "#173d48",
}

DARK = {
    "window": "#1e262b",
    "surface": "#202a2f",
    "surface_alt": "#35464d",
    "surface_hover": "#3b4e55",
    "border": "#536a71",
    "border_strong": "#5d747b",
    "text": "#e4ecee",
    "text_muted": "#b3c3c7",
    "heading": "#69bdb1",
    "accent": "#2fb3a6",
    "accent_hover": "#41c8bb",
    "accent_soft": "#354f55",
    "accent_text": "#06181a",
    "selection": "#354f55",
    "selection_text": "#dce9eb",
    "menu_bg": "#2b383e",
    "menu_border": "#5d747b",
    "focus": "#69bdb1",
    "danger": "#e08a80",
    "success": "#7bd3a4",
    "shadow": "rgba(0, 0, 0, 120)",
    "taskbar_bg": "#1b2429",
    "taskbar_border": "#0f171b",
    "clock_bg": "#303e44",
}

LIGHT.setdefault("shadow", "rgba(15, 40, 50, 70)")
LIGHT.setdefault("taskbar_border", "#b7c6cb")

# A bright neon teal used sparingly for glow accents.
NEON = "#35c9bd"

APPLICATION_NAME = "NeonVeil"
ORGANIZATION_NAME = "neonveil"
ORGANIZATION_DOMAIN = "neonveil.local"


def normalize_theme(theme: str | None) -> str:
    """Return ``"dark"`` or ``"light"`` for any input value."""
    return "dark" if str(theme).lower() == "dark" else "light"


def palette(theme: str = "light") -> dict[str, str]:
    """Return the resolved colour tokens for ``theme``."""
    return dict(DARK if normalize_theme(theme) == "dark" else LIGHT)


def global_stylesheet(theme: str = "light") -> str:
    """Return the application-wide Qt stylesheet for ``theme``."""
    c = palette(theme)
    return f"""
        QMainWindow, QDialog, QWidget {{
            background: {c['window']};
            color: {c['text']};
        }}
        QLabel#heading {{ color: {c['heading']}; font-size: 22px; font-weight: 700; }}
        QLabel#subtitle {{ color: {c['text_muted']}; }}
        QLabel:disabled {{ color: {c['text_muted']}; }}

        QPushButton, QToolButton, QComboBox {{
            color: {c['text']};
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {c['surface_hover']}, stop:1 {c['surface_alt']});
            border: 1px solid {c['border_strong']};
            border-radius: 6px;
            padding: 6px 12px;
        }}
        QPushButton:hover, QToolButton:hover, QComboBox:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {c['surface']}, stop:1 {c['surface_hover']});
            border-color: {c['accent']};
        }}
        QPushButton:pressed, QToolButton:pressed, QComboBox:on {{
            background: {c['accent_soft']};
            border-color: {c['accent']};
        }}
        QPushButton:focus, QToolButton:focus, QComboBox:focus {{
            border: 2px solid {c['focus']};
            background: {c['surface_hover']};
        }}
        QPushButton:disabled, QToolButton:disabled, QComboBox:disabled {{
            color: {c['text_muted']};
            background: {c['surface_alt']};
            border-color: {c['border']};
        }}
        QPushButton:checked, QToolButton:checked {{
            background: {c['accent_soft']};
            border-color: {c['accent']};
            color: {c['text']};
        }}

        QLineEdit, QTextEdit, QPlainTextEdit, QListWidget, QListView,
        QTreeView, QTableView, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit {{
            background: {c['surface']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 4px 6px;
            selection-background-color: {c['accent']};
            selection-color: {c['accent_text']};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus,
        QListView:focus, QTreeView:focus, QTableView:focus, QSpinBox:focus {{
            border: 1px solid {c['accent']};
        }}
        QListWidget::item, QListView::item, QTreeView::item {{
            border-radius: 5px;
            padding: 3px;
        }}
        QListWidget::item:selected, QListView::item:selected, QTreeView::item:selected {{
            background: {c['accent_soft']};
            color: {c['text']};
        }}
        QListWidget::item:hover, QListView::item:hover, QTreeView::item:hover {{
            background: {c['surface_hover']};
        }}
        QHeaderView::section {{
            background: {c['surface_alt']};
            color: {c['text']};
            border: none;
            border-right: 1px solid {c['border']};
            padding: 5px 8px;
        }}

        QCheckBox, QRadioButton {{ color: {c['text']}; spacing: 6px; }}
        QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
        QCheckBox::indicator:unchecked {{
            background: {c['surface']};
            border: 1px solid {c['border_strong']};
            border-radius: 4px;
        }}
        QCheckBox::indicator:checked {{
            background: {c['accent']};
            border: 1px solid {c['accent']};
            border-radius: 4px;
        }}
        QRadioButton::indicator:unchecked {{
            background: {c['surface']};
            border: 1px solid {c['border_strong']};
            border-radius: 8px;
        }}
        QRadioButton::indicator:checked {{
            background: {c['accent']};
            border: 1px solid {c['accent']};
            border-radius: 8px;
        }}

        QMenu {{
            background: {c['menu_bg']};
            border: 1px solid {c['menu_border']};
            border-radius: 8px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 30px 7px 14px;
            border-radius: 5px;
        }}
        QMenu::item:selected {{
            background: {c['accent_soft']};
            color: {c['text']};
        }}
        QMenu::separator {{
            height: 1px;
            background: {c['border']};
            margin: 5px 8px;
        }}

        QScrollBar:vertical {{
            background: transparent;
            width: 12px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border_strong']};
            border-radius: 5px;
            min-height: 28px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 12px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {c['border_strong']};
            border-radius: 5px;
            min-width: 28px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {c['accent']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

        QTabBar::tab {{
            background: {c['surface_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-bottom: none;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 6px 14px;
        }}
        QTabBar::tab:selected {{ background: {c['surface']}; color: {c['heading']}; }}
        QTabBar::tab:hover {{ background: {c['surface_hover']}; }}

        QProgressBar {{
            background: {c['surface_alt']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            text-align: center;
            color: {c['text']};
        }}
        QProgressBar::chunk {{
            background: {c['accent']};
            border-radius: 5px;
        }}

        QSlider::groove:horizontal {{
            height: 6px;
            background: {c['surface_alt']};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            margin: -6px 0;
            background: {c['accent']};
            border-radius: 8px;
        }}
        QSlider::handle:horizontal:hover {{ background: {c['accent_hover']}; }}

        QGroupBox {{
            border: 1px solid {c['border']};
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: {c['heading']};
        }}

        QToolTip {{
            background: {c['menu_bg']};
            color: {c['text']};
            border: 1px solid {c['menu_border']};
            border-radius: 4px;
            padding: 4px 6px;
        }}
        QSplitter::handle {{ background: {c['border']}; }}
    """


def taskbar_stylesheet(theme: str = "light") -> str:
    """Return the Qt stylesheet for the bottom taskbar."""
    c = palette(theme)
    return f"""
        QWidget#taskbar {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {c['taskbar_bg']}, stop:1 {c['taskbar_border']});
            border-top: 1px solid {c['taskbar_border']};
        }}
        QPushButton, QToolButton {{
            color: {c['text']};
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {c['surface_hover']}, stop:1 {c['surface_alt']});
            border: 1px solid {c['border_strong']};
            border-radius: 6px;
            padding: 5px 10px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {c['surface']};
            border-color: {c['accent']};
        }}
        QPushButton:focus, QToolButton:focus {{
            border: 2px solid {c['focus']};
            background: {c['surface_hover']};
        }}
        QPushButton#startButton {{
            color: {c['accent_text']};
            font-weight: 700;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {c['accent_hover']}, stop:1 {c['accent']});
            border: 1px solid {c['accent']};
        }}
        QPushButton#startButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {NEON}, stop:1 {c['accent_hover']});
            border-color: {NEON};
        }}
        QToolButton:checked {{
            background: {c['accent_soft']};
            border-color: {c['accent']};
            border-bottom: 2px solid {NEON};
        }}
        QLabel#clock {{
            background: {c['clock_bg']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 3px 8px;
            color: {c['text']};
            font-weight: 600;
        }}
        QMenu {{
            background: {c['menu_bg']};
            border: 1px solid {c['menu_border']};
            border-radius: 8px;
            padding: 6px;
        }}
        QMenu::item {{ padding: 8px 30px 8px 14px; border-radius: 5px; }}
        QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
    """


def notification_stylesheet(theme: str = "light") -> str:
    """Return the Qt stylesheet for notification popups."""
    c = palette(theme)
    return f"""
        QWidget#notificationPopup {{
            background: {c['menu_bg']};
            color: {c['text']};
            border: 1px solid {c['menu_border']};
            border-left: 4px solid {NEON};
            border-radius: 10px;
        }}
        QLabel#appName {{
            color: {c['heading']};
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 1px;
        }}
        QLabel#title {{ font-weight: 700; font-size: 14px; }}
        QPushButton {{
            background: {c['surface_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 4px 10px;
        }}
        QPushButton:hover, QPushButton:focus {{
            background: {c['accent_soft']};
            border: 1px solid {c['accent']};
        }}
    """
