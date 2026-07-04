import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from history import build_report, record_results


def test_uptime_percentage(tmp_path):
    db = tmp_path / "history.db"
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}])
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}])
    record_results(db, [{"name": "svc", "status": "escalated", "detail": ""}])
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}])

    report = build_report(db)
    entry = next(r for r in report if r["name"] == "svc")
    assert entry["total_runs"] == 4
    assert entry["uptime_pct"] == 75.0


def test_mttr_pairs_bad_episode_with_next_ok(tmp_path):
    db = tmp_path / "history.db"
    base = time.time()
    # simulate: healthy, breaks, stays broken for 100s, recovers
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}], ts=base)
    record_results(db, [{"name": "svc", "status": "escalated", "detail": ""}], ts=base + 10)
    record_results(db, [{"name": "svc", "status": "escalated", "detail": ""}], ts=base + 60)
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}], ts=base + 110)

    report = build_report(db)
    entry = next(r for r in report if r["name"] == "svc")
    # bad_since = base+10 (first non-ok), recovered at base+110 -> 100s MTTR
    assert entry["mttr_seconds"] == 100.0


def test_no_bad_episodes_gives_none_mttr(tmp_path):
    db = tmp_path / "history.db"
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}])
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}])

    report = build_report(db)
    entry = next(r for r in report if r["name"] == "svc")
    assert entry["mttr_seconds"] is None
    assert entry["uptime_pct"] == 100.0


def test_missing_db_returns_empty_report(tmp_path):
    assert build_report(tmp_path / "does_not_exist.db") == []


def test_since_hours_excludes_old_rows(tmp_path):
    import time

    db = tmp_path / "history.db"
    old_ts = time.time() - 10 * 3600  # 10 hours ago
    record_results(db, [{"name": "svc", "status": "ok", "detail": ""}], ts=old_ts)

    report = build_report(db, since_hours=1)  # only look at last 1 hour
    assert report == []
