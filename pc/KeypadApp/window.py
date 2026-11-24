"""Main window: minimal status bar, device preview, key inspector, application
library and optional terminal. Serial/audio/simulation logic stays intact."""

import os
import threading
import time

from PySide6.QtCore import (QEasingCurve, QObject, QPropertyAnimation, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QFormLayout, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QSizePolicy, QSpinBox, QTextEdit,
                               QStackedWidget, QVBoxLayout, QWidget)

import actions
import config
import protocol
import telemetry
import theme
from app_library import AppLibraryPanel, icon_for_exe
from serial_link import SerialLink
from widgets import (AudioPill, DisplayPreview, EncoderCard, HotkeyEdit,
                     KeypadGrid, SegmentedControl, StatusPill, prettify)
from wallpaper_panel import WallpaperPanel
import wallpapers

try:
    import audio
except Exception as _audio_exc:  # noqa: BLE001 - GUI must work without audio
    audio = None
    _AUDIO_ERROR = _audio_exc
else:
    _AUDIO_ERROR = None

ACTION_TYPES = [
    ("none", "None"),
    ("app", "Launch app"),
    ("hotkey", "Hotkey"),
    ("media", "Media key"),
    ("text", "Text macro"),
    ("page", "Switch page"),
]
MEDIA_CODES = ["play_pause", "stop", "next", "prev", "vol_up", "vol_down", "mute"]
ACTION_GLYPHS = {
    "app": "\u25a3",
    "hotkey": "",
    "media": "\u266b",
    "text": "Aa",
    "page": "\u21c4",
    "none": "",
}
ACCENT_PRESETS = ["#A855F7", "#22D3EE", "#10B981", "#F59E0B", "#F43F5E", "#E4E4E7"]

LOG_COLORS = {
    "in": "#C4B5FD",
    "out": "#67E8F9",
    "act": "#F0ABFC",
    "sys": "#71717A",
    "sim": "#A78BFA",
}


class Bridge(QObject):
    line = Signal(str)
    status = Signal(str)


class AudioBridge(QObject):
    ready = Signal(object)


class ActionContext:
    """Adapter handed to actions.execute_action()."""

    def __init__(self, window):
        self.window = window

    @property
    def dry_run(self):
        return self.window.dry_run

    def log(self, message):
        self.window.log("act", message)

    def send(self, line):
        return self.window.send_line(line)

    def set_page(self, page):
        self.window.switch_page(page)


