"""
System resource monitor — reads RAM, CPU, Disk, and bot process memory.

Requires: psutil>=5.9  (already in requirements.txt)

Usage:
    from app.utils.sysmonitor import get_resource_snapshot
    snap = get_resource_snapshot()
    # snap["ram"]["percent"]       -> 72.4
    # snap["cpu"]["percent"]       -> 15.2
    # snap["disk_root"]["free_gb"] -> 120.3
    # snap["bot_ram_mb"]           -> 45.2
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSUTIL_AVAILABLE = False


def _fmt_gb(bytes_val: int) -> float:
    return round(bytes_val / (1024 ** 3), 2)


def _fmt_mb(bytes_val: int) -> float:
    return round(bytes_val / (1024 ** 2), 2)


def _dir_size_mb(path: Path) -> float:
    """Recursively calculate directory size in MB (best-effort)."""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        pass
    return _fmt_mb(total)


def get_resource_snapshot(workspace_dir: Path | None = None) -> dict[str, Any]:
    """
    Return a dict with current system resource usage.

    Keys:
        ram         — total_gb, used_gb, available_gb, percent
        cpu         — percent (non-blocking, uses 0.1s interval)
        disk_root   — total_gb, used_gb, free_gb, percent
        disk_workspace — same as disk_root but for workspace drive/mount
        workspace_size_mb — size of the workspace directory
        db_size_kb  — size of agent.db
        bot_ram_mb  — memory used by this Python process
        psutil_available — bool (False if psutil not installed)
    """
    if not _PSUTIL_AVAILABLE:
        return {
            "psutil_available": False,
            "ram": {},
            "cpu": {},
            "disk_root": {},
            "disk_workspace": {},
            "workspace_size_mb": 0.0,
            "db_size_kb": 0.0,
            "bot_ram_mb": 0.0,
        }

    # ── RAM ──────────────────────────────────────────────────────────────
    vm = psutil.virtual_memory()
    ram = {
        "total_gb": _fmt_gb(vm.total),
        "used_gb": _fmt_gb(vm.used),
        "available_gb": _fmt_gb(vm.available),
        "percent": round(vm.percent, 1),
    }

    # ── CPU ──────────────────────────────────────────────────────────────
    cpu_pct = psutil.cpu_percent(interval=0.1)
    cpu = {
        "percent": round(cpu_pct, 1),
        "count": psutil.cpu_count(logical=True),
    }

    # ── Disk (system root) ───────────────────────────────────────────────
    try:
        disk_root_path = "C:\\" if os.name == "nt" else "/"
        du_root = psutil.disk_usage(disk_root_path)
        disk_root = {
            "total_gb": _fmt_gb(du_root.total),
            "used_gb": _fmt_gb(du_root.used),
            "free_gb": _fmt_gb(du_root.free),
            "percent": round(du_root.percent, 1),
        }
    except Exception:
        disk_root = {}

    # ── Disk (workspace drive) ───────────────────────────────────────────
    disk_workspace: dict[str, Any] = {}
    try:
        from app.config.settings import settings
        ws_path = workspace_dir or settings.WORKSPACE_DIR
        if ws_path.exists():
            du_ws = psutil.disk_usage(str(ws_path))
        else:
            # Use workspace drive root even if dir doesn't exist yet
            drive = Path(str(ws_path).split("\\")[0] + "\\") if os.name == "nt" else Path("/")
            du_ws = psutil.disk_usage(str(drive))
        disk_workspace = {
            "total_gb": _fmt_gb(du_ws.total),
            "used_gb": _fmt_gb(du_ws.used),
            "free_gb": _fmt_gb(du_ws.free),
            "percent": round(du_ws.percent, 1),
        }
    except Exception:
        disk_workspace = disk_root.copy()

    # ── Workspace folder size ────────────────────────────────────────────
    workspace_size_mb = 0.0
    try:
        from app.config.settings import settings
        ws = workspace_dir or settings.WORKSPACE_DIR
        if ws.exists():
            workspace_size_mb = _dir_size_mb(ws)
    except Exception:
        pass

    # ── agent.db size ────────────────────────────────────────────────────
    db_size_kb = 0.0
    try:
        db_path = Path("agent.db")
        if db_path.exists():
            db_size_kb = round(db_path.stat().st_size / 1024, 1)
    except Exception:
        pass

    # ── Bot process RAM ──────────────────────────────────────────────────
    bot_ram_mb = 0.0
    try:
        proc = psutil.Process(os.getpid())
        bot_ram_mb = _fmt_mb(proc.memory_info().rss)
    except Exception:
        pass

    return {
        "psutil_available": True,
        "ram": ram,
        "cpu": cpu,
        "disk_root": disk_root,
        "disk_workspace": disk_workspace,
        "workspace_size_mb": workspace_size_mb,
        "db_size_kb": db_size_kb,
        "bot_ram_mb": bot_ram_mb,
    }
