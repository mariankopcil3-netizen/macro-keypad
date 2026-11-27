"""Fintr Deck entry point.

Usage:
    python main.py                      # GUI, connect manually
    python main.py --port COM5          # GUI + auto connect
    python main.py --port COM5 --dry-run
"""

import argparse
import ctypes
import os
import sys

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from window import MainWindow
import theme

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "app_icon.ico")
APP_MODEL_ID = "FiinTr.FintrDeck"


def _app_icon():
    if os.path.isfile(ICON_PATH):
        return QIcon(ICON_PATH)
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Fintr Deck - PC host for the macro keypad")
    parser.add_argument("--port", help="COM port, e.g. COM5 (auto connects)")
    parser.add_argument("--baud", type=int, help="baud rate (default 115200)")
    parser.add_argument("--dry-run", action="store_true", help="log actions instead of executing them")
    parser.add_argument("--demo", action="store_true", help="cycle simulated events for a quick check")
    args = parser.parse_args()

    if sys.platform == "win32":
        # Taskbar icon grouping: must be set before QApplication.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_MODEL_ID)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Fintr Deck")
    app.setQuitOnLastWindowClosed(True)
    theme.apply(app)

    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    window = MainWindow(args)
    window.show()

    if icon is None:
        icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu()
    show_action = QAction("Show", app)
    show_action.triggered.connect(window.showNormal)
    menu.addAction(show_action)
    quit_action = QAction("Quit", app)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.setToolTip("Fintr Deck")
    tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
