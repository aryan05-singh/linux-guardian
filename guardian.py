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
import os
import re
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from checks.builtin import CHECK_TYPES, build_checks  # noqa: E402
from notify import make_notifier  # noqa: E402

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate_env(value):
    """Recursively replace ${VAR_NAME} in string config values with the
    matching environment variable, so secrets (bot tokens, webhook URLs)
    don't have to live in plaintext in config.yaml."""
    if isinstance(value, str):
        def replace(match: "re.Match[str]") -> str:
            var_name = match.group(1)
            if var_name not in os.environ:
                raise KeyError(f"environment variable {var_name!r} referenced in config but not set")
            return os.environ[var_name]

        return _ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def load_config(config_path: Path) -> dict:
    raw = yaml.safe_load(config_path.read_text()) or {}
    return _interpolate_env(raw)


def validate_config(config: dict) -> list[str]:
    """Check the config is well-formed before actually running anything.
    Returns a list of human-readable error strings (empty = valid)."""
    errors: list[str] = []

    try:
        make_notifier(config.get("notify", {}))
    except KeyError as e:
        errors.append(f"notify config missing key: {e}")

    checks_cfg = config.get("checks", [])
    if not checks_cfg:
        errors.append("no checks defined in config")

    seen_names: set[str] = set()
    for cfg in checks_cfg:
        name = cfg.get("name")
        if not name:
            errors.append(f"check missing 'name': {cfg}")
            continue
        if name in seen_names:
            errors.append(f"duplicate check name: {name!r}")
        seen_names.add(name)

        check_type = cfg.get("type")
        if check_type not in CHECK_TYPES:
            errors.append(f"check {name!r} has unknown type: {check_type!r}")
            continue
        try:
            CHECK_TYPES[check_type](cfg)
        except KeyError as e:
            errors.append(f"check {name!r} missing required key: {e}")
        except Exception as e:
            errors.append(f"check {name!r} invalid: {e}")

    return errors


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


def run(config_path: Path, dry_run: bool = False, only_check: str | None = None) -> bool:
    config = load_config(config_path)

    state_file = Path(config.get("state_file", "guardian_state.json"))
    log_file = Path(config.get("log_file", "guardian.log"))
    cb_cfg = config.get("circuit_breaker", {})
    max_auto_fixes = cb_cfg.get("max_auto_fixes", 3)
    window_hours = cb_cfg.get("window_hours", 6)

    notify = make_notifier(config.get("notify", {}))
    checks_cfg = config.get("checks", [])
    if only_check:
        checks_cfg = [c for c in checks_cfg if c.get("name") == only_check]
        if not checks_cfg:
            print(f"No check named {only_check!r} found in config")
            return False
    checks = build_checks(checks_cfg)

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

        if dry_run:
            _log(log_file, f"DRY-RUN: would attempt fix for {name} ({result['detail']})")
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
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be fixed without running any fix"
    )
    parser.add_argument(
        "--validate-config", action="store_true", help="Validate the config file and exit, without running checks"
    )
    parser.add_argument("--check", metavar="NAME", help="Run only the check with this name")
    args = parser.parse_args()

    if args.validate_config:
        config = load_config(args.config)
        errors = validate_config(config)
        if errors:
            print("Config validation FAILED:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        print("Config OK")
        sys.exit(0)

    ok = run(args.config, dry_run=args.dry_run, only_check=args.check)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
