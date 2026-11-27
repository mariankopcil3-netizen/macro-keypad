"""Interactive product tour: spotlight overlay walking through the app UI.

Usage (main window):
    self.tour = TourOverlay(self)
    self.tour.closed.connect(self._on_tour_closed)
    self.tour.start(self._tour_steps())

tour.Step(title, text, targets, action=None):
    targets - list of callables returning the QWidget(s) to spotlight
    action  - optional callable(overlay) executed when the step is shown
"""
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

import theme

SPOT_MARGIN = 14
SPOT_RADIUS = 10
CARD_WIDTH = 340
CARD_MARGIN = 10
HINT_TEXT = "Enter / klik \u2014 dal\u0161\u00ed \u00b7 Backspace \u2014 zp\u011bt \u00b7 Esc \u2014 konec"


class Step:
    def __init__(self, title, text, targets, action=None):
        self.title = title
        self.text = text
        self.targets = targets
        self.action = action


class TourOverlay(QWidget):
    closed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._steps = []
        self._index = 0
        self._active = False
        self._paused = False
        self._spot = QRect()
        parent.installEventFilter(self)
        self.hide()

    # ------------------------------------------------------------ public API
    def start(self, steps):
        self._steps = list(steps)
        self._index = 0
        self._active = True
        self._paused = False
        self._sync_geometry()
        self.raise_()
        self.show()
        self.setFocus()
        self._apply_step()

    def pause(self):
        self._paused = True
        self.hide()

    def resume(self):
        if not self._active:
            return
        self._paused = False
        self._sync_geometry()
        self.raise_()
        self.show()
        self.setFocus()

    def _next(self):
        if not self._active or self._paused:
            return
        if self._index >= len(self._steps) - 1:
            self.finish()
            return
        self._index += 1
        self._apply_step()

    def _prev(self):
        if not self._active or self._paused or self._index == 0:
            return
        self._index -= 1
        self._apply_step()

    def finish(self):
        self._active = False
        self.hide()
        self.closed.emit()

    def refresh_theme(self):
        self.update()

    def is_active(self):
        return self._active and not self._paused

    # ------------------------------------------------------------ internals
    def _sync_geometry(self):
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(0, 0, parent.width(), parent.height())

    def _target_rect(self):
        parent = self.parentWidget()
        rect = QRect()
        for target in (self._steps[self._index].targets or []):
            try:
                widget = target()
            except AttributeError:      # widget not built yet
                continue
            if widget is None:
                continue
            geometry = widget.geometry()
            top_left = widget.mapTo(parent, QPoint(0, 0))
            geometry.moveTopLeft(top_left)
            if rect.isValid():
                rect = rect.united(geometry)
            else:
                rect = geometry
        if not rect.isValid() or rect.isEmpty():
            return QRect()
        return rect.adjusted(-SPOT_MARGIN, -SPOT_MARGIN, SPOT_MARGIN, SPOT_MARGIN)

    def _apply_step(self):
        step = self._steps[self._index]
        if step.action is not None:
            try:
                step.action(self)
            except Exception:  # noqa: BLE001 - tour must never crash the app
                pass
        self._spot = self._target_rect()
        self._sync_geometry()
        self.update()
        # Widgets hidden behind a tab switch may re-layout; re-measure shortly.
        QTimer.singleShot(250, self._remeasure)

    def _remeasure(self):
        if not self.is_active():
            return
        self._spot = self._target_rect()
        self.update()

    # ------------------------------------------------------------ painting
    def paintEvent(self, _event):  # noqa: N802 - Qt naming
        if not self._active or self._paused:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        accent = QColor(theme.ACCENT)
        dim = QColor(8, 6, 12, 200)

        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        if self._spot.isValid():
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(self._spot), SPOT_RADIUS, SPOT_RADIUS)
            painter.fillPath(path.subtracted(hole), dim)
            pen = QPen(accent, 2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(self._spot), SPOT_RADIUS, SPOT_RADIUS)
        else:
            painter.fillPath(path, dim)

        self._draw_card(painter, accent)

    def _draw_card(self, painter, accent):
        step = self._steps[self._index]
        parent_rect = QRectF(self.rect())

        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        body_font = QFont()
        body_font.setPointSize(9)
        hint_font = QFont()
        hint_font.setPointSize(8)

        text_rect = QRectF(0, 0, CARD_WIDTH - 32, 200)
        painter.setFont(body_font)
        text_rect = painter.boundingRect(text_rect, Qt.TextWordWrap,
                                         step.text)
        title_h = 22
        hint_h = 16
        card_h = title_h + text_rect.height() + hint_h + 26
        card_w = CARD_WIDTH

        # Prefer below the spotlight, otherwise above, otherwise centered.
        if self._spot.isValid():
            x = self._spot.center().x() - card_w / 2
            y = self._spot.bottom() + CARD_MARGIN
            if y + card_h > parent_rect.bottom() - 8:
                y = self._spot.top() - card_h - CARD_MARGIN
            if y < 8:
                y = (parent_rect.height() - card_h) / 2
            x = max(8, min(x, parent_rect.right() - card_w - 8))
        else:
            x = (parent_rect.width() - card_w) / 2
            y = (parent_rect.height() - card_h) / 2

        card = QRectF(x, y, card_w, card_h)
        painter.setPen(QPen(accent, 1))
        painter.setBrush(QColor(23, 17, 31, 248))
        painter.drawRoundedRect(card, 10, 10)

        inner = card.adjusted(16, 8, -16, -8)
        painter.setPen(accent)
        painter.setFont(title_font)
        painter.drawText(QRectF(inner.left(), inner.top(),
                                inner.width(), title_h - 4),
                         Qt.AlignLeft | Qt.AlignVCenter, step.title)

        painter.setPen(QColor(theme.TEXT))
        painter.setFont(body_font)
        painter.drawText(
            QRectF(inner.left(), inner.top() + title_h - 4,
                   inner.width(), text_rect.height() + 4),
            Qt.TextWordWrap, step.text)

        painter.setPen(QColor(theme.DIM))
        painter.setFont(hint_font)
        counter = "%d / %d" % (self._index + 1, len(self._steps))
        painter.drawText(QRectF(inner.left(), card.bottom() - hint_h - 10,
                                inner.width() - 60, hint_h),
                         Qt.AlignLeft | Qt.AlignVCenter, HINT_TEXT)
        painter.drawText(QRectF(card.right() - 66, card.bottom() - hint_h - 10,
                                60, hint_h),
                         Qt.AlignRight | Qt.AlignVCenter, counter)

    # ------------------------------------------------------------ events
    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space, Qt.Key_Right):
            self._next()
        elif key in (Qt.Key_Backspace, Qt.Key_Left):
            self._prev()
        elif key == Qt.Key_Escape:
            self.finish()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 - Qt naming
        if self._spot.isValid() and self._spot.contains(event.position().toPoint()):
            self._next()
        elif not self._spot.isValid():
            self._next()

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if obj is self.parentWidget() and self._active:
            if event.type() in (event.Type.Resize, event.Type.Move):
                self._sync_geometry()
                self._spot = self._target_rect()
                self.update()
        return super().eventFilter(obj, event)
