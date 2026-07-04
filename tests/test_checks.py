import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from checks.builtin import (
    command_check,
    disk_space_check,
    file_freshness_check,
    log_pattern_check,
)


def test_command_check_ok():
    check = command_check({"name": "true_cmd", "check_command": "exit 0"})
    result = check()
    assert result["ok"] is True
    assert result["fix"] is None


def test_command_check_fails_and_offers_fix():
    check = command_check({
        "name": "false_cmd",
        "check_command": "exit 1",
        "fix_command": "echo fixed",
    })
    result = check()
    assert result["ok"] is False
    assert callable(result["fix"])


def test_disk_space_check_threshold(tmp_path):
    # threshold 0 guarantees "failure" regardless of actual disk usage
    check = disk_space_check({"name": "disk", "path": str(tmp_path), "threshold_pct": 0})
    result = check()
    assert result["ok"] is False

    check_ok = disk_space_check({"name": "disk", "path": str(tmp_path), "threshold_pct": 100})
    assert check_ok()["ok"] is True


def test_file_freshness_missing_marker(tmp_path):
    check = file_freshness_check({
        "name": "backup",
        "marker_file": str(tmp_path / "missing"),
        "max_age_hours": 1,
    })
    result = check()
    assert result["ok"] is False
    assert "missing" in result["detail"]


def test_file_freshness_fresh_marker(tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("ok")
    check = file_freshness_check({
        "name": "backup",
        "marker_file": str(marker),
        "max_age_hours": 1,
    })
    assert check()["ok"] is True


def test_file_freshness_stale_marker(tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("ok")
    old_time = time.time() - 3600 * 2
    import os
    os.utime(marker, (old_time, old_time))

    check = file_freshness_check({
        "name": "backup",
        "marker_file": str(marker),
        "max_age_hours": 1,
    })
    result = check()
    assert result["ok"] is False


def test_log_pattern_missing_file_ok(tmp_path):
    check = log_pattern_check({
        "name": "crash_loop",
        "log_file": str(tmp_path / "missing.log"),
        "pattern": "FATAL",
    })
    assert check()["ok"] is True


def test_log_pattern_detects_matches(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("INFO ok\nFATAL crash\nFATAL crash\nFATAL crash\n")
    check = log_pattern_check({
        "name": "crash_loop",
        "log_file": str(log),
        "pattern": "FATAL",
        "max_matches": 2,
    })
    result = check()
    assert result["ok"] is False
    assert "3 matches" in result["detail"]
