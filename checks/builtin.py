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


def ssl_cert_expiry_check(cfg: dict) -> Callable[[], dict]:
    """Warn before a TLS certificate expires. fix_command is typically a
    renewal command (e.g. certbot renew) — re-checking after renewal
    confirms it actually took effect."""
    import socket
    import ssl
    from datetime import datetime, timezone

    name = cfg["name"]
    hostname = cfg["hostname"]
    port = cfg.get("port", 443)
    warn_days = cfg.get("warn_days", 14)
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((hostname, port), timeout=10) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
            expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
            days_left = (expires - datetime.now(timezone.utc)).days
            ok = days_left > warn_days
            return {
                "name": name,
                "ok": ok,
                "detail": f"{hostname}:{port} cert expires in {days_left}d (warn threshold {warn_days}d)",
                "fix": _shell_fix(fix_command) if not ok else None,
            }
        except Exception as e:
            return {
                "name": name,
                "ok": False,
                "detail": f"could not verify cert for {hostname}:{port}: {e}",
                "fix": _shell_fix(fix_command),
            }

    return check


def port_open_check(cfg: dict) -> Callable[[], dict]:
    """Is something listening on host:port? Useful for databases, internal
    APIs, or anything not managed by systemd."""
    import socket

    name = cfg["name"]
    host = cfg.get("host", "127.0.0.1")
    port = cfg["port"]
    timeout = cfg.get("timeout", 5)
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ok = True
                detail = f"{host}:{port} is open"
        except OSError as e:
            ok = False
            detail = f"{host}:{port} not reachable: {e}"
        return {
            "name": name,
            "ok": ok,
            "detail": detail,
            "fix": _shell_fix(fix_command) if not ok else None,
        }

    return check


def process_running_check(cfg: dict) -> Callable[[], dict]:
    """Is a process matching this pattern running, regardless of whether
    it's managed by systemd? Backed by `pgrep -f`."""
    name = cfg["name"]
    pattern = cfg["pattern"]
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        result = _run(["pgrep", "-f", pattern])
        ok = result.returncode == 0
        return {
            "name": name,
            "ok": ok,
            "detail": f"pgrep -f {pattern!r}: {'running' if ok else 'no matching process'}",
            "fix": _shell_fix(fix_command) if not ok else None,
        }

    return check


def memory_usage_check(cfg: dict) -> Callable[[], dict]:
    """Linux-only: reads /proc/meminfo for used-memory percentage."""
    name = cfg["name"]
    threshold_pct = cfg.get("threshold_pct", 90)
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])

        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used_pct = (1 - available / total) * 100 if total else 0
        ok = used_pct < threshold_pct
        return {
            "name": name,
            "ok": ok,
            "detail": f"{used_pct:.1f}% memory used (threshold {threshold_pct}%)",
            "fix": _shell_fix(fix_command) if not ok else None,
        }

    return check


def cpu_load_check(cfg: dict) -> Callable[[], dict]:
    """Linux-only: 1-minute load average against a threshold."""
    import os

    name = cfg["name"]
    threshold = cfg.get("threshold", 4.0)
    fix_command = cfg.get("fix_command")

    def check() -> dict:
        load1, _load5, _load15 = os.getloadavg()
        ok = load1 < threshold
        return {
            "name": name,
            "ok": ok,
            "detail": f"1-min load average {load1:.2f} (threshold {threshold})",
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
    "ssl_cert_expiry": ssl_cert_expiry_check,
    "port_open": port_open_check,
    "process_running": process_running_check,
    "memory_usage": memory_usage_check,
    "cpu_load": cpu_load_check,
}


def build_checks(configs: list) -> list:
    checks = []
    for cfg in configs:
        check_type = cfg["type"]
        if check_type not in CHECK_TYPES:
            raise ValueError(f"Unknown check type: {check_type!r} (name={cfg.get('name')})")
        checks.append(CHECK_TYPES[check_type](cfg))
    return checks
