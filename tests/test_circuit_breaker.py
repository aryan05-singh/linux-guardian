import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from guardian import record_fix_attempt


def test_fix_attempts_accumulate_within_window():
    state = {}
    assert record_fix_attempt(state, "svc", window_hours=6) == 1
    assert record_fix_attempt(state, "svc", window_hours=6) == 2
    assert record_fix_attempt(state, "svc", window_hours=6) == 3


def test_old_attempts_pruned_outside_window():
    state = {"svc": [time.time() - 7 * 3600]}  # 7h old, window is 6h
    count = record_fix_attempt(state, "svc", window_hours=6)
    # old entry pruned, only the new attempt just recorded should remain
    assert count == 1
    assert len(state["svc"]) == 1


def test_independent_checks_tracked_separately():
    state = {}
    record_fix_attempt(state, "svc_a", window_hours=6)
    record_fix_attempt(state, "svc_a", window_hours=6)
    record_fix_attempt(state, "svc_b", window_hours=6)
    assert len(state["svc_a"]) == 2
    assert len(state["svc_b"]) == 1
