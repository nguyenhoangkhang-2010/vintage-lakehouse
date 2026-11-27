"""A description of the machine and environment a benchmark ran on."""

from __future__ import annotations

import os
import platform
import sys
from importlib import metadata
from typing import Any, ClassVar

_PACKAGES = ("vintage-lakehouse", "pyiceberg", "duckdb", "pyarrow", "numpy", "pandas")


def _memory_gb() -> float | None:
    """Physical memory in GiB, where the standard library can tell."""
    try:
        if sys.platform != "win32":
            pages = os.sysconf("SC_PHYS_PAGES")  # type: ignore[attr-defined,unused-ignore]
            size = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined,unused-ignore]
            return round(float(size) * float(pages) / 2**30, 1)
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_: ClassVar[list[tuple[str, Any]]] = [
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong),
                    ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("extended", ctypes.c_ulonglong),
                ]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined,unused-ignore]
                ctypes.byref(status)
            ):
                return round(float(status.total) / 2**30, 1)
    except (OSError, ValueError, AttributeError):
        return None
    return None


def describe_machine() -> dict[str, Any]:
    """OS, CPU, cores, memory (when known), Python and library versions."""
    packages: dict[str, str] = {}
    for name in _PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return {
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine() or "unknown",
        "cores": os.cpu_count() or 1,
        "memory_gb": _memory_gb(),
        "python": platform.python_version(),
        "packages": packages or {"vintage-lakehouse": "not installed"},
    }
