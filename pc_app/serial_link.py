"""Serial link to the keypad with automatic reconnect."""

import threading

import serial
from serial.tools import list_ports

# USB-serial chips commonly found on ESP32 dev boards (VID -> name).
KNOWN_VIDS = {
    0x10C4: "CP210x",
    0x1A86: "CH340/CH341",
    0x0403: "FTDI",
    0x303A: "Espressif native USB",
    0x2E8A: "Raspberry Pi",
}
PREFERRED_VIDS = {0x10C4, 0x1A86, 0x303A}
DESCRIPTION_HINTS = ("cp210", "ch340", "ch341", "esp32", "usb serial", "usb-serial")


def _port_score(port):
    """Heuristic score for a pyserial port; 0 means 'not a keypad candidate'."""
    if port.vid in KNOWN_VIDS:
        score = 10
        if port.vid in PREFERRED_VIDS:
            score += 5
        return score

    text = " ".join(
        str(value) for value in (port.description, port.manufacturer, port.product) if value
    ).lower()
    if any(hint in text for hint in DESCRIPTION_HINTS):
        return 5
    return 0


def port_candidates():
    """Likely keypad ports, best first."""
    scored = []
    for port in list_ports.comports():
        score = _port_score(port)
        if score:
            scored.append((score, port.device))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [device for _, device in scored]


def detect_port():
    """Auto-detect the keypad port or return None."""
    candidates = port_candidates()
    return candidates[0] if candidates else None


class SerialLink:
    def __init__(self, on_line, on_status):
        self._on_line = on_line
        self._on_status = on_status
        self._thread = None
        self._stop = threading.Event()
        self._port = None
        self._baud = 115200
        self._ser = None
        self._lock = threading.Lock()

    @staticmethod
    def available_ports():
        return [p.device for p in list_ports.comports()]

    @staticmethod
    def port_candidates():
        return port_candidates()

    @staticmethod
    def detect_port():
        return detect_port()

    @property
    def connected(self):
        with self._lock:
            return self._ser is not None

    def start(self, port, baud):
        self.stop()
        self._port = port
        self._baud = baud
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="keypad-serial", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None

    def send(self, line):
        with self._lock:
            ser = self._ser
        if ser is None:
            return False
        try:
            ser.write(line.encode("utf-8", "ignore"))
            return True
        except Exception as exc:  # noqa: BLE001
            self._on_status("write failed: %s" % exc)
            return False

    def _run(self):
        while not self._stop.is_set():
            try:
                ser = serial.Serial(self._port, self._baud, timeout=0.2)
            except Exception as exc:  # noqa: BLE001
                self._on_status("open %s failed: %s - retry in 2s" % (self._port, exc))
                self._stop.wait(2.0)
                continue

            with self._lock:
                self._ser = ser
            self._on_status("connected %s @ %d" % (self._port, self._baud))

            try:
                while not self._stop.is_set():
                    raw = ser.readline()
                    if raw:
                        self._on_line(raw.decode("utf-8", "replace").strip())
            except Exception as exc:  # noqa: BLE001
                self._on_status("serial error: %s - reconnecting" % exc)
            finally:
                with self._lock:
                    self._ser = None
                try:
                    ser.close()
                except Exception:  # noqa: BLE001
                    pass

            if not self._stop.is_set():
                self._stop.wait(1.0)
