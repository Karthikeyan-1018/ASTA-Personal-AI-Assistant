"""Real, local-only telemetry collection for the System Monitor API.

This module is not an LLM tool and never changes OS state. Access is gated by
the same existing read-only System Information and Process Inspection tool
permissions at the API layer. Process data is cached briefly so a 2-second
dashboard refresh does not repeatedly sample every process.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.tools.implementations.process_inspection import (
    ResourceSummaryInput,
    SystemResourceSummaryTool,
)
from app.tools.implementations.system_info import (
    _collect_cpu_info,
    _collect_disk_info,
    _collect_hostname,
    _collect_os_info,
)


logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_PREVIOUS_NETWORK: dict[str, tuple[int, int]] = {}
_PREVIOUS_NETWORK_TIME: float | None = None
_PREVIOUS_DISK: tuple[int, int] | None = None
_PREVIOUS_DISK_TIME: float | None = None
_PREVIOUS_CPU: list[tuple[float, float]] | None = None
_PROCESS_CACHE: dict[str, Any] | None = None
_PROCESS_CACHE_TIME = 0.0
_PROCESS_CACHE_SECONDS = 10.0


def _optional_psutil():
    try:
        import psutil

        return psutil
    except ImportError:
        return None


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _cpu_model(fallback: str | None) -> str | None:
    if os.name != "nt":
        return fallback
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            0,
            winreg.KEY_READ,
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "ProcessorNameString")
        cleaned = " ".join(str(value).split())
        return cleaned[:200] if cleaned else fallback
    except (OSError, ImportError):
        return fallback


def _cpu(psutil, now: float) -> dict[str, Any]:
    global _PREVIOUS_CPU
    try:
        samples = psutil.cpu_times(percpu=True)
        current = [
            (sum(float(value) for value in cpu_time), float(cpu_time.idle))
            for cpu_time in samples
        ]
        per_core: list[float] | None = None
        usage = None
        if _PREVIOUS_CPU is not None:
            percentages = []
            for index, (total, idle) in enumerate(current):
                if index >= len(_PREVIOUS_CPU):
                    continue
                old_total, old_idle = _PREVIOUS_CPU[index]
                total_delta = total - old_total
                idle_delta = idle - old_idle
                if total_delta > 0:
                    percentages.append(
                        round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)
                    )
            if percentages:
                per_core = percentages
                usage = round(sum(percentages) / len(percentages), 1)
        _PREVIOUS_CPU = current
        frequency = psutil.cpu_freq()
        details = _collect_cpu_info()
        return {
            "available": True,
            "reason": None if usage is not None else "Waiting for the next real CPU counter sample.",
            "model": _cpu_model(details.get("model")),
            "logical_processors": details.get("logical_processors"),
            "physical_cores": details.get("physical_cores"),
            "architecture": platform.machine() or None,
            "usage_percent": usage,
            "per_core_percent": per_core,
            "frequency_mhz": round(float(frequency.current), 1) if frequency and frequency.current else None,
        }
    except Exception:
        logger.debug("System Monitor CPU collection unavailable.", exc_info=True)
        return _unavailable("CPU counters are unavailable on this host.")


def _memory(psutil) -> dict[str, Any]:
    try:
        value = psutil.virtual_memory()
        return {
            "available": True,
            "reason": None,
            "total_bytes": int(value.total),
            "used_bytes": int(value.used),
            "available_bytes": int(value.available),
            "usage_percent": round(float(value.percent), 1),
        }
    except Exception:
        logger.debug("System Monitor memory collection unavailable.", exc_info=True)
        return _unavailable("Physical memory counters are unavailable.")


def _disk(psutil, now: float) -> dict[str, Any]:
    global _PREVIOUS_DISK, _PREVIOUS_DISK_TIME
    try:
        info = _collect_disk_info()
        counters = psutil.disk_io_counters()
        read_bps = write_bps = None
        if counters and _PREVIOUS_DISK is not None and _PREVIOUS_DISK_TIME is not None:
            elapsed = now - _PREVIOUS_DISK_TIME
            if elapsed > 0:
                read_bps = round(max(0, counters.read_bytes - _PREVIOUS_DISK[0]) / elapsed)
                write_bps = round(max(0, counters.write_bytes - _PREVIOUS_DISK[1]) / elapsed)
        if counters:
            _PREVIOUS_DISK = (int(counters.read_bytes), int(counters.write_bytes))
            _PREVIOUS_DISK_TIME = now
        return {
            "available": bool(info.get("available")),
            "reason": None if info.get("available") else "No approved local disk roots are available.",
            "drives": info.get("drives", []),
            "io_available": counters is not None,
            "io_reason": None if counters is not None else "Windows disk I/O counters are not supported by this host/runtime.",
            "read_bytes_per_second": read_bps,
            "write_bytes_per_second": write_bps,
        }
    except Exception:
        logger.debug("System Monitor disk collection unavailable.", exc_info=True)
        return _unavailable("Disk counters are unavailable.")


def _network(psutil, now: float) -> dict[str, Any]:
    global _PREVIOUS_NETWORK, _PREVIOUS_NETWORK_TIME
    try:
        counters = psutil.net_io_counters(pernic=True) or {}
        stats = psutil.net_if_stats()
        current: dict[str, tuple[int, int]] = {}
        interfaces = []
        elapsed = now - _PREVIOUS_NETWORK_TIME if _PREVIOUS_NETWORK_TIME is not None else 0.0
        for name, counter in counters.items():
            sent, received = int(counter.bytes_sent), int(counter.bytes_recv)
            current[name] = (sent, received)
            previous = _PREVIOUS_NETWORK.get(name)
            stat = stats.get(name)
            interfaces.append({
                "name": str(name)[:128],
                "is_up": bool(stat.isup) if stat else None,
                "speed_mbps": int(stat.speed) if stat and stat.speed > 0 else None,
                "bytes_sent": sent,
                "bytes_received": received,
                "upload_bytes_per_second": round(max(0, sent - previous[0]) / elapsed) if previous and elapsed > 0 else None,
                "download_bytes_per_second": round(max(0, received - previous[1]) / elapsed) if previous and elapsed > 0 else None,
            })
        sent_total = sum(item[0] for item in current.values())
        received_total = sum(item[1] for item in current.values())
        # Aggregate throughput uses the same real per-interface counter deltas.
        up = down = None
        if elapsed > 0 and current:
            deltas = [
                (sent - _OLD_SENT, received - _OLD_RECEIVED)
                for name, (sent, received) in current.items()
                for _OLD_SENT, _OLD_RECEIVED in [_PREVIOUS_NETWORK.get(name, (sent, received))]
            ]
            up = round(sum(max(0, item[0]) for item in deltas) / elapsed)
            down = round(sum(max(0, item[1]) for item in deltas) / elapsed)
        _PREVIOUS_NETWORK = current
        _PREVIOUS_NETWORK_TIME = now
        return {
            "available": bool(counters),
            "reason": None if counters else "No network interfaces are reported by Windows.",
            "upload_bytes_per_second": up,
            "download_bytes_per_second": down,
            "bytes_sent": sent_total,
            "bytes_received": received_total,
            "interfaces": interfaces,
        }
    except Exception:
        logger.debug("System Monitor network collection unavailable.", exc_info=True)
        return _unavailable("Network interface counters are unavailable.")


def _gpu() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return _unavailable("NVIDIA Management Interface (nvidia-smi) is not installed or available on PATH.")
    query = [
        executable,
        "--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            query,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return _unavailable("NVIDIA reports GPU telemetry unavailable.")
        rows = list(csv.reader(io.StringIO(completed.stdout)))
        if not rows or len(rows[0]) < 6:
            return _unavailable("NVIDIA returned no supported GPU measurements.")
        row = [value.strip() for value in rows[0]]
        def number(index: int) -> float | None:
            try:
                value = row[index]
                return None if not value or value.upper() == "N/A" else float(value)
            except (ValueError, IndexError):
                return None
        total_mib = number(2)
        used_mib = number(3)
        return {
            "available": True,
            "reason": None,
            "model": row[0] or None,
            "usage_percent": number(1),
            "memory_total_mib": total_mib,
            "memory_used_mib": used_mib,
            "memory_available_mib": max(0, total_mib - used_mib) if total_mib is not None and used_mib is not None else None,
            "temperature_celsius": number(4),
            "power_watts": number(5),
        }
    except subprocess.TimeoutExpired:
        return _unavailable("GPU telemetry query timed out.")
    except Exception:
        logger.debug("System Monitor GPU collection unavailable.", exc_info=True)
        return _unavailable("GPU telemetry could not be queried.")


def _system(psutil) -> dict[str, Any]:
    try:
        boot_epoch = float(psutil.boot_time())
        uptime = max(0, int(time.time() - boot_epoch))
        return {
            "available": True,
            "reason": None,
            "os": _collect_os_info(),
            "hostname": _collect_hostname().get("hostname"),
            "architecture": platform.machine() or None,
            "boot_time": datetime.fromtimestamp(boot_epoch, timezone.utc).isoformat(),
            "uptime_seconds": uptime,
        }
    except Exception:
        return _unavailable("System boot time or host information is unavailable.")


def _processes(now: float) -> dict[str, Any]:
    global _PROCESS_CACHE, _PROCESS_CACHE_TIME
    if _PROCESS_CACHE is not None and now - _PROCESS_CACHE_TIME < _PROCESS_CACHE_SECONDS:
        return _PROCESS_CACHE
    try:
        result = SystemResourceSummaryTool().collect(ResourceSummaryInput(limit=25))
        _PROCESS_CACHE = result
        _PROCESS_CACHE_TIME = now
        return result
    except Exception:
        logger.debug("System Monitor process collection unavailable.", exc_info=True)
        return _unavailable("Read-only process inspection is unavailable.")


def collect_snapshot(*, include_system_info: bool, include_processes: bool) -> dict[str, Any]:
    """Collect one host snapshot without blocking the ASGI event loop."""

    with _LOCK:
        now = time.monotonic()
        psutil = _optional_psutil()
        if psutil is None:
            missing = _unavailable("Install the pinned psutil dependency to enable host telemetry.")
            base = {
                "cpu": missing,
                "memory": missing,
                "disk": missing,
                "network": missing,
                "gpu": _gpu(),
                "system": missing,
                "processes": _unavailable("Process data is collected by the existing Windows inspection tool."),
            }
        else:
            if include_system_info:
                base = {
                    "cpu": _cpu(psutil, now),
                    "memory": _memory(psutil),
                    "disk": _disk(psutil, now),
                    "network": _network(psutil, now),
                    "gpu": _gpu(),
                    "system": _system(psutil),
                }
            else:
                disabled = _unavailable("Disabled by TOOL_OS_AUTOMATION_ENABLED and TOOL_SYSTEM_INFO_ENABLED.")
                base = {
                    "cpu": disabled,
                    "memory": disabled,
                    "disk": disabled,
                    "network": disabled,
                    "gpu": disabled,
                    "system": disabled,
                }
            base["processes"] = (
                _processes(now)
                if include_processes
                else _unavailable("Disabled by TOOL_OS_AUTOMATION_ENABLED and TOOL_PROCESS_INSPECTION_ENABLED.")
            )

        return {
            "collected_at": datetime.now(timezone.utc),
            "refresh_interval_seconds": max(1.0, min(10.0, float(settings.SYSTEM_MONITOR_REFRESH_SECONDS))),
            **base,
        }
