#!/usr/bin/env python3
"""Live encoder test for the Keypad firmware (COM port, 115200).

Usage:
    python tools/encoder_test.py            # auto-detect port
    python tools/encoder_test.py COM7       # explicit port

Enables the firmware RAW echo mode ("E"), prints encoder/switch events and
raw pin states. Ctrl+C exits and disables RAW mode again.
"""

import sys
import time

import serial
from serial.tools import list_ports

NAMES = {"E1": "ENC1", "E2": "ENC2"}


def pick_port(arg):
    if arg:
        return arg
    best = None
    for p in list_ports.comports():
        hwid = (p.hwid or "").upper()
        if any(v in hwid for v in ("1A86", "10C4", "303A", "0403")):
            desc = (p.description or "").lower()
            score = 2 if "ch9102" in desc else 1
            if best is None or score > best[0]:
                best = (score, p.device)
    if best:
        return best[1]
    ports = [p.device for p in list_ports.comports()]
    raise SystemExit(f"No ESP32-like port found. Available: {ports}")


def main():
    port = pick_port(sys.argv[1] if len(sys.argv) > 1 else None)
    ser = serial.Serial(port, 115200, timeout=0.2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.2)

    print(f"Port: {port}")
    print("Enable RAW mode, turn both encoders and press both switches. Ctrl+C to stop.\n")
    ser.reset_input_buffer()
    ser.write(b"E\n")

    last_raw = None
    try:
        while True:
            line = ser.readline().decode(errors="replace").strip()
            if not line:
                continue
            if line.startswith("RAW "):
                raw = line.split("|", 1)[-1].strip()
                if raw != last_raw:
                    last_raw = raw
                    print(f"  raw  {line[4:]}")
                continue
            if line.startswith(("ENC1", "ENC2", "E1", "E2")):
                print(f"EVENT {line}")
            elif line.startswith("RAWMODE"):
                print(f"[{line}]  (rotate now)")
    except KeyboardInterrupt:
        print("\nStopping, RAW mode off.")
    finally:
        ser.write(b"E\n")
        time.sleep(0.2)
        ser.close()


if __name__ == "__main__":
    main()
