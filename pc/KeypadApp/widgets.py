"""Premium device-face widgets: keycaps (with icons), display preview, encoders,
audio status pill, status badge and segmented profile switcher.

Everything is custom-painted (no GroupBox chrome) so the app reads like a
modern Linear/Raycast style surface instead of native Windows controls.
"""

import json

from PySide6.QtCore import (QEasingCurve, QPointF, QRectF, QSize, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

import theme
from protocol import KEY_MATRIX

SCALE = 2
PANEL_W = 284
PANEL_H = 76
MIME_ENTRY = "application/x-keypad-entry"
FLASH_MS = 220


def prettify(text):
    text = (text or "").strip()
    for prefix in ("media: ", "app: ", "text: ", "hotkey: ", "page: "):
        if text.startswith(prefix):
            return text[len(prefix):].replace("_", " ")
    return text.replace("_", " ")


def _mix(color_a, color_b, t):
    return QColor(
        int(color_a.red() * (1 - t) + color_b.red() * t),
        int(color_a.green() * (1 - t) + color_b.green() * t),
        int(color_a.blue() * (1 - t) + color_b.blue() * t),
    )


class KeyButton(QPushButton):
    """Tactile 3D keycap: key char chip, action icon + label, accent glow."""

    def __init__(self, char, parent=None):
        super().__init__(parent)
        self.char = char
        self._summary = "---"
        self._icon = None
        self._glyph = ""
        self._flash = False
        self._flash_level = 0.0
        self._hover = False
        self._selected = False
        self.setMinimumSize(96, 76)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Key %s" % char)
        self.setToolTip("Key %s" % char)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(26)
        self._glow.setColor(QColor(theme.ACCENT))
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)

        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(220)
        self._flash_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._flash_anim.valueChanged.connect(self._on_flash_anim)
        self._flash_anim.finished.connect(self._flash_done)

        self._hover_level = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_anim)

        # Inner layout: stretch / icon 46x46 / 4px / label / stretch.
        # The key-char badge is painted absolutely (8,8) and never shifts it.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)
        layout.addStretch(1)
        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFixedSize(36, 36)
        layout.addWidget(self.icon_label, 0, Qt.AlignHCenter)
        layout.addSpacing(4)
        self.text_label = QLabel(self)
        self.text_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.text_label.setMinimumWidth(0)
        self.text_label.setSizePolicy(QSizePolicy.Ignored,
                                      QSizePolicy.Preferred)
        self.text_label.setStyleSheet(
            "color: #E4E4E7; font-size: 11px; background: transparent;")
        layout.addWidget(self.text_label, 0)
        layout.addStretch(1)

    def sizeHint(self):
        return QSize(140, 88)

    def set_summary(self, text, icon=None, glyph=""):
        self._summary = text or "---"
        self._icon = icon
        self._glyph = glyph or ""
        self._apply_content()
        self._apply_text()
        self.update()

    def _apply_content(self):
        if self._icon is not None and not self._icon.isNull():
            pixmap = self._icon.pixmap(QSize(36, 36))
            pixmap = pixmap.scaled(36, 36, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation)
            self.icon_label.setStyleSheet("background: transparent;")
            self.icon_label.setPixmap(pixmap)
        elif self._glyph:
            self.icon_label.setStyleSheet(
                "color: %s; font-family: 'Segoe UI Symbol';"
                " font-size: 24px; background: transparent;"
                % theme.ACCENT_SOFT)
            self.icon_label.setText(self._glyph)
        else:
            self.icon_label.clear()

    def _apply_text(self):
        full = prettify(self._summary)
        self.text_label.setToolTip(full)
        self.setToolTip("Key %s \u2014 %s" % (self.char, full))
        metrics = self.text_label.fontMetrics()
        width = max(0, self.text_label.width() - 2)
        if width <= 0:
            self.text_label.setText(full)
            return
        self.text_label.setText(metrics.elidedText(full, Qt.ElideRight, width))

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply_text()

    def set_selected(self, selected):
        self._selected = bool(selected)
        self.update()

    def flash(self):
        self._flash = True
        self._flash_anim.stop()
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._on_flash_anim(1.0)
        self._flash_anim.start()

    def _on_flash_anim(self, value):
        self._flash_level = max(0.0, min(1.0, float(value)))
        glow = QColor(theme.ACCENT)
        glow.setAlpha(int(150 * self._flash_level))
        self._glow.setColor(glow)
        self._glow.setEnabled(self._flash_level > 0.01)
        self.update()

    def _flash_done(self):
        self._flash = False
        self._flash_level = 0.0
        self._glow.setEnabled(False)
        self.update()

    def _on_hover_anim(self, value):
        self._hover_level = max(0.0, min(1.0, float(value)))
        self.update()

    def _hover_to(self, level, duration):
        self._hover_anim.stop()
        self._hover_anim.setDuration(duration)
        self._hover_anim.setStartValue(self._hover_level)
        self._hover_anim.setEndValue(level)
        self._hover_anim.start()

    def enterEvent(self, event):  # noqa: N802 - Qt naming
        self._hover = True
        self._hover_to(1.0, 110)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt naming
        self._hover = False
        self._hover_to(0.0, 150)
        super().leaveEvent(event)

    def paintEvent(self, _event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = QColor(theme.ACCENT)

        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)

        top = QColor("#1E1E27")
        bottom = QColor("#141419")
        hover_level = self._hover_level
        if hover_level > 0.001:
            top = _mix(top, QColor("#262633"), hover_level)
            bottom = _mix(bottom, QColor("#1A1A22"), hover_level)
        if self._flash:
            level = self._flash_level
            top = _mix(top, accent, 0.34 * level)
            bottom = _mix(bottom, accent, 0.16 * level)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillPath(path, gradient)

        if self._selected:
            pen = QPen(accent, 1.6)
        elif self._flash and self._flash_level > 0.01:
            flash_pen = QColor(accent)
            flash_pen.setAlpha(int(70 + 185 * self._flash_level))
            pen = QPen(flash_pen, 1.0 + 0.8 * self._flash_level)
        elif hover_level > 0.001:
            border = _mix(QColor("#2A2A33"), QColor(theme.BORDER2), hover_level)
            pen = QPen(border, 1.0)
        else:
            pen = QPen(QColor("#2A2A33"), 1.0)
        painter.strokePath(path, pen)

        painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
        painter.drawLine(
            QPointF(rect.left() + 14, rect.top() + 1.4),
            QPointF(rect.right() - 14, rect.top() + 1.4),
        )

        chip = QRectF(rect.left() + 8, rect.top() + 8, 22, 17)
        chip_path = QPainterPath()
        chip_path.addRoundedRect(chip, 6, 6)
        chip_fill = QColor(accent)
        chip_fill.setAlpha(38)
        painter.fillPath(chip_path, chip_fill)
        chip_border = QColor(accent)
        chip_border.setAlpha(110)
        painter.strokePath(chip_path, QPen(chip_border, 1.0))
        font = QFont(theme.MONO)
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(chip, Qt.AlignCenter, self.char)
        painter.end()


