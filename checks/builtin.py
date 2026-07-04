"""
Generic, config-driven health checks.

Each factory function takes a check's config dict (one entry from the
`checks:` list in config.yaml) and returns a zero-arg check() function.
Calling check() returns:

    {
        "name": str,             # unique check id (from config)
        "ok": bool,               # True = healthy, no action needed
        "detail": str,             # human-readable status
        "fix": callable | None,    # zero-arg function to attempt a fix, or None
    }

Fixes are deterministic shell commands or systemctl restarts — no LLM
judgment calls in the critical path, so it's safe to auto-remediate
without human review. A check with no fix_command always escalates.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional


def _run(cmd, shell: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)


def _shell_fix(fix_command: Optional[str]) -> Optional[Callable[[], None]]:
    if not fix_command:
        return None

    def fix() -> None:
        _run(fix_command, shell=True)

    return fix


def systemd_service_check(cfg: dict) -> Callable[[], dict]:
    name = cfg["name"]
    service = cfg["service"]
    scope_flag = ["--user"] if cfg.get("scope", "user") == "user" else []
    auto_fix = cfg.get("auto_fix", True)

    def check() -> dict:
        result = _run(["systemctl", *scope_flag, "is-active", service])
        active = result.stdout.strip() == "active"

        def fix() -> None:
            _run(["systemctl", *scope_flag, "restart", service])

        return {
            "name": name,
            "ok": active,
            "detail": f"systemctl status: {result.stdout.strip() or result.stderr.strip()}",
            "fix": fix if (not active and auto_fix) else None,
        }

    return check


def disk_space_check(cfg: dict) -> Callable[[], dict]:
    name = cfg["name"]
    path = cfg.get("path", "/")
    threshold_pct = cfg.get("threshold_pct", 90)
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        usage = shutil.disk_usage(path)
        pct_used = usage.used / usage.total * 100
        ok = pct_used < threshold_pct
        return {
            "name": name,
            "ok": ok,
            "detail": f"{pct_used:.1f}% used at {path} (threshold {threshold_pct}%)",
            "fix": _shell_fix(fix_command) if not ok else None,
        }

    return check


def file_freshness_check(cfg: dict) -> Callable[[], dict]:
    """Generic 'did the scheduled job run recently' check — point it at any
    marker file a cron job or systemd timer touches on success."""
    name = cfg["name"]
    marker_file = Path(cfg["marker_file"])
    max_age_hours = cfg.get("max_age_hours", 24)
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        if not marker_file.exists():
            return {
                "name": name,
                "ok": False,
                "detail": f"marker file missing: {marker_file}",
                "fix": _shell_fix(fix_command),
            }
        age_hours = (time.time() - marker_file.stat().st_mtime) / 3600
        ok = age_hours < max_age_hours
        return {
            "name": name,
            "ok": ok,
            "detail": f"last updated {age_hours:.1f}h ago (max {max_age_hours}h)",
            "fix": _shell_fix(fix_command) if not ok else None,
        }

    return check


def log_pattern_check(cfg: dict) -> Callable[[], dict]:
    """Generic crash-loop / recurring-error detector — tail a log file and
    count regex matches in the last N lines."""
    name = cfg["name"]
    log_file = Path(cfg["log_file"])
    window_lines = cfg.get("window_lines", 500)
    pattern = re.compile(cfg["pattern"])
    max_matches = cfg.get("max_matches", 1)
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        if not log_file.exists():
            return {"name": name, "ok": True, "detail": "log file not found, skipping", "fix": None}

        with open(log_file, "r", errors="ignore") as f:
            lines = f.readlines()[-window_lines:]

        matches = [l for l in lines if pattern.search(l)]
        ok = len(matches) < max_matches
        return {
            "name": name,
            "ok": ok,
            "detail": f"{len(matches)} matches in last {window_lines} lines (max {max_matches})",
            "fix": _shell_fix(fix_command) if not ok else None,
        }

    return check


def command_check(cfg: dict) -> Callable[[], dict]:
    """Escape hatch: any shell command that exits 0 for healthy, non-zero
    for unhealthy (curl health endpoints, custom scripts, etc.)."""
    name = cfg["name"]
    check_command = cfg["check_command"]
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        result = _run(check_command, shell=True)
        ok = result.returncode == 0
        output = (result.stdout.strip() or result.stderr.strip())[:200]
        return {
            "name": name,
            "ok": ok,
            "detail": f"exit code {result.returncode}: {output}",
            "fix": _shell_fix(fix_command) if not ok else None,
        }

    return check


CHECK_TYPES = {
    "systemd_service": systemd_service_check,
    "disk_space": disk_space_check,
    "file_freshness": file_freshness_check,
    "log_pattern": log_pattern_check,
    "command": command_check,
}


def build_checks(configs: list) -> list:
    checks = []
    for cfg in configs:
        check_type = cfg["type"]
        if check_type not in CHECK_TYPES:
            raise ValueError(f"Unknown check type: {check_type!r} (name={cfg.get('name')})")
        checks.append(CHECK_TYPES[check_type](cfg))
    return checks
