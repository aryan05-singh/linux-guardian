# linux-guardian

*by Aryan Singh*

A small, config-driven self-healing health-check runner for any Linux server. No agents, no daemons, no LLM in the critical path — just deterministic checks, deterministic fixes, and a circuit breaker so it never hammers a problem it can't actually solve.

## Why

Most servers fail in a handful of boring, repetitive ways: a service crashes and doesn't restart, disk fills up with old logs, a cron job silently stops running, a background auth token expires. Fixing these by hand is easy but tedious, and if you're not watching, you find out from a user complaint instead of a check.

`linux-guardian` runs on a schedule (cron / systemd timer), checks a list of things you define in YAML, and for anything that's broken:

1. Runs a deterministic fix (if one is configured).
2. Re-checks to confirm the fix actually held.
3. If it's still broken, or there's no safe fix for this kind of check, it escalates to a human via webhook/Telegram/stdout instead of guessing.

A **circuit breaker** tracks how many times each check has been auto-fixed in a rolling time window (default: 3 fixes / 6 hours). If a check needs fixing every run, that's a sign of a deeper problem a mechanical restart can't solve — the breaker trips and escalates instead of retrying forever.

## Design principle: no LLM judgment calls in the fix path

Every check and fix here is a plain deterministic function — `systemctl restart`, `find -delete`, a shell command. There is no LLM deciding what to do when something breaks. That's deliberate: an autonomous agent that can restart services and delete files needs to be predictable and auditable, not "creative." Where the fix genuinely isn't safe to guess (e.g. a crash loop with an unknown root cause), the check is configured with no `fix_command` at all, so it always escalates.

## Built-in check types

| Type | What it does | Example use |
|---|---|---|
| `systemd_service` | Is a systemd unit active? Fix = restart. | Web server, bot process, background worker |
| `disk_space` | Is usage under a threshold on a given path? Fix = your cleanup command. | Prune old logs before disk fills up |
| `file_freshness` | Has a marker file been touched recently? Fix = re-run the job. | Did last night's backup/cron actually run? |
| `log_pattern` | Does a log file contain more than N matches of a regex in the last window? | Crash-loop / recurring-error detection |
| `command` | Escape hatch — any shell command, exit 0 = healthy. | HTTP health endpoints, custom auth checks |

Add a new type by writing one function in `checks/builtin.py` that returns a `check()` closure — see the existing five for the pattern.

## Quick start

```bash
git clone <this-repo> linux-guardian && cd linux-guardian
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cp config.example.yaml config.yaml
# edit config.yaml: your service names, paths, thresholds, notify target

.venv/bin/python guardian.py --config config.yaml
```

Wire it to a schedule:

```cron
# crontab -e
*/15 * * * * cd /path/to/linux-guardian && .venv/bin/python guardian.py --config config.yaml
```

Exit code is `0` if everything is healthy (or was auto-fixed), `1` if anything escalated — useful if you also want your cron/monitoring wrapper to alert on a non-zero exit.

## Notifications

Configured under `notify:` in `config.yaml`, one of:

- `stdout` (default) — prints to the console/log, good for local testing
- `webhook` — POSTs `{"text": "..."}` to any URL (Slack incoming webhooks, Discord, a custom endpoint)
- `telegram` — sends via a bot token + chat ID

## Example config

See [`config.example.yaml`](config.example.yaml) for a fully commented example covering all five check types.

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

Covers the built-in checks (pass/fail/fix-availability for each type) and the circuit breaker's time-window logic (attempts accumulate, prune outside the window, tracked independently per check).

## Project origin

This started as a private tool watching one specific personal automation stack, then got generalized here into a config-driven, dependency-free version anyone can point at their own server.

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by **Aryan Singh** — [GitHub](https://github.com/aryan05-singh)