class KeypadGrid(QWidget):
    key_pressed = Signal(str)
    entry_dropped = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMaximumHeight(360)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.buttons = {}
        for row, line in enumerate(KEY_MATRIX):
            for col, ch in enumerate(line):
                button = KeyButton(ch)
                button.clicked.connect(lambda _=False, c=ch: self.key_pressed.emit(c))
                grid.addWidget(button, row, col)
                self.buttons[ch] = button
        self._selected = None

    def set_summary(self, char, text, icon=None, glyph=""):
        button = self.buttons.get(char)
        if button:
            button.set_summary(text, icon, glyph)

    def flash(self, char):
        button = self.buttons.get(char)
        if button:
            button.flash()

    def select(self, char):
        if self._selected and self._selected in self.buttons:
            self.buttons[self._selected].set_selected(False)
        self._selected = char
        if char in self.buttons:
            self.buttons[char].set_selected(True)

    def dragEnterEvent(self, event):  # noqa: N802 - Qt naming
        if event.mimeData().hasFormat(MIME_ENTRY):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):  # noqa: N802 - Qt naming
        if event.mimeData().hasFormat(MIME_ENTRY):
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802 - Qt naming
        if not event.mimeData().hasFormat(MIME_ENTRY):
            return
        try:
            payload = json.loads(bytes(event.mimeData().data(MIME_ENTRY)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        child = self.childAt(event.position().toPoint())
        if isinstance(child, KeyButton):
            self.entry_dropped.emit(child.char, payload)
            event.acceptProposedAction()


class DisplayPreview(QWidget):
    """Rounded 284x76 panel preview, styled like a real IPS module."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.profile = "EDIT"
        self.meter1 = (0, "--")
        self.meter2 = (0, "--")
        self.wallpaper = None
        self.key = "-"
        self.pc = "waiting"
        self.live = False
        self.telemetry = (None, None, None)  # cpu, gpu, ram
        self._flash_key = ""
        self._flash_level = 0.0

        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(FLASH_MS)
        self._flash_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._flash_anim.valueChanged.connect(self._on_flash_anim)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(38)
        self.setGraphicsEffect(self._glow)
        self.refresh_theme()

    def refresh_theme(self):
        color = QColor(theme.ACCENT3)
        color.setAlpha(80)
        self._glow.setColor(color)
        self.update()

    def sizeHint(self):
        return QSize(PANEL_W * SCALE + 4, PANEL_H * SCALE + 4)

    def hasHeightForWidth(self):  # noqa: N802 - Qt naming
        return True

    def heightForWidth(self, width):  # noqa: N802 - Qt naming
        return max(64, int((width - 4) * PANEL_H / PANEL_W) + 4)

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        desired = self.heightForWidth(self.width())
        if self.height() != desired:
            self.setFixedHeight(desired)

    def set_profile(self, name):
        self.profile = (name or "?")[:5]
        self.update()

    def set_encoder(self, enc_id, pos):
        fill = int(pos) % 100
        if fill < 0:
            fill += 100
        self.set_meter(enc_id, fill, str(pos))

    def set_meter(self, enc_id, fill, text):
        value = int(max(0, min(100, fill)))
        if enc_id == 1:
            self.meter1 = (value, str(text))
        else:
            self.meter2 = (value, str(text))
        self.update()

    def set_wallpaper(self, image):
        """image: QPixmap/QImage/None – background of the virtual display."""
        if image is None:
            self.wallpaper = None
        else:
            pixmap = image if isinstance(image, QPixmap) else QPixmap.fromImage(image)
            self.wallpaper = pixmap.scaled(PANEL_W, PANEL_H,
                                           Qt.IgnoreAspectRatio,
                                           Qt.SmoothTransformation)
        self.update()

    def set_key(self, char):
        self.key = char or "-"
        self.update()

    def set_telemetry(self, cpu, gpu, ram):
        values = (cpu, gpu, ram)
        if values == self.telemetry:
            return
        self.telemetry = values
        self.update()

    def flash_key(self, char):
        """Key press pulse: accent chip in the header + border pulse."""
        self.key = char or "-"
        self._flash_key = self.key
        self._flash_anim.stop()
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._on_flash_anim(1.0)
        self._flash_anim.start()

    def _on_flash_anim(self, value):
        self._flash_level = max(0.0, min(1.0, float(value)))
        self.update()

    def set_pc(self, text):
        self.pc = text or "waiting"
        self.update()

    def set_live(self, live):
        self.live = bool(live)
        if not self.live and self.pc == "waiting":
            self.pc = "offline"
        self.update()

    def _font(self, px, bold=False):
        font = QFont(theme.MONO)
        font.setPixelSize(px)
        font.setBold(bold)
        return font

    def paintEvent(self, _event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        scale = min(self.width() / PANEL_W, self.height() / PANEL_H)
        painter.translate((self.width() - PANEL_W * scale) / 2,
                          (self.height() - PANEL_H * scale) / 2)
        painter.scale(scale, scale)

        frame = QRectF(0, 0, PANEL_W, PANEL_H)
        outer = QPainterPath()
        outer.addRoundedRect(frame, 10, 10)
        accent = QColor(theme.ACCENT)

        back = QLinearGradient(frame.topLeft(), frame.bottomRight())
        back.setColorAt(0.0, _mix(QColor("#0B0B10"), accent, 0.05))
        back.setColorAt(0.55, _mix(QColor("#130E1E"), accent, 0.09))
        back.setColorAt(1.0, _mix(QColor("#1E1333"), accent, 0.15))
        painter.fillPath(outer, back)

        painter.save()
        painter.setClipPath(outer)

        if self.wallpaper is not None:
            painter.drawPixmap(0, 0, self.wallpaper)
        else:
            halo_color = QColor(accent)
            halo_color.setAlpha(46)
            halo = QRadialGradient(QPointF(38, 6), 130)
            halo.setColorAt(0.0, halo_color)
            halo.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
            painter.fillRect(frame, halo)

        header_base = _mix(QColor("#120C1C"), accent, 0.16)
        header = QLinearGradient(QPointF(0, 0), QPointF(0, 15))
        header_top = QColor(header_base)
        header_top.setAlpha(235)
        header_bottom = QColor(header_base)
        header_bottom.setAlpha(120)
        header.setColorAt(0.0, header_top)
        header.setColorAt(1.0, header_bottom)
        painter.fillRect(QRectF(0, 0, PANEL_W, 15), header)
        line_color = QColor(accent)
        line_color.setAlpha(95)
        painter.setPen(QPen(line_color, 1))
        painter.drawLine(QPointF(0, 15), QPointF(PANEL_W, 15))

        painter.setFont(self._font(8, True))
        painter.setPen(QColor("#EDE9FE"))
        painter.drawText(11, 11, "MACRO KEYPAD")

        status_color = QColor(theme.OK if self.live else theme.DIM)
        status_text = "LIVE" if self.live else "OFFLINE"
        status_font = self._font(7, True)
        painter.setFont(status_font)
        metrics = QFontMetrics(status_font)
        text_w = metrics.horizontalAdvance(status_text)
        dot_x = PANEL_W - 14 - text_w
        if self.live:
            glow = QRadialGradient(QPointF(dot_x, 7.5), 9)
            glow.setColorAt(0.0, QColor(16, 185, 129, 120))
            glow.setColorAt(1.0, QColor(16, 185, 129, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(dot_x, 7.5), 9, 9)
            painter.setBrush(status_color)
            painter.drawEllipse(QPointF(dot_x, 7.5), 2.4, 2.4)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(status_color)
            painter.drawEllipse(QPointF(dot_x, 7.5), 2.2, 2.2)
        painter.setPen(status_color)
        painter.drawText(
            QRectF(dot_x + 5, 0, text_w + 2, 15),
            Qt.AlignVCenter | Qt.AlignLeft, status_text)

        def draw_ring(cx, cy, fill, text, label):
            track = QRectF(cx - 14, cy - 14, 28, 28)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#2A2A33"), 3, Qt.SolidLine, Qt.RoundCap))
            painter.drawEllipse(track.adjusted(1.6, 1.6, -1.6, -1.6))

            fill = int(max(0, min(100, fill)))
            span = int(fill * 3.6)
            arc_rect = track.adjusted(1.6, 1.6, -1.6, -1.6)
            if span:
                glow_color = QColor(accent)
                glow_color.setAlpha(60)
                painter.setPen(QPen(glow_color, 6, Qt.SolidLine, Qt.RoundCap))
                painter.drawArc(arc_rect, 90 * 16, -span * 16)
                painter.setPen(QPen(QColor(theme.ACCENT), 2.6,
                                    Qt.SolidLine, Qt.RoundCap))
                painter.drawArc(arc_rect, 90 * 16, -span * 16)

            painter.setFont(self._font(7, True))
            painter.setPen(QColor(theme.ACCENT_SOFT))
            painter.drawText(QRectF(cx - 16, cy - 25, 32, 8),
                             Qt.AlignHCenter | Qt.AlignVCenter, label)

            text = str(text)
            px = 14 if len(text) <= 2 else (11 if len(text) == 3
                                            else (10 if len(text) == 4 else 8))
            painter.setFont(self._font(px, True))
            painter.setPen(QColor("#F4F4F5"))
            painter.drawText(QRectF(cx - 16, cy - 8, 32, 16),
                             Qt.AlignCenter, text)

        draw_ring(74, 41, self.meter1[0], self.meter1[1], "E1")
        draw_ring(210, 41, self.meter2[0], self.meter2[1], "E2")

        # Telemetry CPU | GPU | RAM, centered between the encoder rings.
        centers = (108, 142, 176)
        labels = ("CPU", "GPU", "RAM")
        painter.setFont(self._font(6, True))
        painter.setPen(QColor(theme.MUTED))
        for cx, label in zip(centers, labels):
            painter.drawText(QRectF(cx - 11, 21, 22, 6),
                             Qt.AlignHCenter | Qt.AlignVCenter, label)
        value_font = self._font(9, True)
        painter.setFont(value_font)
        for cx, value in zip(centers, self.telemetry):
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(QRectF(cx - 12, 29, 24, 11),
                             Qt.AlignHCenter | Qt.AlignVCenter,
                             "--%" if value is None else "%d%%" % value)
        for cx, value in zip(centers, self.telemetry):
            fill = 0 if value is None else max(0, min(100, int(value)))
            bar_color = QColor(accent)
            if value is not None and value > 70:
                t = min(1.0, (value - 70) / 30.0)
                warn = QColor(theme.WARN)
                err = QColor(theme.ERR)
                bar_color = _mix(accent, warn, t * 2) if t < 0.5 \
                    else _mix(warn, err, (t - 0.5) * 2)
            track = QRectF(cx - 11, 44, 22, 3)
            track_path = QPainterPath()
            track_path.addRoundedRect(track, 1.5, 1.5)
            painter.fillPath(track_path, QColor("#23232E"))
            if fill:
                bar = QRectF(cx - 11, 44, 22.0 * fill / 100.0, 3)
                if bar.width() >= 2:
                    bar_path = QPainterPath()
                    bar_path.addRoundedRect(bar, 1.5, 1.5)
                    painter.fillPath(bar_path, bar_color)

        painter.setFont(self._font(7, True))
        painter.setPen(QColor(theme.ACCENT_SOFT))
        painter.drawText(QRectF(PANEL_W / 2 - 30, 56, 60, 8),
                         Qt.AlignHCenter | Qt.AlignVCenter, "MODE")

        mode_glow_color = QColor(accent)
        mode_glow_color.setAlpha(60)
        mode_glow = QRadialGradient(QPointF(PANEL_W / 2, 69), 36)
        mode_glow.setColorAt(0.0, mode_glow_color)
        mode_glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(mode_glow)
        painter.drawEllipse(QPointF(PANEL_W / 2, 69), 36, 18)

        painter.setFont(self._font(13, True))
        painter.setPen(QColor(theme.ACCENT))
        painter.drawText(QRectF(PANEL_W / 2 - 50, 63, 100, 12),
                         Qt.AlignCenter, self.profile)

        level = self._flash_level
        if level > 0.01 and self._flash_key:
            chip_text = "KEY %s" % self._flash_key
            chip_font = self._font(7, True)
            painter.setFont(chip_font)
            chip_metrics = QFontMetrics(chip_font)
            chip_w = chip_metrics.horizontalAdvance(chip_text) + 12
            chip = QRectF(PANEL_W / 2 - chip_w / 2, 2.5, chip_w, 10)
            chip_path = QPainterPath()
            chip_path.addRoundedRect(chip, 4, 4)
            chip_fill = QColor(accent)
            chip_fill.setAlpha(int(170 * level))
            painter.fillPath(chip_path, chip_fill)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(chip, Qt.AlignCenter, chip_text)

        painter.restore()

        level = self._flash_level
        border = QColor(theme.ACCENT3 if self.live else theme.ACCENT)
        if level > 0.01:
            border.setAlpha(int(110 + 145 * level))
            border_pen = QPen(border, 1.0 + 1.6 * level)
        else:
            border.setAlpha(170 if self.live else 110)
            border_pen = QPen(border, 1.0)
        painter.strokePath(outer, border_pen)
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
        painter.drawLine(QPointF(12, 1), QPointF(PANEL_W - 12, 1))
        painter.end()


class VolumeBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._shown = 0.0
        self._target = 0.0
        self._unknown = True
        self._muted = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._flash_level = 0.0
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(FLASH_MS)
        self._flash_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._flash_anim.valueChanged.connect(self._on_flash_anim)

    def _on_anim(self, value):
        self._shown = float(value)
        self.update()

    def _on_flash_anim(self, value):
        self._flash_level = max(0.0, min(1.0, float(value)))
        self.update()

    def flash(self):
        self._flash_anim.stop()
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._on_flash_anim(1.0)
        self._flash_anim.start()

    def set_value(self, percent, muted=False):
        self._unknown = percent is None
        self._muted = muted
        target = 0.0 if percent is None else max(0.0, min(100.0, float(percent)))
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(target)
        self._anim.start()
        if self._unknown:
            self._shown = 0.0
            self.update()

    def paintEvent(self, _event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        height = 8.0
        y = (self.height() - height) / 2
        track = QRectF(0, y, self.width(), height)
        track_path = QPainterPath()
        track_path.addRoundedRect(track, 4, 4)
        painter.fillPath(track_path, QColor("#23232E"))
        painter.strokePath(track_path, QPen(QColor("#2E2E3A"), 1.0))

        width = track.width() * max(0.0, min(100.0, self._shown)) / 100.0
        if not self._unknown and width > 0:
            fill = QRectF(0, y, max(width, height), height)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill, 4, 4)
            gradient = QLinearGradient(QPointF(0, y), QPointF(fill.width(), y))
            if self._muted:
                gradient.setColorAt(0.0, QColor("#3F3F46"))
                gradient.setColorAt(1.0, QColor("#52525B"))
            else:
                gradient.setColorAt(0.0, QColor(theme.ACCENT2))
                gradient.setColorAt(1.0, QColor(theme.ACCENT_SOFT))
            painter.fillPath(fill_path, gradient)

            if not self._muted:
                cap = QPointF(fill.right() - height / 2, y + height / 2)
                cap_color = QColor(theme.ACCENT_SOFT)
                glow = QRadialGradient(cap, 9)
                glow_color = QColor(cap_color)
                glow_color.setAlpha(120)
                glow.setColorAt(0.0, glow_color)
                glow_color.setAlpha(0)
                glow.setColorAt(1.0, glow_color)
                painter.setPen(Qt.NoPen)
                painter.setBrush(glow)
                painter.drawEllipse(cap, 9, 9)
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawEllipse(cap, 2.0, 2.0)

        level = self._flash_level
        if level > 0.01:
            highlight = QColor("#FFFFFF")
            highlight.setAlpha(int(150 * level))
            painter.fillPath(track_path, highlight)
            border = QColor(theme.ACCENT)
            border.setAlpha(int(90 + 160 * level))
            painter.strokePath(track_path, QPen(border, 1.0 + 1.2 * level))
        painter.end()


class AudioPill(QFrame):
    """Compact header indicator: MASTER value + animated bar + active app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 12, 4)
        row.setSpacing(8)

        self.title = QLabel("MASTER")
        self.title.setStyleSheet("color: %s; font-size: 10px; font-weight: 700;"
                                 % theme.DIM)
        row.addWidget(self.title)

        self.value = QLabel("--")
        self.value.setStyleSheet(
            "color: %s; font-size: 12px; font-weight: 600;" % theme.TEXT
        )
        row.addWidget(self.value)

        self.bar = VolumeBar()
        self.bar.setFixedWidth(68)
        row.addWidget(self.bar, 0, Qt.AlignVCenter)

        divider = QFrame()
        divider.setObjectName("sep")
        divider.setFixedSize(1, 16)
        row.addWidget(divider, 0, Qt.AlignVCenter)

        self.app_label = QLabel("--")
        self.app_label.setStyleSheet("color: %s; font-size: 10px;" % theme.MUTED)
        row.addWidget(self.app_label)
        self.setMinimumWidth(330)
        self._last = (None, None)

    def set_values(self, master, muted, app_name, app_percent, app_muted):
        previous = self._last
        self._last = (master, app_percent)
        if (previous[0] is not None or previous[1] is not None) \
                and previous != (master, app_percent):
            self.bar.flash()
        if master is None:
            self.value.setText("--")
        else:
            self.value.setText("%d%%%s" % (master, "  mute" if muted else ""))
        self.bar.set_value(master, bool(muted))
        if app_percent is None:
            self.app_label.setText("%s  --" % (app_name or "app"))
        else:
            self.app_label.setText("%s  %d%%%s" % (
                app_name or "app", app_percent, "  mute" if app_muted else ""))
        self.app_label.setStyleSheet(
            "color: %s; font-size: 10px;"
            % (theme.ERR if app_percent is None else theme.MUTED)
        )


class EncoderCard(QFrame):
    step = Signal(int)
    switch = Signal()

    def __init__(self, enc_id, title, parent=None):
        super().__init__(parent)
        self.enc_id = enc_id
        self.setObjectName("inner")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.title_label = QLabel(theme.caption(title))
        top.addWidget(self.title_label)
        top.addStretch(1)
        self.pos_label = QLabel("pos 0")
        self.pos_label.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        top.addWidget(self.pos_label)
        layout.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.percent_label = QLabel("--")
        self.percent_label.setStyleSheet(theme.mono_style(24, theme.TEXT, bold=True))
        row.addWidget(self.percent_label)
        self.state_label = QLabel("no session")
        self.state_label.setStyleSheet("color: %s; font-size: 11px;" % theme.DIM)
        row.addWidget(self.state_label, 0, Qt.AlignBottom)
        row.addStretch(1)
        layout.addLayout(row)

        self.bar = VolumeBar()
        layout.addWidget(self.bar)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        minus = QPushButton("\u2212")
        plus = QPushButton("+")
        for button in (minus, plus):
            button.setObjectName("mini")
            button.setFixedSize(28, 26)
        minus.clicked.connect(lambda: self.step.emit(-1))
        plus.clicked.connect(lambda: self.step.emit(1))
        bottom.addWidget(minus)
        bottom.addWidget(plus)
        bottom.addStretch(1)
        self.mute_btn = QPushButton("MUTE")
        self.mute_btn.setObjectName("mini")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setFixedSize(54, 26)
        self.mute_btn.clicked.connect(self.switch.emit)
        bottom.addWidget(self.mute_btn)
        layout.addLayout(bottom)
        self._last_percent = None

    def set_position(self, pos):
        previous = getattr(self, "_last_pos", None)
        self._last_pos = pos
        self.pos_label.setText("pos %d" % pos)
        if previous is not None and previous != pos:
            self.bar.flash()

    def set_title(self, title):
        self.title_label.setText(theme.caption(title))

    def set_values(self, percent, muted=False, pos=None):
        previous = self._last_percent
        self._last_percent = percent
        if previous is not None and previous != percent:
            self.bar.flash()
        self.bar.set_value(percent, muted)
        if percent is None:
            self.percent_label.setText("--")
            self.state_label.setText("no session")
        else:
            self.percent_label.setText("%d%%" % percent)
            self.state_label.setText("muted" if muted else "volume")
        self.mute_btn.setChecked(bool(muted))
        if pos is not None:
            self.set_position(pos)


class StatusPill(QWidget):
    """Rounded badge with a softly glowing status dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "offline"
        self._state = "offline"
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.set_status("offline", "offline")

    def _colors(self):
        return {
            "offline": QColor(theme.ERR),
            "connecting": QColor(theme.WARN),
            "live": QColor(theme.OK),
        }

    def set_status(self, text, state="offline"):
        self._text = text
        self._state = state if state in self._colors() else "offline"
        font = QFont(theme.SANS)
        font.setPixelSize(11)
        font.setWeight(QFont.DemiBold)
        width = QFontMetrics(font).horizontalAdvance(text) + 40
        self.setFixedWidth(max(96, width))
        self.update()

    def paintEvent(self, _event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.fillPath(path, QColor(theme.SURFACE2))
        painter.strokePath(path, QPen(QColor(theme.BORDER), 1))

        color = self._colors().get(self._state, QColor(theme.ERR))
        center = QPointF(14, rect.center().y())
        glow = QRadialGradient(center, 9)
        glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 110))
        glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, 9, 9)
        painter.setBrush(color)
        painter.drawEllipse(center, 3.4, 3.4)

        font = QFont(theme.SANS)
        font.setPixelSize(11)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(24, 0, rect.width() - 30, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._text,
        )
        painter.end()


class SegmentedControl(QFrame):
    changed = Signal(int)
    add_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._buttons = []
        self._current = 0

    def set_names(self, names, current=None, can_add=False):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._buttons = []
        for index, name in enumerate(names):
            button = QPushButton(name)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self._select(i, True))
            self._layout.addWidget(button)
            self._buttons.append(button)
        if can_add:
            add = QPushButton("+")
            add.setObjectName("seg")
            add.setFixedWidth(32)
            add.setCursor(Qt.PointingHandCursor)
            add.setToolTip("Pridat profil")
            add.clicked.connect(self.add_requested.emit)
            self._layout.addWidget(add)
        self._select(0 if current is None else current, False)

    def _select(self, index, emit):
        if not self._buttons:
            return
        index = max(0, min(index, len(self._buttons) - 1))
        self._current = index
        for i, button in enumerate(self._buttons):
            button.setChecked(i == index)
        if emit:
            self.changed.emit(index)

    def set_current(self, index):
        self._select(index, False)

    def current(self):
        return self._current


class HotkeyEdit(QLineEdit):
    """Click-to-record combo field: captures keys, never fires them."""

    changed = Signal(str)
    recording_changed = Signal(bool)

    _MODIFIER_KEYS = (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta)
    _NAMED_KEYS = {
        Qt.Key_Return: "Enter", Qt.Key_Enter: "Enter", Qt.Key_Escape: "Esc",
        Qt.Key_Backspace: "Backspace", Qt.Key_Delete: "Delete",
        Qt.Key_Insert: "Insert", Qt.Key_Tab: "Tab", Qt.Key_Space: "Space",
        Qt.Key_Home: "Home", Qt.Key_End: "End", Qt.Key_PageUp: "PageUp",
        Qt.Key_PageDown: "PageDown", Qt.Key_Up: "Up", Qt.Key_Down: "Down",
        Qt.Key_Left: "Left", Qt.Key_Right: "Right", Qt.Key_CapsLock: "CapsLock",
        Qt.Key_ScrollLock: "ScrollLock", Qt.Key_Pause: "Pause",
        Qt.Key_Print: "PrintScreen", Qt.Key_Super_L: "Win", Qt.Key_Super_R: "Win",
        Qt.Key_QuoteLeft: "`", Qt.Key_Minus: "-", Qt.Key_Equal: "=",
        Qt.Key_BracketLeft: "[", Qt.Key_BracketRight: "]", Qt.Key_Semicolon: ";",
        Qt.Key_Apostrophe: "'", Qt.Key_Comma: ",", Qt.Key_Period: ".",
        Qt.Key_Slash: "/", Qt.Key_Backslash: "\\", Qt.Key_Plus: "+",
        Qt.Key_Asterisk: "*", Qt.Key_AsciiTilde: "`",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recording = False
        self._previous = ""
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setPlaceholderText("Klik pro nahr\u00e1n\u00ed zkratky\u2026")
        self.setToolTip(
            "Klikni a stiskni kombinaci kl\u00e1ves (Esc zru\u0161\u00ed)")

    @property
    def recording(self):
        return self._recording

    def start_recording(self):
        if self._recording:
            return
        self._recording = True
        self._previous = self.text()
        self.clear()
        self.setFocus(Qt.MouseFocusReason)
        self.setPlaceholderText("Stiskn\u011bte kombinaci kl\u00e1ves\u2026")
        self.setStyleSheet(
            "QLineEdit { border: 2px solid %s; border-radius: 6px;"
            " background: rgba(255,255,255,0.04); color: %s; }"
            % (theme.ACCENT, theme.TEXT))
        self.recording_changed.emit(True)

    def stop_recording(self, restore=False):
        if not self._recording:
            return
        self._recording = False
        if restore:
            self.setText(self._previous)
        self.setStyleSheet("")
        self.setPlaceholderText("Klik pro nahr\u00e1n\u00ed zkratky\u2026")
        self.recording_changed.emit(False)

    def mousePressEvent(self, event):  # noqa: N802 - Qt naming
        if not self._recording:
            self.start_recording()
            event.accept()
            return
        super().mousePressEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 - Qt naming
        if self._recording:
            self.stop_recording(restore=not self.text())
        super().focusOutEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self.stop_recording(restore=True)
            event.accept()
            return
        if key in self._MODIFIER_KEYS:
            event.accept()
            return
        combo = self._format(event)
        if not combo:
            event.accept()
            return
        self.setText(combo)
        self.changed.emit(combo)
        self.stop_recording()
        event.accept()

    def _format(self, event):
        key = event.key() & ~0x20000000  # strip Qt.KeypadModifier
        name = self._NAMED_KEYS.get(key)
        if name is None:
            if Qt.Key_A <= key <= Qt.Key_Z:
                name = chr(key)
            elif Qt.Key_0 <= key <= Qt.Key_9:
                name = chr(key)
            elif Qt.Key_F1 <= key <= Qt.Key_F24:
                name = "F%d" % (key - Qt.Key_F1 + 1)
        if not name:
            return ""
        parts = []
        mods = event.modifiers()
        if mods & Qt.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.ShiftModifier:
            parts.append("Shift")
        if mods & Qt.AltModifier:
            parts.append("Alt")
        if mods & Qt.MetaModifier:
            parts.append("Win")
        parts.append(name)
        return "+".join(parts)
