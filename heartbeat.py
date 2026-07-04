"""
Dead man's switch: ping an external monitor to prove *guardian itself* is
still running on schedule.

If cron/systemd stops firing entirely — the box is off, the crontab got
wiped, the script started crashing before it could even load its config —
guardian has no way to escalate that fact itself, because the thing that's
broken is the very process that would normally notice and escalate. An
external uptime service (e.g. https://healthchecks.io, free tier) is the
only thing that can notice a ping stopped arriving and alert you through
its own independent channel.

Ping fires once per run, only after every check has completed without an
unhandled exception — see the call site in guardian.py's run().
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request


def ping_heartbeat(url: str, timeout: int = 10) -> None:
    try:
        urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.URLError as e:
        print(f"[heartbeat] ping failed: {e}", file=sys.stderr)
