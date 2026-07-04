# Changelog

## [0.3.0] - 2026-07-04

### Added
- History tracking: every run's results are persisted to SQLite (`history_db` in config)
- `--report` flag — per-check uptime % and MTTR (mean time to recovery) from history, windowed by `--since-hours`
- Dead man's switch: `heartbeat_url` config option pings an external monitor (e.g. healthchecks.io) once per completed run, so guardian's own downtime can be caught by something other than itself
- 8 new tests covering history math (uptime %, MTTR pairing, time-window filtering) and heartbeat ping behavior

## [0.2.0] - 2026-07-04

### Added
- Five new check types: `ssl_cert_expiry`, `port_open`, `process_running`, `memory_usage`, `cpu_load`
- `--dry-run` flag — report what would be fixed without running any fix
- `--validate-config` flag — catch config errors before a scheduled run hits them
- `--check NAME` flag — run a single check in isolation for debugging
- `--list-checks` flag — introspect available check types and their config keys
- `--json` flag — machine-readable output for integration with other tooling
- `${ENV_VAR}` interpolation in `config.yaml` so secrets don't sit in plaintext
- Per-run summary line (N ok, N fixed, N escalated)
- `setup.sh` one-command installer
- `Dockerfile` for containerized runs
- ruff linting in CI
- 25 total tests covering all check types and CLI behavior

## [0.1.0] - 2026-07-04

### Added
- Initial release: `systemd_service`, `disk_space`, `file_freshness`, `log_pattern`, `command` checks
- Circuit breaker — stops auto-fixing a check after N attempts in a time window, escalates instead
- Notifications via webhook, Telegram, or stdout
- Test suite (pytest)
