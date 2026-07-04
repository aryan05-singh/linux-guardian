#!/usr/bin/env python3
"""
linux-guardian — a self-healing health-check runner for any Linux server.

Define checks in a YAML config (systemd services, disk space, stale cron
markers, log-pattern crash loops, or arbitrary shell commands). Each failing
check either runs its deterministic fix and re-verifies, or escalates via
your notifier (webhook, Telegram, or stdout) if it has no safe fix.

A circuit breaker stops auto-fixing a check that keeps failing within a
time window (default: 3 attempts / 6 hours) and escalates instead — a check
that needs fixing every run points at a deeper problem a mechanical retry
can't solve, and hammering it is worse than asking a human.

Usage:
    python3 guardian.py --config config.yaml

Intended to run on a schedule (cron/systemd timer), not continuously.
Exits 0 if all checks passed or were auto-fixed, 1 if anything escalated.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from checks.builtin import build_checks  # noqa: E402
from notify import make_notifier  # noqa: E402


def _log(log_file: Path, msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(line + "\n")


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {}


def save_state(state_file: Path, state: dict) -> None:
    state_file.write_text(json.dumps(state, indent=2))


def record_fix_attempt(state: dict, check_name: str, window_hours: float) -> int:
    """Record a fix attempt, prune entries outside the window, return count in window."""
    now = time.time()
    window_start = now - window_hours * 3600
    attempts = state.setdefault(check_name, [])
    attempts[:] = [t for t in attempts if t > window_start]
    attempts.append(now)
    return len(attempts)


def run(config_path: Path) -> bool:
    config = yaml.safe_load(config_path.read_text()) or {}

    state_file = Path(config.get("state_file", "guardian_state.json"))
    log_file = Path(config.get("log_file", "guardian.log"))
    cb_cfg = config.get("circuit_breaker", {})
    max_auto_fixes = cb_cfg.get("max_auto_fixes", 3)
    window_hours = cb_cfg.get("window_hours", 6)

    notify = make_notifier(config.get("notify", {}))
    checks = build_checks(config.get("checks", []))

    state = load_state(state_file)
    any_escalation = False

    for check_fn in checks:
        try:
            result = check_fn()
        except Exception as e:
            _log(log_file, f"CHECK ERROR: {e}")
            notify(f"⚠️ guardian: a check crashed: {e}")
            any_escalation = True
            continue

        name = result["name"]
        if result["ok"]:
            _log(log_file, f"OK   {name}: {result['detail']}")
            continue

        _log(log_file, f"FAIL {name}: {result['detail']}")

        if result["fix"] is None:
            notify(f"\U0001f534 guardian: {name} is failing with no auto-fix.\n{result['detail']}")
            any_escalation = True
            continue

        fix_count = record_fix_attempt(state, name, window_hours)
        if fix_count > max_auto_fixes:
            notify(
                f"\U0001f534 guardian: {name} has failed {fix_count} times in "
                f"{window_hours}h — stopping auto-fix, needs attention.\n{result['detail']}"
            )
            any_escalation = True
            continue

        _log(log_file, f"FIX  attempting fix for {name} (attempt {fix_count}/{max_auto_fixes})")
        try:
            result["fix"]()
        except Exception as e:
            _log(log_file, f"FIX ERROR: {name}: {e}")
            notify(f"\U0001f534 guardian: fix for {name} threw an error: {e}")
            any_escalation = True
            continue

        recheck = check_fn()
        if recheck["ok"]:
            _log(log_file, f"FIXED {name}")
        else:
            _log(log_file, f"FIX DID NOT HOLD: {name}: {recheck['detail']}")
            notify(f"\U0001f7e1 guardian: tried to fix {name} but it's still failing.\n{recheck['detail']}")
            any_escalation = True

    save_state(state_file, state)
    if not any_escalation:
        _log(log_file, "All checks OK, no escalation needed")
    return not any_escalation


def main() -> None:
    parser = argparse.ArgumentParser(description="linux-guardian: self-healing health checks")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    ok = run(args.config)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
