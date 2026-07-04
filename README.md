# linux-guardian

![tests](https://github.com/aryan05-singh/linux-guardian/actions/workflows/tests.yml/badge.svg)

*by Aryan Singh*

A small, config-driven self-healing health-check runner for any Linux server. No agents, no daemons, no LLM in the critical path — just deterministic checks, deterministic fixes, and a circuit breaker so it never hammers a problem it can't actually solve.

**No AI subscription, API key, or account of any kind required.** This is a plain Python script — clone it, write a YAML config, run it on a schedule. If you want chat notifications, plug in a free Telegram bot token (from [@BotFather](https://t.me/BotFather)) or a Slack/Discord webhook URL — both are unrelated to any AI product.

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
| `ssl_cert_expiry` | Does a TLS cert expire within N days? | Renewal reminders before a site goes down |
| `port_open` | Is something listening on host:port? | Databases / internal services not managed by systemd |
| `process_running` | Is a process matching a pattern running (`pgrep -f`)? | Non-systemd scripts and workers |
| `memory_usage` | Is RAM usage under a threshold? (Linux `/proc/meminfo`) | Catch memory leaks before OOM |
| `cpu_load` | Is the 1-minute load average under a threshold? | Catch runaway processes |

Add a new type by writing one function in `checks/builtin.py` that returns a `check()` closure — see the existing ones for the pattern.

## CLI flags

```bash
python guardian.py --config config.yaml                  # normal run
python guardian.py --config config.yaml --dry-run          # report what would be fixed, fix nothing
python guardian.py --config config.yaml --validate-config  # check config.yaml is well-formed, don't run anything
python guardian.py --config config.yaml --check my_check   # run only one named check (debugging)
python guardian.py --config config.yaml --json             # machine-readable output on stdout
python guardian.py --list-checks                            # show every check type + its config keys
python guardian.py --config config.yaml --report            # uptime % and MTTR per check, from history
python guardian.py --config config.yaml --investigate NAME  # AI root-cause analysis for one check (see below)
```

`--json` output is guaranteed to be the only thing on stdout — notifications and logs are written to stderr/log file, so you can safely pipe it into `jq` or another tool without it choking on interleaved log lines.

## History, uptime, and MTTR

Every run appends each check's result to a local SQLite file (`history_db` in config, default `guardian_history.db`). `--report` turns that into, per check: total runs, uptime percentage, and MTTR (mean time to recovery — the average time from a check first going unhealthy to it next reporting healthy again):

```bash
$ python guardian.py --config config.yaml --report
myapp_service: 99.2% uptime over 672 runs, MTTR 45s
root_disk: 100.0% uptime over 672 runs, MTTR n/a
website_cert: 100.0% uptime over 672 runs, MTTR n/a
```

`--since-hours N` narrows the window (default: 168h / 7 days). Combine with `--json` for machine-readable output.

## Dead man's switch

Guardian can only escalate problems if it's still running at all. If cron dies, the box loses power, or the script starts crashing before it even loads its config, guardian has no way to notice that about itself. Point `heartbeat_url` in config.yaml at a free external monitor like [healthchecks.io](https://healthchecks.io) — guardian pings it once per completed run, and if a ping doesn't arrive on schedule, the external service alerts you through its own independent channel:

```yaml
heartbeat_url: "https://hc-ping.com/your-uuid-here"
```

## Optional: AI root-cause investigation

Everything above is deliberately deterministic — see [Design principle](#design-principle-no-llm-judgment-calls-in-the-fix-path). But there's a real gap a rule can't fill: guardian can tell you a check is failing with no safe fix, and that's where it stops. It can't tell you *why*. Root-causing is a judgment call — correlating timing, ruling out red herrings, deciding what's actually relevant — and that's exactly what an LLM is good at, as long as it's kept out of the fix path itself.

`--investigate <check_name>` hands the failure to Claude along with diagnostic evidence (guardian's own log tail, plus whatever the model asks for next) and gets back a structured root-cause report instead of a raw log dump:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
pip install -r requirements-ai.txt

python guardian.py --config config.yaml --investigate telegram_polling_errors
```

```
Root cause: A duplicate bot process was likely running briefly during a deploy,
causing Telegram's getUpdates conflict.
Confidence: medium
Evidence: 3 polling failures in the log window, clustered within 90 seconds of
a systemd restart in the same log.
Recommended action: If this recurs after a deploy, add a brief delay before
the new process starts polling.
```

This is a genuine **agentic loop**, not a single API call: the model can call a `run_diagnostic` tool (from a small read-only allowlist — disk usage, memory, uptime, recent git log) to gather more evidence before it commits to an answer, up to a few rounds. If it's still not confident, it says so rather than guessing. See [`ai_investigate.py`](ai_investigate.py) — under 150 lines, no framework, just the Anthropic SDK's native tool-use loop.

### Making it fully automatic (end-to-end, no manual step)

`--investigate` above is a manual command — useful for testing, but it means a human has to notice an escalation and remember to run it. Set `ai_investigate.enabled: true` in config.yaml and guardian does this itself:

```yaml
ai_investigate:
  enabled: true
```

Now the loop is genuinely end-to-end: cron runs guardian → a check fails → guardian tries its deterministic fix → if it has no fix, or the fix didn't hold, guardian **automatically** calls the AI investigation and includes the root-cause report directly in the notification (Telegram/Slack/webhook) it was already going to send. Nobody has to notice anything or run a follow-up command — you just read the message. This is opt-in and additive: with it off (default), escalations behave exactly as before; with it on, they arrive pre-diagnosed.

Entirely optional — the base tool needs no AI, no API key, no subscription. This is an opt-in layer for when a rule genuinely isn't enough.

## Secrets in config

String values in `config.yaml` support `${ENV_VAR}` interpolation, so bot tokens and webhook URLs don't have to sit in plaintext in the file you might commit or share:

```yaml
notify:
  method: telegram
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  chat_id: "${TELEGRAM_CHAT_ID}"
```

Guardian raises an error at load time if a referenced variable isn't set in the environment, rather than silently sending to an empty chat ID.

## Quick start

```bash
git clone https://github.com/aryan05-singh/linux-guardian.git && cd linux-guardian
./setup.sh   # creates .venv, installs deps, scaffolds config.yaml

# edit config.yaml: your service names, paths, thresholds, notify target
.venv/bin/python guardian.py --config config.yaml --validate-config
.venv/bin/python guardian.py --config config.yaml
```

Wire it to a schedule:

```cron
# crontab -e
*/15 * * * * cd /path/to/linux-guardian && .venv/bin/python guardian.py --config config.yaml
```

Or run it in a container instead of a venv:

```bash
docker build -t linux-guardian .
docker run --rm -v $(pwd)/config.yaml:/app/config.yaml linux-guardian
```

Exit code is `0` if everything is healthy (or was auto-fixed), `1` if anything escalated — useful if you also want your cron/monitoring wrapper to alert on a non-zero exit.

## Notifications

Configured under `notify:` in `config.yaml`, one of:

- `stdout` (default) — prints to the console/log, good for local testing
- `webhook` — POSTs `{"text": "..."}` to any URL (Slack incoming webhooks, Discord, a custom endpoint)
- `telegram` — sends via a bot token + chat ID

## Example config

See [`config.example.yaml`](config.example.yaml) for a fully commented example covering every check type.

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

Covers the built-in checks (pass/fail/fix-availability for each type), the circuit breaker's time-window logic, config validation, env-var interpolation, dry-run behavior, history/uptime/MTTR math, the heartbeat ping, and that `--json` output stays valid JSON even when a check escalates. The AI investigate tool-use loop is tested with a fake Anthropic client injected via `sys.modules` — no real API key or network call needed, so it runs in CI same as everything else. CI also runs `ruff` on every push.

## Project origin

This started as a private tool watching one specific personal automation stack, then got generalized here into a config-driven, dependency-free version anyone can point at their own server.

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by **Aryan Singh** — [GitHub](https://github.com/aryan05-singh)
