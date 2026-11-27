"""Keymap calibration wizard: app lights a tile, user presses the physical key.

Firmware protocol (src/main.cpp):
    PC -> "c"                start calibration (direct crosspoint scan)
    PC -> "?"                handshake (aborts stuck calibration)
    ESP32 -> "CAL:START"     calibration armed
    ESP32 -> "CAL:next:<c>"  press the printed key <c> next
    ESP32 -> "CAL:<n>:(r,c):<c>"  crosspoint (r,c) learned as key <c>
    ESP32 -> "CAL:DUPLICATE:..."  crosspoint already used
    ESP32 -> "KEYMAP:<12>"   final map stored in NVS (wizard finished)
    ESP32 -> "CAL:ABORT"     calibration cancelled (handshake/timeout)
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

import theme

PHYSICAL_ROWS = (("1", "2", "3"), ("4", "5", "6"),
                 ("7", "8", "9"), ("*", "0", "#"))


class CalibrationWizard(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.app_window = window
        self._started = False
        self._done = False
        self._current = None
        self._tiles = {}

        self.setWindowTitle("Kalibrace kl\u00e1ves")
        self.setModal(False)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "Nau\u010d appku fyzick\u00e9 rozlo\u017een\u00ed tla\u010d\u00edtek.\n"
            "Spus\u0165 kalibraci a stiskej kl\u00e1vesy v tomhle po\u0159ad\u00ed "
            "\u2013 appka je rozsv\u00edc\u00ed jedna po druh\u00e9 a firmware si "
            "mapu ulo\u017e\u00ed trvale.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: %s;" % theme.TEXT)
        layout.addWidget(intro)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(8)
        for row, line in enumerate(PHYSICAL_ROWS):
            for col, char in enumerate(line):
                tile = QLabel(char)
                tile.setAlignment(Qt.AlignCenter)
                tile.setMinimumSize(56, 56)
                tile.setStyleSheet(self._tile_style(idle=True))
                self._tiles[char] = tile
                grid.addWidget(tile, row, col)
        layout.addWidget(grid_widget, 1)

        self.status = QLabel(
            "P\u0159ipoj keypad p\u0159es COM port a klikni na "
            "„Spustit kalibraci“.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: %s; font-size: 11px;" % theme.MUTED)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Spustit kalibraci")
        self.start_btn.setObjectName("accent")
        self.start_btn.clicked.connect(self._start)
        buttons.addWidget(self.start_btn)
        buttons.addStretch(1)
        self.close_btn = QPushButton("Zav\u0159\u00edt")
        self.close_btn.setObjectName("ghost")
        self.close_btn.clicked.connect(self.close)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

    # ------------------------------------------------------------- styles
    @staticmethod
    def _tile_style(idle=False, current=False, done=False):
        base = ("QLabel { border-radius: 8px; font-size: 16px; "
                "font-weight: 700; background: rgba(255,255,255,0.04); "
                "border: 2px solid rgba(255,255,255,0.08); color: %s; }"
                % theme.MUTED)
        if current:
            return ("QLabel { border-radius: 8px; font-size: 16px; "
                    "font-weight: 700; background: %s; border: 2px solid %s; "
                    "color: #0B0B10; }" % (theme.ACCENT, theme.ACCENT))
        if done:
            return ("QLabel { border-radius: 8px; font-size: 16px; "
                    "font-weight: 700; background: rgba(16,185,129,0.15); "
                    "border: 2px solid rgba(16,185,129,0.45); "
                    "color: #10B981; }")
        return base

    # ------------------------------------------------------------- flow
    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        self.app_window.bridge.line.connect(self._on_line)

    def _start(self):
        if self._done:
            self._reset_tiles()
        self._started = True
        self.start_btn.setEnabled(False)
        self.status.setText("Kalibrace b\u011b\u017e\u00ed \u2013 "
                            "\u010dekej na sign\u00e1l z keypadu\u2026")
        self.app_window.send_line("c\n")

    def _reset_tiles(self):
        self._done = False
        self._current = None
        for tile in self._tiles.values():
            tile.setStyleSheet(self._tile_style(idle=True))

    def _on_line(self, line):
        if not self._started or self._done:
            if line.startswith("CAL:ABORT"):
                self._aborted("Kalibrace byla p\u0159eru\u0161ena.")
            return
        if line.startswith("CAL:START"):
            self.status.setText("Kalibrace aktivn\u00ed \u2013 "
                                "\u010dekej na prvn\u00ed kl\u00e1vesu.")
            return
        if line.startswith("CAL:next:"):
            char = line[9:10]
            self._highlight(char)
            self.status.setText(
                "Stiskni kl\u00e1vesu %s na fyzick\u00e9m keypadu." % char)
            return
        if line.startswith("CAL:"):
            # CAL:<n>:(r,c):<c>
            try:
                index = int(line[4:].split(":", 1)[0])
                tail = line.split(":", 2)[2]
                position, char = tail.split(":", 1)
            except (IndexError, ValueError):
                return
            tile = self._tiles.get(char)
            if tile is not None:
                tile.setStyleSheet(self._tile_style(done=True))
            self._highlight(None)
            self.status.setText(
                "Ulo\u017eeno: %d/12 \u00b7 pozice %s \u2192 kl\u00e1vesa %s"
                % (index, position, char))
            return
        if line.startswith("CAL:DUPLICATE"):
            self.status.setText(
                "Tuhle pozici u\u017e m\u00e1me \u2013 zkus jinou kl\u00e1vesu.")
            return
        if line.startswith("KEYMAP:"):
            self._finished(line[7:].strip())
            return

    def _highlight(self, char):
        if self._current and self._current in self._tiles:
            tile = self._tiles[self._current]
            if tile.styleSheet() == self._tile_style(current=True):
                tile.setStyleSheet(self._tile_style(idle=True))
        self._current = char
        if char and char in self._tiles:
            self._tiles[char].setStyleSheet(self._tile_style(current=True))

    def _finished(self, keymap):
        self._done = True
        self._started = False
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Kalibrovat znovu")
        self.status.setText(
            "Hotovo! Mapa %s ulo\u017eena ve firmware." % keymap)
        QTimer.singleShot(1600, self.close)

    def _aborted(self, message):
        self._started = False
        self._done = False
        self.start_btn.setEnabled(True)
        self.status.setText(message)

    # ------------------------------------------------------------- teardown
    def closeEvent(self, event):  # noqa: N802 - Qt naming
        try:
            self.app_window.bridge.line.disconnect(self._on_line)
        except (RuntimeError, TypeError):
            pass
        if self._started and not self._done:
            # "?" handshake cancels a stuck calibration on the device.
            self.app_window.send_line("?\n")
        super().closeEvent(event)