class KeyInspector(QFrame):
    """Right-hand editor for the selected key (no profile/accent rows)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._loading = False
        self._profile = None
        self._char = "1"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.char_chip = QLabel("1")
        self.char_chip.setFixedSize(30, 26)
        self.char_chip.setAlignment(Qt.AlignCenter)
        header.addWidget(self.char_chip)
        self.summary_label = QLabel("---")
        self.summary_label.setStyleSheet("color: %s; font-size: 12px;" % theme.MUTED)
        self.summary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.summary_label.setMinimumWidth(0)
        self.summary_label.setWordWrap(False)
        self._summary_full = "---"
        header.addWidget(self.summary_label, 1)
        layout.addLayout(header)

        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(10)
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.type_combo = QComboBox()
        for value, label in ACTION_TYPES:
            self.type_combo.addItem(label, value)
        self.form.addRow(self._field_label("Action"), self.type_combo)

        self._rows = {}

        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("spotify: nebo C:/Apps/app.exe")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["auto", "always", "focus"])
        self.process_edit = QLineEdit()
        self.process_edit.setPlaceholderText("Spotify.exe (nepovinne)")
        self._add_row("app", "Target", self.target_edit)
        self._add_row("app", "Mode", self.mode_combo)
        self._add_row("app", "Process", self.process_edit)

        self.hotkey_edit = HotkeyEdit()
        self._add_row("hotkey", "Combo", self.hotkey_edit)

        self.media_combo = QComboBox()
        for code in MEDIA_CODES:
            self.media_combo.addItem(code.replace("_", " "), code)
        self._add_row("media", "Media", self.media_combo)

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("text to type")
        self._add_row("text", "Text", self.text_edit)

        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, protocol.PROFILE_COUNT)
        self._add_row("page", "Page", self.page_spin)

        layout.addLayout(self.form)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("ghost")
        clear_btn.clicked.connect(self._clear)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("accent")
        apply_btn.clicked.connect(self._apply)
        buttons.addWidget(apply_btn)
        layout.addLayout(buttons)

        self.type_combo.currentIndexChanged.connect(self._sync_fields)
        self.refresh_theme()
        self._sync_fields()

    def refresh_theme(self):
        accent = QColor(theme.ACCENT)
        self.char_chip.setStyleSheet(
            "background: rgba(%d, %d, %d, 46);"
            " border: 1px solid rgba(%d, %d, %d, 128); border-radius: 8px;"
            " color: %s; font-family: '%s'; font-weight: 700;"
            % (accent.red(), accent.green(), accent.blue(),
               accent.red(), accent.green(), accent.blue(),
               theme.ACCENT_SOFT, theme.MONO)
        )

    def _field_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("color: %s; font-size: 11px;" % theme.MUTED)
        label.setMinimumWidth(58)
        return label

    def _add_row(self, kind, text, widget):
        label = self._field_label(text)
        self.form.addRow(label, widget)
        self._rows.setdefault(kind, []).append((label, widget))

    def _sync_fields(self):
        kind = self.type_combo.currentData()
        for row_kind, rows in self._rows.items():
            visible = row_kind == kind
            for label, widget in rows:
                label.setVisible(visible)
                widget.setVisible(visible)

    def load(self, profile, char):
        self._loading = True
        self._profile = profile
        self._char = char
        self.char_chip.setText(char)
        action = profile["keys"].get(char, {"type": "none"})
        kind = action.get("type", "none")
        index = self.type_combo.findData(kind)
        self.type_combo.setCurrentIndex(index if index >= 0 else 0)
        self.target_edit.setText(action.get("target", ""))
        self.mode_combo.setCurrentText(action.get("mode", "auto"))
        self.process_edit.setText(action.get("process", ""))
        if self.hotkey_edit.recording:
            self.hotkey_edit.stop_recording()
        self.hotkey_edit.setText(action.get("combo", ""))
        media_index = self.media_combo.findData(action.get("code", ""))
        if media_index >= 0:
            self.media_combo.setCurrentIndex(media_index)
        self.text_edit.setText(action.get("text", ""))
        try:
            self.page_spin.setValue(int(action.get("page", 1)))
        except (TypeError, ValueError):
            self.page_spin.setValue(1)
        self._summary_full = prettify(config.action_summary(action))
        self._apply_elide()
        self._sync_fields()
        self._loading = False

    def _apply_elide(self):
        width = max(0, self.summary_label.width() - 4)
        if width <= 0:
            self.summary_label.setText(self._summary_full)
            return
        elided = self.summary_label.fontMetrics().elidedText(
            self._summary_full, Qt.ElideRight, width)
        self.summary_label.setText(elided)
        self.summary_label.setToolTip(self._summary_full)

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply_elide()

    def _build_action(self):
        kind = self.type_combo.currentData()
        if kind == "app":
            action = {
                "type": "app",
                "target": self.target_edit.text().strip(),
                "mode": self.mode_combo.currentText(),
            }
            process = self.process_edit.text().strip()
            if process:
                action["process"] = process
        elif kind == "hotkey":
            action = {"type": "hotkey", "combo": self.hotkey_edit.text().strip()}
        elif kind == "media":
            action = {"type": "media", "code": self.media_combo.currentData()}
        elif kind == "text":
            action = {"type": "text", "text": self.text_edit.text()}
        elif kind == "page":
            action = {"type": "page", "page": self.page_spin.value()}
        else:
            return {"type": "none"}
        return action

    def _apply(self):
        if self._profile is None:
            return
        action = self._build_action()
        previous = self._profile["keys"].get(self._char) or {}
        if previous.get("label") and previous.get("type") == action.get("type"):
            action["label"] = previous["label"]
        self._profile["keys"][self._char] = action
        self.changed.emit()

    def _clear(self):
        if self._profile is None:
            return
        self._profile["keys"][self._char] = {"type": "none"}
        self.changed.emit()


class SettingsDialog(QDialog):
    """Connection, modes, audio target, global accent and terminal toggle."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Nastaven\u00ed")
        self.setModal(True)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        conn_card, conn = self._section("P\u0159ipojen\u00ed")
        port_row = QHBoxLayout()
        port_row.setSpacing(6)
        self.port_combo = QComboBox()
        self.port_combo.addItems(SerialLink.available_ports())
        if window.current_port and self.port_combo.findText(window.current_port) < 0:
            self.port_combo.addItem(window.current_port)
        if window.current_port:
            self.port_combo.setCurrentText(window.current_port)
        port_row.addWidget(self.port_combo, 1)
        refresh = QPushButton("\u27f3")
        refresh.setObjectName("mini")
        refresh.setFixedSize(32, 30)
        refresh.setToolTip("Obnovit porty")
        refresh.clicked.connect(self._refresh_ports)
        port_row.addWidget(refresh)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])
        self.baud_combo.setEditable(True)
        self.baud_combo.setFixedWidth(100)
        self.baud_combo.setCurrentText(str(window.current_baud))
        port_row.addWidget(self.baud_combo)
        conn.addLayout(port_row)

        self.connect_btn = QPushButton("Disconnect" if window._link_active else "Connect")
        self.connect_btn.setObjectName("accent")
        self.connect_btn.clicked.connect(self._toggle_connect)
        conn.addWidget(self.connect_btn)
        layout.addWidget(conn_card)

        modes_card, modes = self._section("Re\u017eimy")
        self.sim_check = QCheckBox("Simulace enkod\u00e9r\u016f (bez HW)")
        self.sim_check.setChecked(window.sim_enabled)
        self.sim_check.stateChanged.connect(
            lambda state: window.set_sim_enabled(bool(state)))
        modes.addWidget(self.sim_check)
        self.dry_check = QCheckBox("Dry run (akce pouze do logu)")
        self.dry_check.setChecked(window.dry_run)
        self.dry_check.stateChanged.connect(
            lambda state: window.set_dry_run(bool(state)))
        modes.addWidget(self.dry_check)
        self.terminal_check = QCheckBox("Zobrazit termin\u00e1l / log")
        self.terminal_check.setChecked(window.cfg.get("terminal_visible", False))
        self.terminal_check.stateChanged.connect(
            lambda state: window.set_terminal_visible(bool(state)))
        modes.addWidget(self.terminal_check)
        self.telemetry_check = QCheckBox(
            "Odes\u00edlat telemetrii (CPU/GPU/RAM) do za\u0159\u00edzen\u00ed")
        self.telemetry_check.setChecked(
            window.cfg.get("telemetry_enabled", True))
        self.telemetry_check.stateChanged.connect(
            lambda state: window.set_telemetry_enabled(bool(state)))
        modes.addWidget(self.telemetry_check)
        self.unknown_check = QCheckBox(
            "Zobrazit nezn\u00e1m\u00e9 a syst\u00e9mov\u00e9 zdroje")
        self.unknown_check.setChecked(
            window.cfg.get("show_unknown_sources", False))
        self.unknown_check.stateChanged.connect(
            lambda state: window.set_show_unknown_sources(bool(state)))
        modes.addWidget(self.unknown_check)
        layout.addWidget(modes_card)

        audio_card, audio_box = self._section("Audio")
        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        audio_row.addWidget(QLabel("C\u00edlov\u00e1 aplikace"))
        self.audio_combo = QComboBox()
        self.audio_combo.setEditable(True)
        self.audio_combo.addItems([
            "Spotify.exe", "Discord.exe", "chrome.exe", "firefox.exe", "msedge.exe",
        ])
        self.audio_combo.setCurrentText(window.cfg["audio"].get("target_app", ""))
        self.audio_combo.activated.connect(self._audio_changed)
        self.audio_combo.lineEdit().editingFinished.connect(self._audio_changed)
        audio_row.addWidget(self.audio_combo, 1)
        audio_box.addLayout(audio_row)
        layout.addWidget(audio_card)

        enc_card, enc_box = self._section("Enkod\u00e9r E2")
        enc_row = QHBoxLayout()
        enc_row.setSpacing(8)
        enc_row.addWidget(QLabel("Re\u017eim"))
        self.e2_combo = QComboBox()
        for label, value in (("Hlasitost aplikace", "app_volume"),
                             ("Scroll", "scroll"),
                             ("Zoom", "zoom"),
                             ("Vlastn\u00ed hotkey", "hotkey")):
            self.e2_combo.addItem(label, value)
        mode = ((window.cfg.get("encoder") or {})
                .get("e2_mode", "app_volume"))
        index = self.e2_combo.findData(mode)
        self.e2_combo.setCurrentIndex(max(0, index))
        self.e2_combo.activated.connect(self._e2_mode_changed)
        enc_row.addWidget(self.e2_combo, 1)
        enc_box.addLayout(enc_row)

        hk_wrap = QWidget()
        hk_row = QHBoxLayout(hk_wrap)
        hk_row.setContentsMargins(0, 0, 0, 0)
        hk_row.setSpacing(8)
        hk_row.addWidget(QLabel("Zkratka"))
        self.e2_hotkey = HotkeyEdit()
        self.e2_hotkey.setText(
            (window.cfg.get("encoder") or {}).get("e2_combo", ""))
        self.e2_hotkey.changed.connect(window.set_e2_combo)
        hk_row.addWidget(self.e2_hotkey, 1)
        enc_box.addWidget(hk_wrap)

        self.e2_hint = QLabel(
            "Hlasitost aplikace pou\u017e\u00edv\u00e1 c\u00edlovou aplikaci ze sekce Audio.")
        self.e2_hint.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        self.e2_hint.setWordWrap(True)
        enc_box.addWidget(self.e2_hint)

        self._e2_hk_wrap = hk_wrap
        hk_wrap.setVisible(self.e2_combo.currentData() == "hotkey")
        self.e2_hint.setVisible(self.e2_combo.currentData() == "app_volume")
        layout.addWidget(enc_card)

        theme_card, theme_box = self._section("Barva akcentu")
        presets = QHBoxLayout()
        presets.setSpacing(8)
        for color in ACCENT_PRESETS:
            button = QPushButton()
            button.setFixedSize(26, 26)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(color)
            button.setStyleSheet(
                "QPushButton { background: %s; border: 1px solid rgba(255,255,255,0.25);"
                " border-radius: 8px; }"
                " QPushButton:hover { border: 1px solid #FFFFFF; }" % color
            )
            button.clicked.connect(lambda _=False, c=color: window.set_accent(c))
            presets.addWidget(button)
        presets.addStretch(1)
        custom = QPushButton("Vlastn\u00ed\u2026")
        custom.clicked.connect(self._pick_color)
        presets.addWidget(custom)
        reset = QPushButton("Reset")
        reset.setObjectName("ghost")
        reset.clicked.connect(lambda: window.set_accent(theme.DEFAULT_ACCENT))
        presets.addWidget(reset)
        theme_box.addLayout(presets)
        layout.addWidget(theme_card)

        layout.addStretch(1)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Zav\u0159\u00edt")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _section(self, title):
        card = QFrame()
        card.setObjectName("inner")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 12)
        box.setSpacing(8)
        box.addWidget(QLabel(theme.caption(title)))
        return card, box

    def _refresh_ports(self):
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItems(SerialLink.available_ports())
        if current:
            self.port_combo.setCurrentText(current)

    def _toggle_connect(self):
        try:
            baud = int(self.baud_combo.currentText())
        except ValueError:
            baud = config.DEFAULT_BAUD
        self.window.set_link(self.port_combo.currentText().strip(), baud)
        self.window.toggle_connection()
        self.connect_btn.setText(
            "Disconnect" if self.window._link_active else "Connect")

    def _audio_changed(self, _index=None):
        self.window.set_audio_app(self.audio_combo.currentText().strip())

    def _e2_mode_changed(self, _index=None):
        mode = self.e2_combo.currentData()
        self.window.set_e2_mode(mode)
        self._e2_hk_wrap.setVisible(mode == "hotkey")
        self.e2_hint.setVisible(mode == "app_volume")

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(theme.ACCENT), self, "Barva akcentu")
        if color.isValid():
            self.window.set_accent(color.name())


