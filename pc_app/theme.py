"""Deep-black + neon-purple theme for Fintr Deck (Fusion + QSS)."""

from PySide6.QtGui import QColor, QFont, QFontDatabase

BG = "#09090B"
SURFACE = "#111115"
SURFACE2 = "#141419"
SURFACE3 = "#0F0F13"
CONTROL = "#1C1C24"
HOVER = "#252530"
BORDER = "#27272A"
BORDER2 = "#3F3F46"
TEXT = "#F4F4F5"
MUTED = "#A1A1AA"
DIM = "#71717A"
DEFAULT_ACCENT = "#A855F7"
ACCENT = DEFAULT_ACCENT
ACCENT2 = "#9333EA"
ACCENT3 = "#8B5CF6"
ACCENT_SOFT = "#C084FC"
OK = "#10B981"
ERR = "#EF4444"
WARN = "#F59E0B"
MONO_PLACEHOLDER = "MONO"
SANS_PLACEHOLDER = "SANS"

_APP = None
SANS = "Segoe UI"
MONO = "Consolas"
QSS = ""


def _pick(candidates, fallback):
    families = set(QFontDatabase.families())
    for name in candidates:
        if name in families:
            return name
    return fallback


def _build_qss(sans, mono):
    return """
QWidget {
    color: %(text)s;
    font-family: '%(sans)s';
    font-size: 13px;
}
QMainWindow, QDialog { background: %(bg)s; }
QWidget#root { background: %(bg)s; }
QLabel { background: transparent; }
QLabel#caption {
    color: %(dim)s;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#muted { color: %(muted)s; }
QLabel#accentText { color: %(accent_soft)s; }

QFrame#header {
    background: %(surface3)s;
    border: 1px solid %(border)s;
    border-radius: 16px;
}
QFrame#card {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: 16px;
}
QFrame#terminal {
    background: #070709;
    border: 1px solid %(border)s;
    border-radius: 16px;
}
QFrame#inner {
    background: %(surface2)s;
    border: 1px solid %(border)s;
    border-radius: 10px;
}
QFrame#inner[dropHover="true"] {
    border: 1px solid %(accent)s;
}
QFrame#pill {
    background: %(surface2)s;
    border: 1px solid %(border)s;
    border-radius: 16px;
}
QFrame#commandBar {
    background: #0B0B10;
    border: 1px solid %(border)s;
    border-radius: 10px;
}
QFrame#segmented {
    background: #0F0F13;
    border: 1px solid %(border)s;
    border-radius: 11px;
}
QFrame#sep { background: %(border)s; }

QPushButton {
    background: %(control)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 6px 12px;
    color: %(text)s;
    font-weight: 500;
}
QPushButton:hover { background: %(hover)s; border-color: %(border2)s; }
QPushButton:pressed { background: #2E2E3A; }
QPushButton:disabled { color: #52525B; }
QPushButton:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %(accent3)s, stop:1 %(accent)s);
    border: 1px solid %(accent)s;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton:checked:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %(accent)s, stop:1 %(accent_soft)s);
}

QPushButton#accent {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %(accent3)s, stop:1 %(accent)s);
    border: 1px solid %(accent)s;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton#accent:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %(accent)s, stop:1 %(accent_soft)s);
}
QPushButton#accent:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %(accent2)s, stop:1 %(accent3)s);
}

QPushButton#ghost {
    background: transparent;
    border: 1px solid transparent;
    color: %(muted)s;
}
QPushButton#ghost:hover { background: %(control)s; color: %(text)s; }
QPushButton#ghost:checked {
    background: %(control)s;
    color: %(accent_soft)s;
    border: 1px solid %(accent3)s;
}

QPushButton#mini {
    background: %(control)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 0;
    color: %(muted)s;
    font-weight: 700;
    font-size: 14px;
}
QPushButton#mini:hover { background: %(hover)s; color: %(text)s; }
QPushButton#mini:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %(accent3)s, stop:1 %(accent)s);
    border-color: %(accent)s;
    color: #FFFFFF;
}

QPushButton#seg {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 16px;
    color: %(muted)s;
    font-weight: 600;
}
QPushButton#seg:hover { color: %(text)s; background: %(control)s; }
QPushButton#seg:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %(accent3)s, stop:1 %(accent)s);
    border-color: %(accent)s;
    color: #FFFFFF;
}

QLineEdit, QComboBox, QSpinBox {
    background: %(surface2)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 18px;
    selection-background-color: %(accent3)s;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: %(border2)s; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: %(accent3)s; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: %(surface2)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: %(control)s;
    selection-color: #FFFFFF;
    outline: none;
}

QListWidget#libraryList {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget#libraryList::item {
    background: transparent;
    border: none;
    padding: 0;
}

QTextEdit#log {
    background: transparent;
    border: none;
    padding: 0;
    font-family: '%(mono)s';
    font-size: 12px;
    color: %(text)s;
    selection-background-color: %(accent3)s;
}

QCheckBox { spacing: 6px; color: %(muted)s; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid %(border)s;
    border-radius: 4px;
    background: %(surface2)s;
}
QCheckBox::indicator:checked {
    background: %(accent)s;
    border-color: %(accent)s;
}

QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical {
    background: #2A2A33;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: %(border2)s; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #2A2A33; border-radius: 4px; min-width: 28px; }

QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 12px; }
QSplitter::handle:vertical { height: 12px; }

QStatusBar { background: %(surface3)s; color: %(muted)s; }

QToolTip {
    background: %(control)s;
    color: %(text)s;
    border: 1px solid %(border2)s;
    border-radius: 6px;
    padding: 6px 8px;
}
""" % {
    "bg": BG, "surface": SURFACE, "surface2": SURFACE2, "surface3": SURFACE3,
    "control": CONTROL, "hover": HOVER, "border": BORDER, "border2": BORDER2,
    "text": TEXT, "muted": MUTED, "dim": DIM, "accent": ACCENT,
    "accent2": ACCENT2, "accent3": ACCENT3, "accent_soft": ACCENT_SOFT,
    "sans": sans, "mono": mono,
}


