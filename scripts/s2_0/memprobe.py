"""Windows 行程記憶體測量（ctypes，不需要 psutil）。

背景執行緒每 10 ms 取樣一次 WorkingSet（實際佔用的實體記憶體）與 PrivateUsage（commit），
可以分段記錄每個階段的峰值。
"""

from __future__ import annotations

import ctypes
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


_psapi = ctypes.WinDLL("psapi")
_kernel = ctypes.WinDLL("kernel32")
_kernel.GetCurrentProcess.restype = wintypes.HANDLE
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def current() -> tuple[int, int]:
    """(WorkingSet, PrivateUsage) bytes。"""
    c = PROCESS_MEMORY_COUNTERS_EX()
    c.cb = ctypes.sizeof(c)
    _psapi.GetProcessMemoryInfo(_kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize, c.PrivateUsage


def system_ram() -> dict:
    s = MEMORYSTATUSEX()
    s.dwLength = ctypes.sizeof(s)
    _kernel.GlobalMemoryStatusEx(ctypes.byref(s))
    return {"total_gb": round(s.ullTotalPhys / 2**30, 1), "available_gb": round(s.ullAvailPhys / 2**30, 1)}


def mb(b: int) -> float:
    return round(b / 2**20, 1)


class Probe:
    def __init__(self, interval: float = 0.01):
        self.interval = interval
        self.phases: dict[str, dict] = {}
        self._peak_ws = self._peak_priv = 0
        self._lock = threading.Lock()
        self._stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop:
            ws, priv = current()
            with self._lock:
                self._peak_ws = max(self._peak_ws, ws)
                self._peak_priv = max(self._peak_priv, priv)
            time.sleep(self.interval)

    @contextmanager
    def phase(self, name: str):
        ws0, priv0 = current()
        with self._lock:
            self._peak_ws, self._peak_priv = ws0, priv0
        t0 = time.perf_counter()
        yield
        elapsed = time.perf_counter() - t0
        ws1, priv1 = current()
        with self._lock:
            peak_ws, peak_priv = max(self._peak_ws, ws1), max(self._peak_priv, priv1)
        self.phases[name] = {
            "sec": round(elapsed, 3),
            "ws_before_mb": mb(ws0), "ws_after_mb": mb(ws1), "ws_peak_mb": mb(peak_ws),
            "private_peak_mb": mb(peak_priv), "private_after_mb": mb(priv1),
        }

    def stop(self):
        self._stop = True
