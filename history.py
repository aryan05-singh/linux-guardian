"""
Persist every check result to SQLite so `guardian.py --report` can compute,
per check, an uptime percentage and mean-time-to-recovery (MTTR) over a
time window — not just "is it healthy right now" but "how healthy has it
actually been."

MTTR here means: every time a check goes from healthy to unhealthy, how
long on average until it's next reported healthy again. A check that gets
auto-fixed in one run has a short MTTR; one that stays broken for days
(only escalating) has a long one.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS check_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT
        )
        """
    )
    return conn


def record_results(db_path: Path, results: list[dict], ts: float | None = None) -> None:
    if not results:
        return
    now = ts if ts is not None else time.time()
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO check_results (ts, name, status, detail) VALUES (?, ?, ?, ?)",
            [(now, r["name"], r["status"], r.get("detail", "")) for r in results],
        )
        conn.commit()
    finally:
        conn.close()


def build_report(db_path: Path, since_hours: float = 24 * 7) -> list[dict]:
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        cutoff = time.time() - since_hours * 3600
        rows = conn.execute(
            "SELECT ts, name, status FROM check_results WHERE ts >= ? ORDER BY name, ts",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    by_check: dict[str, list[tuple[float, str]]] = {}
    for ts, name, status in rows:
        by_check.setdefault(name, []).append((ts, status))

    report = []
    for name, entries in by_check.items():
        total = len(entries)
        ok_count = sum(1 for _, status in entries if status == "ok")
        uptime_pct = (ok_count / total * 100) if total else 0.0

        recovery_times: list[float] = []
        bad_since: float | None = None
        for ts, status in entries:
            if status != "ok" and bad_since is None:
                bad_since = ts
            elif status == "ok" and bad_since is not None:
                recovery_times.append(ts - bad_since)
                bad_since = None
        mttr_seconds = sum(recovery_times) / len(recovery_times) if recovery_times else None

        report.append(
            {
                "name": name,
                "total_runs": total,
                "uptime_pct": round(uptime_pct, 1),
                "mttr_seconds": round(mttr_seconds, 1) if mttr_seconds is not None else None,
            }
        )

    return report
