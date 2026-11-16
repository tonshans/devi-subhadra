"""Cheap local probes for /status: disk space and CPU temperature."""

from __future__ import annotations

import shutil
from pathlib import Path

THERMAL_ROOT = Path("/sys/class/thermal")


def disk_space_line(path: str | Path) -> str:
    target = Path(path)
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return f"disk: n/a ({target})"
    used_gb = (usage.total - usage.free) / 1024**3
    total_gb = usage.total / 1024**3
    percent = round((usage.total - usage.free) / usage.total * 100) if usage.total else 0
    return f"disk: {used_gb:.1f}/{total_gb:.1f} GB used ({percent}%) on {target}"


def cpu_temperature_c(root: Path = THERMAL_ROOT) -> float | None:
    readings: list[float] = []
    for zone in root.glob("thermal_zone*/temp"):
        try:
            readings.append(int(zone.read_text().strip()) / 1000)
        except (OSError, ValueError):
            continue
    return max(readings) if readings else None


def cpu_temp_line(root: Path = THERMAL_ROOT) -> str:
    value = cpu_temperature_c(root)
    return f"cpu temp: {value:.1f} C" if value is not None else "cpu temp: n/a"