def apply(app):
    global SANS, MONO, QSS, _APP
    _APP = app
    SANS = _pick(["Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans"],
                 "Segoe UI")
    MONO = _pick(["JetBrains Mono", "Cascadia Mono", "Cascadia Code", "Consolas"],
                 "Consolas")
    QSS = _build_qss(SANS, MONO)
    app.setStyle("Fusion")
    app.setFont(QFont(SANS, 10))
    app.setStyleSheet(QSS)


def accent_variants(color):
    """Return (base, soft, mid, deep) hex strings derived from color."""
    parsed = QColor(color)
    if not parsed.isValid():
        parsed = QColor(DEFAULT_ACCENT)
    return (
        parsed.name(),
        parsed.lighter(118).name(),
        parsed.darker(112).name(),
        parsed.darker(132).name(),
    )


def set_accent(color, repaint=True):
    """Make `color` the global accent and rebuild the stylesheet."""
    global ACCENT, ACCENT_SOFT, ACCENT3, ACCENT2, QSS
    ACCENT, ACCENT_SOFT, ACCENT3, ACCENT2 = accent_variants(color)
    QSS = _build_qss(SANS, MONO)
    if repaint and _APP is not None:
        _APP.setStyleSheet(QSS)
    return ACCENT


def caption(text):
    return '<span style="color:%s;font-size:10px;font-weight:700;">%s</span>' % (
        DIM, text.upper())


def mono_style(size=12, color=None, bold=False):
    return (
        "font-family: '%s'; font-size: %dpx; color: %s;%s"
        % (MONO, size, color or TEXT, " font-weight: 700;" if bold else "")
    )
