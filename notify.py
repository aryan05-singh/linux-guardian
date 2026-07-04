"""
Generic escalation channel for guardian.py.

Pick a method in config.yaml's `notify:` block: webhook (Slack/Discord/any
JSON-accepting endpoint), telegram, or stdout (default, useful for local
testing). Add a new method here and register it in make_notifier if you
need something else (email, PagerDuty, etc.) — the orchestrator only ever
calls notify(text), so it doesn't need to know which one is active.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Callable


def _post_json(url: str, payload: dict, timeout: int = 10) -> None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        print(f"[notify] webhook failed: {e}", file=sys.stderr)


def make_notifier(cfg: dict) -> Callable[[str], None]:
    method = cfg.get("method", "stdout")

    if method == "webhook":
        url = cfg["url"]

        def notify(text: str) -> None:
            _post_json(url, {"text": text})

        return notify

    if method == "telegram":
        bot_token = cfg["bot_token"]
        chat_id = cfg["chat_id"]
        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        def notify(text: str) -> None:
            _post_json(api_url, {"chat_id": chat_id, "text": text})

        return notify

    def notify(text: str) -> None:
        # stderr, not stdout, so --json output stays machine-parseable even
        # when notifications fire in the same run.
        print(f"[notify] {text}", file=sys.stderr)

    return notify
