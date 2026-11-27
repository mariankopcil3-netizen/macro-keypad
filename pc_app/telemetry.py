"""Background CPU / GPU / RAM sampler (1 Hz, off the GUI thread).

GPU: NVIDIA via pynvml (nvmlDeviceGetUtilizationRates); anything else
(Radeon / iGPU) falls back to the native Windows PDH counter
\\GPU Engine(*engtype_3D)\\Utilization Percentage, summed and capped like
Task Manager. No admin rights required. Missing sensors report as None.
"""

import ctypes
import threading
import time
import winreg

try:
    import pynvml
except ImportError:  # optional NVIDIA path
    pynvml = None

try:
    import psutil
except ImportError:  # safe fallback: CPU/RAM report "--%"
    psutil = None

from PySide6.QtCore import QObject, Signal

TICK_SECONDS = 1.0
_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2
_PDH_CSTATUS_VALID = 0


class TelemetryBridge(QObject):
    ready = Signal(dict)


def _adapter_name():
    """First display adapter name from the driver registry key."""
    try:
        path = (r"SYSTEM\CurrentControlSet\Control\Class"
                r"\{4d36e968-e325-11ce-bfc1-08002be10318}\0000")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return str(winreg.QueryValueEx(key, "DriverDesc")[0])
    except OSError:
        return ""


class _NvmlGpu:
    def __init__(self):
        pynvml.nvmlInit()
        self._handles = []
        names = []
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            self._handles.append(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            names.append(name.decode(errors="replace")
                         if isinstance(name, bytes) else str(name))
        if not self._handles:
            raise OSError("no NVIDIA device")
        self.name = " + ".join(names)

    def percent(self):
        values = []
        for handle in self._handles:
            try:
                values.append(
                    int(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu))
            except Exception:  # noqa: BLE001 - per-card hiccup
                continue
        return max(values) if values else None


class _PdhItem(ctypes.Structure):
    # PDH_FMT_COUNTERVALUE_ITEM_W: szName, then FmtValue{CStatus, union{double}}
    # => stride 24 on x64
    _fields_ = [("szName", ctypes.c_void_p),
                ("CStatus", ctypes.c_uint32),
                ("FmtValue", ctypes.c_double)]


class _PdhGpu:
    def __init__(self):
        self._pdh = ctypes.windll.pdh
        self._query = ctypes.c_void_p()
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        self._counter = ctypes.c_void_p()
        path = r"\GPU Engine(*engtype_3D)\Utilization Percentage"
        if self._pdh.PdhAddEnglishCounterW(self._query, path, 0,
                                          ctypes.byref(self._counter)) != 0:
            self._close()
            raise OSError("GPU engine counter not available")
        self.name = _adapter_name()
        # Seed sample: the open query keeps rate deltas across seconds, so the
        # first real read already reflects GPU work instead of 0 %.
        self._pdh.PdhCollectQueryData(self._query)
        self._last_collect = time.monotonic()
        self._value = None

    @staticmethod
    def _adapter_key(instance):
        parts = instance.split("_")
        for index, part in enumerate(parts):
            if part.lower() == "luid" and index + 2 < len(parts):
                return (parts[index + 1] + parts[index + 2]).lower()
        return instance.rsplit("_eng", 1)[0].lower()

    def percent(self):
        pdh = self._pdh
        now = time.monotonic()
        if now - self._last_collect < 0.5:
            # Rate counters need a full interval; report unknown instead of 0.
            return self._value
        self._last_collect = now
        if pdh.PdhCollectQueryData(self._query) != 0:
            return None
        size = ctypes.c_uint(0)
        count = ctypes.c_uint(0)
        status = pdh.PdhGetFormattedCounterArrayW(
            self._counter, _PDH_FMT_DOUBLE,
            ctypes.byref(size), ctypes.byref(count), None)
        if (status & 0xFFFFFFFF) not in (0, _PDH_MORE_DATA) or size.value == 0:
            return 0
        # PDH can under-report the required size; allocate a safety pad.
        buffer = ctypes.create_string_buffer(size.value + 4096)
        if pdh.PdhGetFormattedCounterArrayW(
                self._counter, _PDH_FMT_DOUBLE,
                ctypes.byref(size), ctypes.byref(count),
                ctypes.cast(buffer, ctypes.c_void_p)) != 0:
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhItem))
        per_adapter = {}
        for index in range(count.value):
            item = items[index]
            if item.CStatus != _PDH_CSTATUS_VALID:
                continue
            key = self._adapter_key(ctypes.wstring_at(item.szName))
            per_adapter[key] = per_adapter.get(key, 0.0) + max(0.0, item.FmtValue)
        if not per_adapter:
            return 0
        self._value = int(min(100.0, max(per_adapter.values())))
        return self._value

    def _close(self):
        if self._query.value:
            self._pdh.PdhCloseQuery(self._query)


def _make_gpu():
    if pynvml is not None:
        try:
            return _NvmlGpu()
        except Exception:  # noqa: BLE001 - no NVIDIA present or NVML blocked
            pass
    try:
        return _PdhGpu()
    except Exception:  # noqa: BLE001 - PDH not available (e.g. no GPU engines)
        return None


def sample_loop(stop, emit):
    """Worker thread body: emit a snapshot dict every second until stopped."""
    if psutil is not None:
        try:
            psutil.cpu_percent(interval=None)  # prime the CPU delta counter
        except Exception:  # noqa: BLE001
            pass
    gpu = _make_gpu()
    gpu_name = gpu.name if gpu is not None else ""
    next_tick = time.monotonic()
    while not stop.is_set():
        data = {"cpu": None, "gpu": None, "ram": None,
                "gpu_name": gpu_name, "ram_used": None, "ram_total": None}
        if psutil is not None:
            try:
                data["cpu"] = int(psutil.cpu_percent(interval=None))
                memory = psutil.virtual_memory()
                data["ram"] = int(memory.percent)
                data["ram_used"] = memory.used / (1024 ** 3)
                data["ram_total"] = memory.total / (1024 ** 3)
            except Exception:  # noqa: BLE001
                pass
        if gpu is not None:
            try:
                data["gpu"] = gpu.percent()
            except Exception:  # noqa: BLE001 - sensor hiccup -> "--%"
                data["gpu"] = None
        emit(data)
        next_tick += TICK_SECONDS
        delay = next_tick - time.monotonic()
        if delay < 0:
            next_tick = time.monotonic()
            delay = 0.0
        stop.wait(delay)


def start(stop):
    """Convenience: (bridge, thread) pair; caller owns the stop event."""
    bridge = TelemetryBridge()
    thread = threading.Thread(
        target=sample_loop, args=(stop, bridge.ready.emit), daemon=True)
    thread.start()
    return bridge