class HotkeyDialog(QDialog):
    """Interactive capture dialog used by the System tab 'Hotkey…' entry."""

    def __init__(self, parent=None, initial=""):
        super().__init__(parent)
        self.setWindowTitle("Hotkey")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel(theme.caption("Zachytit zkratku")))
        self.edit = HotkeyEdit()
        if initial:
            self.edit.setText(initial)
        layout.addWidget(self.edit)

        hint = QLabel("Klik do pole zah\u00e1j\u00ed nahr\u00e1v\u00e1n\u00ed \u00b7"
                      " Esc zru\u0161\u00ed \u00b7 kl\u00e1vy se b\u011bhem"
                      " nahr\u00e1v\u00e1n\u00ed nespou\u0161t\u011bj\u00ed")
        hint.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Zru\u0161it")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Ulo\u017eit")
        save.setObjectName("accent")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        self._save_btn = save
        row.addWidget(cancel)
        row.addWidget(save)
        layout.addLayout(row)

    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not self.edit.text():
            QTimer.singleShot(0, self.edit.start_recording)

    def combo(self):
        return self.edit.text().strip()


class MainWindow(QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.setWindowTitle("KeypadApp \u2014 Macro Pad Console")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 760)

        self.cfg = config.load_config()
        theme.set_accent(self.cfg.get("accent", theme.DEFAULT_ACCENT))

        self.current_profile = 0
        self.selected_key = "1"
        self.last_enc1 = None
        self.last_enc2 = None
        self.sim_enabled = True
        self.dry_run = bool(getattr(args, "dry_run", False))
        self._link_active = False
        self.current_port = ""
        self.current_baud = int(self.cfg.get("baud", config.DEFAULT_BAUD))
        self._ctx = ActionContext(self)

        self.bridge = Bridge()
        self.bridge.line.connect(self.on_device_line)
        self.bridge.status.connect(self.on_status)
        self.serial = SerialLink(self.bridge.line.emit, self.bridge.status.emit)

        self._build_ui()
        self._restore_wallpaper()

        port = getattr(args, "port", None) or self._auto_port()
        baud = getattr(args, "baud", None) or self.cfg.get("baud", config.DEFAULT_BAUD)
        self.current_port = port or ""
        self.current_baud = int(baud)

        self._audio_snapshot = None
        self._pending_vol1 = 0
        self._pending_vol2 = 0
        self._vol_flush_scheduled = False

        self._refresh_bindings()
        self._refresh_audio_label()
        self._update_e2_title()

        # Audio state is polled off the GUI thread; the UI only ever reads the
        # cached snapshot, so encoder bursts and repaints never block on COM.
        self._audio_bridge = AudioBridge()
        self._audio_bridge.ready.connect(self._on_audio_snapshot)
        self._audio_stop = threading.Event()
        self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._audio_thread.start()

        # CPU/GPU/RAM sampler: 1 Hz worker thread, GUI reads only the snapshot.
        self._telemetry_stop = threading.Event()
        self._telemetry_bridge = telemetry.start(self._telemetry_stop)
        self._telemetry_bridge.ready.connect(self._on_telemetry)

        self.log("sys", "KeypadApp ready")
        if port:
            self.toggle_connection()

        if getattr(args, "demo", False):
            self.set_dry_run(True)
            self._demo_step = 0
            self._demo_timer = QTimer(self)
            self._demo_timer.timeout.connect(self._demo_tick)
            self._demo_timer.start(900)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        layout.addLayout(self._build_status_row())

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(16)
        content.addWidget(self._build_device_panel(), 3)
        content.addWidget(self._build_right_column(), 2)
        layout.addLayout(content, 1)

    def _build_status_row(self):
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(10)

        self.brand_label = QLabel()
        row.addWidget(self.brand_label, 0, Qt.AlignVCenter)
        self._refresh_brand()
        subtitle = QLabel(theme.caption("Macro pad console"))
        row.addWidget(subtitle, 0, Qt.AlignVCenter)
        row.addStretch(1)

        self.audio_pill = AudioPill()
        row.addWidget(self.audio_pill, 0, Qt.AlignVCenter)

        self.status_pill = StatusPill()
        row.addWidget(self.status_pill, 0, Qt.AlignVCenter)

        self.settings_btn = QPushButton("\u2699")
        self.settings_btn.setObjectName("mini")
        self.settings_btn.setFixedSize(32, 32)
        self.settings_btn.setToolTip("Nastaven\u00ed")
        self.settings_btn.setStyleSheet(
            "QPushButton { font-family: 'Segoe UI Symbol'; font-size: 17px; }")
        self.settings_btn.clicked.connect(self._open_settings)
        row.addWidget(self.settings_btn, 0, Qt.AlignVCenter)
        return row

    def _build_device_panel(self):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(QLabel(theme.caption("Device preview")))
        self.left_seg = SegmentedControl()
        self.left_seg.set_names(["Za\u0159\u00edzen\u00ed", "Tapety"], 0)
        self.left_seg.changed.connect(self._switch_left_tab)
        head.addWidget(self.left_seg)
        head.addStretch(1)
        self.key_count_label = QLabel("")
        self.key_count_label.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        head.addWidget(self.key_count_label)
        layout.addLayout(head)

        self.left_stack = QStackedWidget()

        # Page 1 – device preview (display + keypad + encoders)
        device_page = QWidget()
        device_layout = QVBoxLayout(device_page)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(14)

        # Display preview – wide panel, expands horizontally, keeps aspect ratio
        self.display_preview = DisplayPreview()
        device_layout.addWidget(self.display_preview, 1)

        # Keypad grid – fills remaining width, buttons stretch
        self.keypad = KeypadGrid()
        self.keypad.key_pressed.connect(self._sim_key)
        self.keypad.entry_dropped.connect(self._entry_dropped)
        device_layout.addWidget(self.keypad, 1)

        # Encoders row at bottom – 50 % / 50 % across full width
        encoders = QHBoxLayout()
        encoders.setSpacing(10)
        self.enc1_widget = EncoderCard(1, "E1 • master")
        self.enc2_widget = EncoderCard(2, "E2 • aplikace")
        self.enc1_widget.step.connect(lambda d: self._sim_encoder(1, d))
        self.enc2_widget.step.connect(lambda d: self._sim_encoder(2, d))
        self.enc1_widget.switch.connect(lambda: self._sim_encoder_switch(1))
        self.enc2_widget.switch.connect(lambda: self._sim_encoder_switch(2))
        encoders.addWidget(self.enc1_widget, 1)
        encoders.addWidget(self.enc2_widget, 1)
        device_layout.addLayout(encoders)
        self.left_stack.addWidget(device_page)

        # Page 2 – wallpaper gallery
        self.wallpaper_panel = WallpaperPanel()
        self.wallpaper_panel.applied.connect(self._apply_wallpaper)
        self.left_stack.addWidget(self.wallpaper_panel)

        layout.addWidget(self.left_stack, 1)

        self._fade_cover = QWidget(self.left_stack)
        self._fade_cover.setStyleSheet(
            "background: #0F0F14; border-radius: 10px;")
        self._fade_effect = QGraphicsOpacityEffect(self._fade_cover)
        self._fade_cover.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(180)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.finished.connect(self._fade_cover.hide)
        self._fade_cover.hide()

        card.setMinimumWidth(520)
        return card

    def _switch_left_tab(self, index):
        if self.left_stack.currentIndex() == index:
            return
        self.left_stack.setCurrentIndex(index)
        if index == 1:
            self.wallpaper_panel.refresh()
        self._fade_cover.setGeometry(self.left_stack.rect())
        self._fade_cover.raise_()
        self._fade_cover.show()
        self._fade_anim.stop()
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    # ------------------------------------------------------------ wallpapers
    def _restore_wallpaper(self):
        selected = ((self.cfg.get("wallpaper") or {}).get("selected") or "")
        if not selected:
            return
        for folder in (wallpapers.BUILTIN_DIR, wallpapers.CUSTOM_DIR):
            path = folder / ("%s.png" % selected)
            if path.exists():
                self.display_preview.set_wallpaper(QPixmap(str(path)))
                return

    def _apply_wallpaper(self, entry, raw):
        self.cfg.setdefault("wallpaper", {})["selected"] = entry.get("id", "")
        config.save_config(self.cfg)
        self.display_preview.set_wallpaper(QPixmap(str(entry["path"])))
        self.wallpaper_panel.set_status(
            "Pozad\u00ed nastaveno \u00b7 %d B RGB565" % len(raw))
        self.log("sys", "wallpaper %s applied (%d B RGB565)"
                 % (entry.get("id"), len(raw)))
        self._send_wallpaper(raw)

    def _send_wallpaper(self, raw):
        if getattr(self, "_wp_timer", None) is not None and self._wp_timer.isActive():
            self._wp_timer.stop()
        self._wp_lines = iter(protocol.wallpaper_transfer_lines(raw))
        self._wp_sent = 0
        self._wp_timer = QTimer(self)
        self._wp_timer.setInterval(30)
        self._wp_timer.timeout.connect(self._wp_pump)
        self._wp_timer.start()
        self.wallpaper_panel.set_status("Odes\u00edl\u00e1m na za\u0159\u00edzen\u00ed\u2026")

    def _wp_pump(self):
        try:
            for _ in range(16):
                line = next(self._wp_lines)
                if not self.serial.send(line):
                    self._wp_timer.stop()
                    self.wallpaper_panel.set_status(
                        "Za\u0159\u00edzen\u00ed nen\u00ed p\u0159ipojeno \u00b7"
                        " data p\u0159ipravena (.bin)")
                    self.log("sys", "wallpaper transfer: not connected")
                    return
                self._wp_sent += 1
        except StopIteration:
            self._wp_timer.stop()
            self.wallpaper_panel.set_status(
                "Odesl\u00e1no na za\u0159\u00edzen\u00ed \u00b7 %d datov\u00fdch"
                " d\u00e1vek" % self._wp_sent)
            self.log("sys", "wallpaper transferred (%d chunks)" % self._wp_sent)

    def _build_right_column(self):
        column = QWidget()
        column.setMinimumWidth(400)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.library = AppLibraryPanel()
        self.library.set_show_unknown(
            self.cfg.get("show_unknown_sources", False), rescan=False)
        self.library.assign_requested.connect(self._assign_entry)
        self.library.scan_finished.connect(self._refresh_bindings)
        layout.addWidget(self.library, 1)

        # Profilové přepínače
        self.segment = SegmentedControl()
        self.segment.changed.connect(self._on_profile_changed)
        self.segment.add_requested.connect(self._add_profile)
        layout.addWidget(self.segment, 0)

        # Karta editoru vybrané klávesy
        self.inspector = KeyInspector()
        self.inspector.changed.connect(self._on_config_changed)
        self.inspector.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.inspector, 0)

        # Dynamický terminál (pokud je zapnutý v Nastavení)
        self.terminal_card = self._build_terminal()
        self.terminal_card.setMaximumHeight(190)
        self.terminal_card.setVisible(bool(self.cfg.get("terminal_visible", False)))
        layout.addWidget(self.terminal_card, 0)

        self._sync_min_height()
        return column

    def _sync_min_height(self):
        shown = not self.terminal_card.isHidden()
        self.setMinimumHeight(840 if shown else 760)

    def _build_terminal(self):
        card = QFrame()
        card.setObjectName("terminal")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(5)
        for color in (theme.ERR, theme.WARN, theme.OK):
            dot = QLabel("\u25cf")
            dot.setStyleSheet("color: %s; font-size: 9px;" % color)
            head.addWidget(dot)
        head.addSpacing(6)
        head.addWidget(QLabel(theme.caption("Terminal")))
        head.addStretch(1)
        layout.addLayout(head)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(4000)
        self.log_view.setFrameShape(QFrame.NoFrame)
        self.log_view.setMinimumHeight(110)
        layout.addWidget(self.log_view, 1)

        bar = QFrame()
        bar.setObjectName("commandBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 4, 4, 4)
        bar_layout.setSpacing(6)

        prompt = QLabel("\u203a")
        prompt.setStyleSheet(theme.mono_style(15, theme.ACCENT_SOFT, bold=True))
        bar_layout.addWidget(prompt, 0, Qt.AlignVCenter)

        self.send_edit = QLineEdit()
        self.send_edit.setPlaceholderText(
            "raw prikaz do zarizeni, napr. H:hello nebo P:2")
        self.send_edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; padding: 2px 0; }"
        )
        self.send_edit.returnPressed.connect(self._send_raw)
        bar_layout.addWidget(self.send_edit, 1)

        send_btn = QPushButton("Send")
        send_btn.setObjectName("accent")
        send_btn.setFixedSize(58, 26)
        send_btn.clicked.connect(self._send_raw)
        bar_layout.addWidget(send_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("ghost")
        clear_btn.setFixedSize(56, 26)
        clear_btn.clicked.connect(self.log_view.clear)
        bar_layout.addWidget(clear_btn)

        layout.addWidget(bar)
        return card

    # ------------------------------------------------------------- helpers
    def log(self, channel, message):
        if not hasattr(self, "log_view"):
            return
        stamp = time.strftime("%H:%M:%S")
        color = LOG_COLORS.get(channel, theme.MUTED)
        safe = (str(message).replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))
        self.log_view.append(
            '<span style="color:#52525B; font-family:\'%s\';">%s</span>'
            '&nbsp;&nbsp;<span style="color:%s; font-weight:600;">%s</span>'
            '&nbsp;&nbsp;<span style="color:#D4D4D8;">%s</span>'
            % (theme.MONO, stamp, color, channel.upper(), safe)
        )
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def send_line(self, line):
        if self.serial.send(line):
            self.log("out", line.strip())
            return True
        self.log("sys", "not connected, dropped: %s" % line.strip())
        return False

    def _auto_port(self):
        available = SerialLink.available_ports()
        last = (self.cfg.get("last_port") or "").strip()
        if last and last in available:
            self.log("sys", "using last port %s" % last)
            return last
        detected = SerialLink.detect_port()
        if detected:
            self.log("sys", "auto-detected keypad on %s" % detected)
            return detected
        self.log("sys", "no device detected; pick a port in Settings")
        return ""

    def _open_settings(self):
        SettingsDialog(self).exec()

    # ------------------------------------------------------------- state
    def set_sim_enabled(self, value):
        self.sim_enabled = bool(value)

    def set_dry_run(self, value):
        self.dry_run = bool(value)

    def set_show_unknown_sources(self, value):
        self.cfg["show_unknown_sources"] = bool(value)
        config.save_config(self.cfg)
        self.library.set_show_unknown(bool(value))

    def set_terminal_visible(self, visible):
        self.terminal_card.setVisible(bool(visible))
        self._sync_min_height()
        # Uprav výšku knihovny, aby se tři bloky (knihovna, editor + terminál) vešly do okna
        if bool(visible):
            self.library.setMaximumHeight(350)
        else:
            self.library.setMaximumHeight(800)
        self.cfg["terminal_visible"] = bool(visible)
        config.save_config(self.cfg)

    def set_telemetry_enabled(self, value):
        self.cfg["telemetry_enabled"] = bool(value)
        config.save_config(self.cfg)

    def set_accent(self, color):
        final = theme.set_accent(color)
        self.cfg["accent"] = final
        config.save_config(self.cfg)
        for button in self.keypad.buttons.values():
            button.update()
        self.display_preview.refresh_theme()
        self.enc1_widget.bar.update()
        self.enc2_widget.bar.update()
        self.inspector.refresh_theme()
        self._refresh_brand()
        self.send_line(protocol.cmd_accent(final))

    def _refresh_brand(self):
        self.brand_label.setText(
            '<span style="color:%s; font-size:15px;">\u25cf</span>'
            '&nbsp;<span style="color:%s; font-size:13px; font-weight:700;">'
            'KeypadApp</span>' % (theme.ACCENT, theme.TEXT))

    def set_audio_app(self, target):
        if target == self.cfg["audio"].get("target_app"):
            return
        self.cfg["audio"]["target_app"] = target
        config.save_config(self.cfg)
        self._update_audio_cache(app=None, app_muted=False)

    def set_link(self, port, baud):
        self.current_port = port or ""
        self.current_baud = int(baud)

    # ---------------------------------------------------------- device IO
    def toggle_connection(self):
        if self._link_active:
            self.serial.stop()
            self._link_active = False
            self.status_pill.set_status("offline", "offline")
            self.display_preview.set_live(False)
            self.log("sys", "disconnected")
            return
        if not self.current_port:
            QMessageBox.warning(self, "KeypadApp",
                                "Vyber port v Nastaven\u00ed.")
            return
        self._link_active = True
        self.status_pill.set_status("connecting\u2026", "connecting")
        self.serial.start(self.current_port, self.current_baud)
        self.log("sys", "connecting %s @ %d" % (self.current_port, self.current_baud))

    def _send_raw(self):
        text = self.send_edit.text().strip()
        if not text:
            return
        self.send_line(text + "\n")
        self.send_edit.clear()

    def on_status(self, message):
        self.log("sys", message)
        if message.startswith("connected "):
            parts = message.split()
            port = parts[1] if len(parts) > 1 else "?"
            baud = parts[3] if len(parts) > 3 else ""
            self.status_pill.set_status("%s \u00b7 %s" % (port, baud), "live")
            self.display_preview.set_live(True)
            if self.cfg.get("last_port") != port:
                self.cfg["last_port"] = port
                config.save_config(self.cfg)
            self._push_page_names()
            self._refresh_audio_label()
            self.send_line(protocol.cmd_accent(theme.ACCENT))
        elif message.startswith("disconnected") or message.startswith("error") \
                or "failed" in message:
            self.status_pill.set_status("offline", "offline")
            self.display_preview.set_live(False)

    def on_device_line(self, line):
        self.log("in", line)
        kind, payload = protocol.parse_line(line)
        if kind == "key":
            self.handle_key(payload["char"])
        elif kind == "enc":
            self.handle_encoder(payload["id"], payload["pos"])
        elif kind == "enc_sw":
            self.handle_encoder_switch(payload["id"])
        elif kind == "profile":
            self.select_profile(payload["index"])
        elif kind == "display":
            self.display_preview.set_pc(payload["text"][:30])

    def _push_page_names(self):
        if not self.serial.connected:
            return
        for index, profile in enumerate(self.cfg["profiles"]):
            self.send_line(protocol.cmd_page_name(index + 1, profile.get("name", "")))

    # -------------------------------------------------------------- config
    def _on_profile_changed(self, index):
        if 0 <= index < len(self.cfg["profiles"]):
            self.current_profile = index
            self._refresh_bindings()

    def _add_profile(self):
        if len(self.cfg["profiles"]) >= protocol.PROFILE_COUNT:
            self.log("sys", "max %d profily (limit firmwaru)" % protocol.PROFILE_COUNT)
            return
        taken = {p.get("name", "").upper() for p in self.cfg["profiles"]}
        name = next(("P%d" % n for n in range(1, 10) if "P%d" % n not in taken), "USER")
        profile = {"name": name[:5],
                   "keys": {ch: {"type": "none"} for ch in "123456789*0#"}}
        self.cfg["profiles"].append(profile)
        config.save_config(self.cfg)
        self.current_profile = len(self.cfg["profiles"]) - 1
        self._refresh_bindings()
        self.log("act", "profile %s added" % profile["name"])

    def _on_config_changed(self):
        config.save_config(self.cfg)
        self.log("sys", "bindings.json saved")
        self._refresh_bindings()

    def _refresh_bindings(self):
        if not self.cfg["profiles"]:
            return
        self.current_profile = min(self.current_profile, len(self.cfg["profiles"]) - 1)
        profile = self.cfg["profiles"][self.current_profile]
        names = [p.get("name", "?") for p in self.cfg["profiles"]]
        self.segment.set_names(
            names, self.current_profile,
            can_add=len(names) < protocol.PROFILE_COUNT,
        )
        self.display_preview.set_profile(profile.get("name", "?"))
        for char in self.keypad.buttons:
            action = profile["keys"].get(char, {"type": "none"})
            summary, icon, glyph = self._key_visual(action)
            self.keypad.set_summary(char, summary, icon, glyph)
        self.keypad.select(self.selected_key)
        self.inspector.load(profile, self.selected_key)
        if hasattr(self, "key_count_label"):
            self.key_count_label.setText("%d kl\u00e1ves" % len(self.keypad.buttons))

    # ------------------------------------------------------------- icons
    def _action_exe(self, action):
        exe = action.get("exe") or ""
        if exe and os.path.isfile(exe):
            return exe
        target = action.get("target") or ""
        if target.lower().endswith(".exe") and os.path.isfile(target):
            return target
        process = action.get("process") or ""
        if not process and target:
            try:
                process = actions.resolve_process(target)
            except Exception:  # noqa: BLE001
                process = ""
        process_name = os.path.basename(process or "").lower()
        if process_name:
            apps = getattr(getattr(self, "library", None), "_apps", [])
            for entry in apps:
                if os.path.basename(entry.get("exe", "")).lower() == process_name:
                    return entry["exe"]
        return ""

    def _match_library_entry(self, action, exe):
        apps = getattr(getattr(self, "library", None), "_apps", [])
        if exe:
            lowered = exe.lower()
            for entry in apps:
                if (entry.get("exe") or "").lower() == lowered:
                    return entry
        target = (action.get("target") or "").lower()
        if target:
            for entry in apps:
                entry_target = ((entry.get("action") or {}).get("target") or "")
                if entry_target.lower() == target:
                    return entry
        return None

    def _key_visual(self, action):
        kind = action.get("type", "none")
        glyph = ACTION_GLYPHS.get(kind, "")
        summary = config.get_display_label(action)
        if kind == "app":
            exe = self._action_exe(action)
            entry = self._match_library_entry(action, exe)
            if entry is not None and not action.get("label"):
                entry_name = str(entry.get("name") or "").strip()
                if entry_name:
                    summary = entry_name
            icon = None
            if entry is not None:
                if entry.get("icon"):
                    icon = QIcon(entry["icon"])
                elif entry.get("exe"):
                    icon = icon_for_exe(entry["exe"])
            if icon is None and exe:
                icon = icon_for_exe(exe)
            if icon is not None:
                return summary, icon, ""
            return summary, None, glyph
        return summary, None, glyph

    # ---------------------------------------------------------- actions
    def handle_key(self, char, source="hw"):
        self.selected_key = char
        self.keypad.select(char)
        self.keypad.flash(char)
        self.display_preview.flash_key(char)
        if source != "hw":
            self.send_line(protocol.cmd_flash(char))
        profile = self.cfg["profiles"][self.current_profile]
        self.inspector.load(profile, char)
        action = profile["keys"].get(char, {"type": "none"})
        self.log("act", "key %s -> %s" % (char, config.action_summary(action)))
        actions.execute_action(action, self._ctx)

    def handle_encoder(self, enc_id, pos):
        if enc_id == 1:
            delta = pos - self.last_enc1 if self.last_enc1 is not None else 0
            self.last_enc1 = pos
            self.enc1_widget.set_position(pos)
            if delta:
                self._queue_volume(1, delta)
            self._update_display_meters()
        else:
            delta = pos - self.last_enc2 if self.last_enc2 is not None else 0
            self.last_enc2 = pos
            self.enc2_widget.set_position(pos)
            if delta:
                if self._e2_mode() == "app_volume":
                    self._queue_volume(2, delta)
                else:
                    self._e2_step(delta)
            self._update_display_meters()

    def _queue_volume(self, enc_id, delta):
        """Coalesce encoder detents: one COM write per 20 ms burst."""
        if enc_id == 1:
            self._pending_vol1 += delta
        else:
            self._pending_vol2 += delta
        if not self._vol_flush_scheduled:
            self._vol_flush_scheduled = True
            QTimer.singleShot(20, self._flush_volumes)

    def _flush_volumes(self):
        self._vol_flush_scheduled = False
        delta1, delta2 = self._pending_vol1, self._pending_vol2
        self._pending_vol1 = 0
        self._pending_vol2 = 0
        if delta1:
            self._apply_volume(delta1)
        if delta2 and self._e2_mode() == "app_volume":
            self._apply_app_volume(delta2)

    def handle_encoder_switch(self, enc_id):
        if enc_id == 1:
            self._mute_toggle()
        else:
            mode = self._e2_mode()
            if mode == "app_volume":
                self._mute_app_toggle()
            elif mode == "hotkey":
                self._e2_hotkey_fire()
            else:
                self.log("act", "E2 switch (%s)" % mode)
        self._update_display_meters()

    # -------------------------------------------------------------- encoders
    def _encoder_cfg(self):
        enc = self.cfg.get("encoder")
        return enc if isinstance(enc, dict) else {}

    def _e2_mode(self):
        return self._encoder_cfg().get("e2_mode", "app_volume")

    def _e2_combo_value(self):
        return (self._encoder_cfg().get("e2_combo") or "").strip()

    def set_e2_mode(self, mode):
        self.cfg.setdefault("encoder", {})["e2_mode"] = mode
        self._on_config_changed()
        self._update_e2_title()
        self._update_display_meters()

    def set_e2_combo(self, combo):
        self.cfg.setdefault("encoder", {})["e2_combo"] = (combo or "").strip()
        self._on_config_changed()
        self._update_display_meters()

    def _update_e2_title(self):
        titles = {
            "app_volume": "E2 • aplikace",
            "scroll": "E2 • scroll",
            "zoom": "E2 • zoom",
            "hotkey": "E2 • hotkey",
        }
        self.enc2_widget.set_title(titles.get(self._e2_mode(), "E2"))

    def _e2_step(self, delta):
        mode = self._e2_mode()
        if mode == "app_volume":
            self._apply_app_volume(delta)
        elif mode == "scroll":
            self._send_scroll(delta, ctrl=False)
        elif mode == "zoom":
            self._send_scroll(delta, ctrl=True)
        else:
            for _ in range(max(1, abs(int(delta)))):
                self._e2_hotkey_fire()

    def _send_scroll(self, delta, ctrl=False):
        if self.dry_run:
            self.log("act", "[dry] %s %+d" % ("zoom" if ctrl else "scroll", delta))
            return
        try:
            actions.send_wheel(delta, ctrl=ctrl)
            self.log("act", "%s %+d" % ("zoom" if ctrl else "scroll", delta))
        except Exception as exc:  # noqa: BLE001
            self.log("act", "wheel failed: %s" % exc)

    def _e2_hotkey_fire(self):
        combo = self._e2_combo_value()
        if not combo:
            self.log("act", "E2 hotkey not set")
            return
        if self.dry_run:
            self.log("act", "[dry] hotkey %s" % combo)
            return
        try:
            actions.send_hotkey(combo)
            self.log("act", "E2 hotkey %s" % combo)
        except Exception as exc:  # noqa: BLE001
            self.log("act", "E2 hotkey failed: %s" % exc)

    # ----------------------------------------------------------------- audio
    def _audio_loop(self):
        while not self._audio_stop.is_set():
            try:
                target = self.cfg["audio"].get("target_app", "")
            except Exception:  # noqa: BLE001
                target = ""
            data = None
            if audio is not None:
                try:
                    data = audio.snapshot(target)
                except Exception as exc:  # noqa: BLE001
                    data = {"master": None, "master_muted": False, "app": None,
                            "app_muted": False, "app_name": target,
                            "error": str(exc)}
            if data is not None:
                self._audio_bridge.ready.emit(data)
            self._audio_stop.wait(0.5)

    def _on_audio_snapshot(self, data):
        if data == self._audio_snapshot:
            return
        self._audio_snapshot = data
        self._apply_audio_view()

    def _on_telemetry(self, data):
        cpu, gpu, ram = data.get("cpu"), data.get("gpu"), data.get("ram")
        self.display_preview.set_telemetry(cpu, gpu, ram)
        tip = []
        gpu_name = (data.get("gpu_name") or "").strip()
        if gpu_name:
            tip.append("GPU: %s" % gpu_name)
        if data.get("ram_total"):
            tip.append("RAM: %.1f / %.1f GB" % (data.get("ram_used") or 0.0,
                                                data["ram_total"]))
        self.display_preview.setToolTip("\n".join(tip) or "Telemetrie")
        if self._link_active and not self.dry_run \
                and self.cfg.get("telemetry_enabled", True) \
                and any(value is not None for value in (cpu, gpu, ram)):
            self.serial.send(protocol.cmd_telemetry(cpu=cpu, gpu=gpu, ram=ram))

    def _audio_cache(self):
        return dict(self._audio_snapshot or {
            "master": None, "master_muted": False,
            "app": None, "app_muted": False,
        })

    def _update_audio_cache(self, **values):
        cache = self._audio_cache()
        cache.update(values)
        self._audio_snapshot = cache
        self._apply_audio_view()

    def _apply_audio_view(self):
        target = self.cfg["audio"].get("target_app", "")
        cache = self._audio_cache()
        master = cache.get("master")
        master_muted = bool(cache.get("master_muted"))
        app_value = cache.get("app")
        app_muted = bool(cache.get("app_muted"))
        short = os.path.splitext(target)[0][:14] if target else "app"
        self.audio_pill.set_values(master, master_muted, short, app_value,
                                   app_muted)
        self.enc1_widget.set_values(master, master_muted, self.last_enc1 or 0)
        mode = self._e2_mode()
        if mode == "app_volume":
            self.enc2_widget.set_values(app_value, app_muted, self.last_enc2 or 0)
        else:
            self.enc2_widget.set_values(None, False, self.last_enc2 or 0)
            self.enc2_widget.state_label.setText(mode)
        self._update_display_meters()

    def _update_display_meters(self):
        cache = self._audio_cache()
        master = cache.get("master")
        if master is None:
            self.display_preview.set_meter(1, 0, "--")
        else:
            self.display_preview.set_meter(1, master, "%d%%" % master)

        mode = self._e2_mode()
        if mode == "app_volume":
            value = cache.get("app")
            if value is None:
                self.display_preview.set_meter(2, 0, "--")
            else:
                self.display_preview.set_meter(2, value, "%d%%" % value)
        elif mode in ("scroll", "zoom"):
            pos = self.last_enc2 or 0
            self.display_preview.set_meter(2, abs(pos) % 100, str(pos))
        else:
            combo = self._e2_combo_value()
            key = combo.split("+")[-1] if combo else ""
            self.display_preview.set_meter(2, 100 if combo else 0,
                                            (key or "?").upper())

    def _sim_enabled(self):
        if not self.sim_enabled:
            self.log("sys", "simulace vypnuta")
            return False
        return True

    def _sim_key(self, char):
        # Klik na kl\u00e1vesu v n\u00e1hledu = v\u00fdb\u011br + proveden\u00ed akce (Dry run \u0159e\u0161\u00ed execute_action).
        self.handle_key(char, source="sim")

    def _entry_dropped(self, char, entry):
        if char not in self.keypad.buttons:
            return
        self.selected_key = char
        self.keypad.select(char)
        self.inspector.load(self.cfg["profiles"][self.current_profile], char)
        self._assign_entry(entry)

    def _assign_entry(self, entry):
        if not isinstance(entry, dict):
            return
        action = None
        prompt = entry.get("prompt")
        if prompt == "hotkey":
            dialog = HotkeyDialog(self)
            if dialog.exec() and dialog.combo():
                action = {"type": "hotkey", "combo": dialog.combo()}
        elif prompt == "text":
            text, accepted = QInputDialog.getText(
                self, "Text macro", "Text k napsání:")
            if accepted and text:
                action = {"type": "text", "text": text}
        elif prompt == "url":
            text, accepted = QInputDialog.getText(
                self, "Open URL", "URL nebo cesta:")
            if accepted and text.strip():
                action = {"type": "app", "target": text.strip(), "mode": "auto"}
        elif entry.get("action"):
            action = dict(entry["action"])
        elif entry.get("exe"):
            exe = entry["exe"]
            action = {"type": "app", "target": exe, "mode": "auto",
                      "process": os.path.basename(exe), "exe": exe}
        if action is None:
            return
        label = str(entry.get("name") or "").strip()
        if prompt or not label:
            label = config.get_display_label(action)
        action["label"] = label
        profile = self.cfg["profiles"][self.current_profile]
        profile["keys"][self.selected_key] = action
        self._on_config_changed()
        self.log("act", "%s -> key %s" % (entry.get("name", "item"), self.selected_key))

    def _sim_encoder(self, enc_id, delta):
        if not self._sim_enabled():
            return
        if enc_id == 1:
            base = self.last_enc1 if self.last_enc1 is not None else 0
            self.handle_encoder(1, base + delta)
        else:
            base = self.last_enc2 if self.last_enc2 is not None else 0
            self.handle_encoder(2, base + delta)

    def _sim_encoder_switch(self, enc_id):
        if not self._sim_enabled():
            return
        self.handle_encoder_switch(enc_id)

    def _volume_step(self):
        try:
            return max(1, int(self.cfg["audio"].get("volume_step", 2)))
        except (TypeError, ValueError):
            return 2

    def _require_audio(self):
        if audio is None:
            self.log("act", "audio unavailable: %s" % _AUDIO_ERROR)
            return False
        return True

    def _apply_volume(self, delta):
        step = self._volume_step()
        if self.dry_run:
            self.log("act", "[dry] master volume %+d%%" % (delta * step))
            return
        if not self._require_audio():
            return
        try:
            before = self._audio_cache().get("master")
            if before is None:
                before = audio.master_percent()
            after = audio.set_master_percent(before + delta * step)
            self.log("act", "master volume %d%% (%+d%%)" % (after, after - before))
            self._update_audio_cache(master=after, master_muted=False)
        except Exception as exc:  # noqa: BLE001
            self.log("act", "volume failed: %s" % exc)

    def _apply_app_volume(self, delta):
        target = self.cfg["audio"].get("target_app", "").strip()
        if not target:
            self.log("act", "audio target app not set")
            return
        step = self._volume_step()
        if self.dry_run:
            self.log("act", "[dry] %s volume %+d%%" % (target, delta * step))
            return
        if not self._require_audio():
            return
        try:
            before = self._audio_cache().get("app")
            if before is None:
                before = audio.app_volume_percent(target)
            if before is None:
                self.log("act", "%s has no audio session" % target)
                return
            after = audio.set_app_volume_percent(target, before + delta * step)
            if after is not None:
                self.log("act", "%s volume %d%% (%+d%%)"
                         % (target, after, after - before))
                self._update_audio_cache(app=after)
        except Exception as exc:  # noqa: BLE001
            self.log("act", "%s volume failed: %s" % (target, exc))

    def _mute_toggle(self):
        if self.dry_run:
            self.log("act", "[dry] master mute toggle")
            self._refresh_audio_label()
            return
        if not self._require_audio():
            return
        try:
            muted = audio.toggle_master_mute()
            self.log("act", "master mute %s" % ("on" if muted else "off"))
            self._update_audio_cache(master_muted=muted)
        except Exception as exc:  # noqa: BLE001
            self.log("act", "mute failed: %s" % exc)

    def _mute_app_toggle(self):
        target = self.cfg["audio"].get("target_app", "").strip()
        if not target:
            self.log("act", "audio target app not set")
            return
        if self.dry_run:
            self.log("act", "[dry] %s mute toggle" % target)
            self._refresh_audio_label()
            return
        if not self._require_audio():
            return
        try:
            muted = audio.toggle_app_mute(target)
            if muted is None:
                self.log("act", "%s has no audio session" % target)
                return
            self.log("act", "%s mute %s" % (target, "on" if muted else "off"))
            self._update_audio_cache(app_muted=muted)
        except Exception as exc:  # noqa: BLE001
            self.log("act", "%s mute failed: %s" % (target, exc))

    def _refresh_audio_label(self):
        # UI reads only the cached snapshot; the poller keeps it fresh.
        self._apply_audio_view()

    def switch_page(self, page):
        count = len(self.cfg["profiles"])
        page = max(1, min(int(page), count))
        self.current_profile = page - 1
        self.segment.set_current(self.current_profile)
        self._refresh_bindings()
        self.send_line(protocol.cmd_page(page))
        self.log("act", "page %d (%s)"
                 % (page, self.cfg["profiles"][self.current_profile].get("name", "")))

    def select_profile(self, index):
        if 0 <= index < len(self.cfg["profiles"]):
            self.current_profile = index
            self.segment.set_current(index)
            self._refresh_bindings()

    def _demo_tick(self):
        """Cycle simulated events for a quick visual check (--demo)."""
        self._demo_step += 1
        step = self._demo_step
        key = "123456789*0#"[step % 12]
        self.handle_key(key, source="sim")
        if step % 3 == 0:
            self._sim_encoder(1, 1)
        if step % 5 == 0:
            self._sim_encoder(2, -1)
        if step % 7 == 0:
            self.switch_page((step // 7) % 3 + 1)
        self.display_preview.set_pc("C45 G30 52C")

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        self._audio_stop.set()
        self._telemetry_stop.set()
        self._audio_thread.join(timeout=1.5)
        self.serial.stop()
        super().closeEvent(event)
