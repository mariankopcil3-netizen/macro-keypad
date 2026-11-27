"""E2 encoder configuration panel (second tab of the E2 card).

Public API used by the main window:
    changed                  - Signal() emitted after any user edit
    set_state(dict)          - fill the form from cfg["encoder"]
    state() -> dict          - keys: e2_mode, e2_target, e2_combo,
                               e2_hotkey_left, e2_hotkey_right, e2_hotkey_switch
    refresh_targets(names)   - audio session names for the target combo
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QLabel, QVBoxLayout,
                               QWidget)

import theme
from widgets import HotkeyEdit

MODES = (
    ("app_volume", "Hlasitost aplikace"),
    ("scroll", "Scroll"),
    ("zoom", "Zoom"),
    ("hotkey", "Vlastn\u00ed hotkey"),
)


class E2ConfigPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(8)

        self.mode_combo = QComboBox()
        for _value, label in MODES:
            self.mode_combo.addItem(label, _value)
        layout.addWidget(self.mode_combo)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._form = form

        self.target_combo = QComboBox()
        self.target_combo.setToolTip(
            "Aplikace, kterou E2 ovl\u00e1d\u00e1 (hlasitost + mute). "
            "Mus\u00ed m\u00edt aktivn\u00ed audio session.")
        form.addRow("Aplikace", self.target_combo)

        self.hotkey_left = HotkeyEdit()
        form.addRow("Rotace vlevo", self.hotkey_left)
        self.hotkey_right = HotkeyEdit()
        form.addRow("Rotace vpravo", self.hotkey_right)
        self.hotkey_switch = HotkeyEdit()
        form.addRow("Stisk", self.hotkey_switch)
        layout.addLayout(form)
        self._fields = (self.target_combo, self.hotkey_left,
                        self.hotkey_right, self.hotkey_switch)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        layout.addStretch(1)

        self.mode_combo.currentIndexChanged.connect(self._emit_changed)
        self.target_combo.activated.connect(self._emit_changed)
        self.hotkey_left.changed.connect(lambda _t: self._emit_changed())
        self.hotkey_right.changed.connect(lambda _t: self._emit_changed())
        self.hotkey_switch.changed.connect(lambda _t: self._emit_changed())
        self._on_mode_changed()

    # ------------------------------------------------------------- state
    def set_state(self, state):
        state = state if isinstance(state, dict) else {}
        self._block = True
        try:
            mode = state.get("e2_mode", "app_volume")
            index = self.mode_combo.findData(mode)
            self.mode_combo.setCurrentIndex(max(0, index))
            self._set_combo_text(self.target_combo,
                                 (state.get("e2_target") or "").strip())
            self.hotkey_left.setText(
                (state.get("e2_hotkey_left")
                 or state.get("e2_combo") or "").strip())
            self.hotkey_right.setText(
                (state.get("e2_hotkey_right")
                 or state.get("e2_combo") or "").strip())
            self.hotkey_switch.setText(
                (state.get("e2_hotkey_switch")
                 or state.get("e2_combo") or "").strip())
            self._on_mode_changed()
        finally:
            self._block = False

    def state(self):
        return {
            "e2_mode": self.mode_combo.currentData() or "app_volume",
            "e2_target": (self.target_combo.currentText() or "").strip(),
            "e2_combo": (self.hotkey_left.text()
                         or self.hotkey_right.text()).strip(),
            "e2_hotkey_left": self.hotkey_left.text().strip(),
            "e2_hotkey_right": self.hotkey_right.text().strip(),
            "e2_hotkey_switch": self.hotkey_switch.text().strip(),
        }

    def refresh_targets(self, names):
        current = self.target_combo.currentText()
        self._block = True
        try:
            self.target_combo.clear()
            self.target_combo.addItem("")           # = follow top "Audio app"
            for name in names or []:
                self.target_combo.addItem(str(name))
            if current:
                index = self.target_combo.findText(current)
                self.target_combo.setCurrentIndex(max(0, index))
        finally:
            self._block = False

    # ------------------------------------------------------------- internals
    def _set_combo_text(self, combo, text):
        if not text:
            combo.setCurrentIndex(0)
            return
        index = combo.findText(text)
        if index < 0:
            combo.addItem(text)
            index = combo.findText(text)
        combo.setCurrentIndex(index)

    def _on_mode_changed(self):
        mode = self.mode_combo.currentData()
        is_volume = mode == "app_volume"
        is_hotkey = mode == "hotkey"
        form = getattr(self, "_form", None)
        if form is not None:
            form.setRowVisible(0, is_volume)    # "Aplikace" target combo
            form.setRowVisible(1, is_hotkey)    # rotace vlevo
            form.setRowVisible(2, is_hotkey)    # rotace vpravo
            form.setRowVisible(3, is_hotkey)    # stisk
        self.hint.setText({
            "app_volume": "E2 \u0159\u00edd\u00ed hlasitost vybran\u00e9 "
                          "aplikace; stisk = mute. Drag & drop aplikace "
                          "z knihovny na E2 kartu to nastav\u00ed tak\u00e9.",
            "scroll": "Ot\u00e1\u010den\u00ed = scrollov\u00e1n\u00ed "
                      "pod kurzorem, stisk = st\u0159edn\u00ed tla\u010d\u00edtko.",
            "zoom": "Ot\u00e1\u010den\u00ed = zoom (Ctrl + kole\u010dko), "
                    "stisk = reset zoomu.",
            "hotkey": "P\u0159i\u0159a\u017e zkratky pro ot\u00e1\u010den\u00ed "
                      "vlevo/vpravo a stisk.",
        }.get(mode, ""))

    def _emit_changed(self, _index=None):
        if getattr(self, "_block", False):
            return
        self._on_mode_changed()
        self.changed.emit()
