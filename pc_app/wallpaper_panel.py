"""Wallpaper gallery tab: thumbnails, preview, RGB565 apply/send."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QFrame, QPushButton, QVBoxLayout, QWidget)

import theme
import wallpapers
from widgets import PANEL_H, PANEL_W

THUMB_W = int(PANEL_W * 0.52)   # ~147 px, keeps the 284x76 ratio
THUMB_H = int(PANEL_H * 0.52)
PREVIEW_SCALE = 1.5

_THUMB_CACHE = {}


def _thumb(entry):
    path = entry["path"]
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        key = (str(path), 0)
    pixmap = _THUMB_CACHE.get(key)
    if pixmap is None:
        pixmap = QPixmap.fromImage(wallpapers.qimage(path)).scaled(
            THUMB_W, THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        _THUMB_CACHE[key] = pixmap
    return pixmap


class WallpaperPanel(QWidget):
    applied = Signal(object, object)  # entry dict, rgb565 bytes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        self._buttons = []
        self._selected = None
        self._raw = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(QLabel(theme.caption("Galerie tapet")))
        head.addStretch(1)
        add_btn = QPushButton("+ P\u0159idat tapetu")
        add_btn.setObjectName("mini")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_custom)
        head.addWidget(add_btn)
        layout.addLayout(head)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        layout.addWidget(self.grid_host)

        preview_card = QFrame()
        preview_card.setObjectName("inner")
        preview_box = QVBoxLayout(preview_card)
        preview_box.setContentsMargins(10, 8, 10, 10)
        preview_box.setSpacing(8)

        self.preview = QLabel("Vyber tapetu z galerie")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(114)
        self.preview.setStyleSheet(
            "color: %s; font-size: 11px; border: 1px dashed #2A2A33;"
            " border-radius: 8px; background: #101017;" % theme.DIM)
        preview_box.addWidget(self.preview)

        info_row = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        info_row.addWidget(self.info)
        info_row.addStretch(1)
        self.apply_btn = QPushButton("Pou\u017e\u00edt na displej")
        self.apply_btn.setObjectName("accent")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)
        info_row.addWidget(self.apply_btn)
        preview_box.addLayout(info_row)

        self.status = QLabel("")
        self.status.setStyleSheet("color: %s; font-size: 10px;" % theme.MUTED)
        preview_box.addWidget(self.status)

        layout.addWidget(preview_card)
        layout.addStretch(1)

        self.refresh()

    # ----------------------------------------------------------------- data
    def refresh(self):
        self._entries = wallpapers.list_wallpapers()
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._buttons = []
        columns = 2
        for index, entry in enumerate(self._entries):
            button = QPushButton(entry["name"])
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(_thumb(entry))
            button.setFixedSize(232, 64)
            button.setIconSize(QSize(THUMB_W, THUMB_H))
            button.setStyleSheet(self._thumb_style(False))
            button.clicked.connect(lambda _=False, i=index: self._activate(i))
            self.grid.addWidget(button, index // columns, index % columns)
            self._buttons.append(button)

        selected_id = self._selected["id"] if self._selected else None
        match = next((i for i, e in enumerate(self._entries)
                      if e["id"] == selected_id), None)
        if match is not None:
            self._select(match)
        else:
            self._selected = None
            self._raw = None
            self.preview.setText("Vyber tapetu z galerie")
            self.preview.setPixmap(QPixmap())
            self.info.setText("")
            self.apply_btn.setEnabled(False)

    def _thumb_style(self, checked):
        border = theme.ACCENT if checked else "#2A2A33"
        background = "rgba(%d, %d, %d, 0.16)" % (
            int(theme.ACCENT[1:3], 16), int(theme.ACCENT[3:5], 16),
            int(theme.ACCENT[5:7], 16)) if checked else "#14141B"
        return (
            "QPushButton { text-align: left; padding: 6px 8px;"
            " border: 1px solid %s; border-radius: 8px; background: %s;"
            " color: %s; font-size: 10px; }"
            " QPushButton:hover { border: 1px solid %s; }"
            % (border, background, theme.MUTED, theme.ACCENT_SOFT)
        )

    def _activate(self, index):
        """Click on a thumbnail = apply locally + push to the device."""
        self._select(index)
        if self._selected is not None and self._raw:
            self.applied.emit(self._selected, self._raw)

    def _select(self, index):
        if not 0 <= index < len(self._entries):
            return
        for i, button in enumerate(self._buttons):
            checked = i == index
            button.setChecked(checked)
            button.setStyleSheet(self._thumb_style(checked))
        entry = self._entries[index]
        self._selected = entry
        try:
            self._raw = wallpapers.selected_raw(entry["path"])
        except Exception as exc:  # noqa: BLE001
            self.set_status("Konverze selhala: %s" % exc)
            return
        pixmap = QPixmap.fromImage(wallpapers.qimage(entry["path"])).scaled(
            int(PANEL_W * PREVIEW_SCALE), int(PANEL_H * PREVIEW_SCALE),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pixmap)
        self.preview.setFixedSize(pixmap.size())
        self.preview.setStyleSheet(
            "border: 1px solid %s; border-radius: 8px;" % theme.ACCENT)
        self.info.setText("284 \u00d7 76 \u00b7 RGB565 big-endian \u00b7"
                          " %d B \u00b7 %s" % (len(self._raw),
                                              entry["path"].name))
        self.apply_btn.setEnabled(True)
        self.set_status("")

    def _add_custom(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Vybrat obr\u00e1zek", "",
            "Obr\u00e1zky (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        target = wallpapers.add_custom(path)
        self.refresh()
        index = next((i for i, e in enumerate(self._entries)
                      if e["path"] == target), None)
        if index is not None:
            self._select(index)
        self.set_status("P\u0159id\u00e1no: %s" % target.name)

    def _apply(self):
        if self._selected is None or not self._raw:
            return
        self.applied.emit(self._selected, self._raw)

    def set_status(self, text):
        self.status.setText(text or "")
