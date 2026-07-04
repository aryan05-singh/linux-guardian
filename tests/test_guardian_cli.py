import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from guardian import _interpolate_env, run, validate_config
from history import build_report


def test_interpolate_env_replaces_var(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "s3cr3t")
    result = _interpolate_env({"token": "${MY_SECRET}", "nested": ["prefix-${MY_SECRET}"]})
    assert result["token"] == "s3cr3t"
    assert result["nested"][0] == "prefix-s3cr3t"


def test_interpolate_env_raises_on_missing_var(monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    try:
        _interpolate_env("${DOES_NOT_EXIST}")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_validate_config_flags_missing_checks():
    errors = validate_config({"notify": {"method": "stdout"}, "checks": []})
    assert any("no checks defined" in e for e in errors)


def test_validate_config_flags_unknown_type():
    errors = validate_config({"checks": [{"name": "x", "type": "not_a_real_type"}]})
    assert any("unknown type" in e for e in errors)


def test_validate_config_flags_missing_required_key():
    errors = validate_config({"checks": [{"name": "svc", "type": "systemd_service"}]})
    assert any("missing required key" in e for e in errors)


def test_validate_config_passes_for_good_config():
    errors = validate_config({
        "checks": [{"name": "always_ok", "type": "command", "check_command": "exit 0"}],
    })
    assert errors == []


def test_dry_run_does_not_execute_fix(tmp_path):
    marker = tmp_path / "fix_ran"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
state_file: {tmp_path / "state.json"}
log_file: {tmp_path / "guardian.log"}
notify:
  method: stdout
checks:
  - type: command
    name: always_fails
    check_command: "exit 1"
    fix_command: "touch {marker}"
""")
    ok = run(config_path, dry_run=True)
    assert ok is False  # still reports unhealthy
    assert not marker.exists()  # but never actually ran the fix


def test_only_check_filters_to_one_check(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
state_file: {tmp_path / "state.json"}
log_file: {tmp_path / "guardian.log"}
notify:
  method: stdout
checks:
  - type: command
    name: check_a
    check_command: "exit 0"
  - type: command
    name: check_b
    check_command: "exit 1"
""")
    # Running only check_a (which passes) should report healthy overall,
    # even though check_b (excluded) would have failed.
    ok = run(config_path, only_check="check_a")
    assert ok is True


def test_json_output_is_valid_json_even_with_escalation(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
state_file: {tmp_path / "state.json"}
log_file: {tmp_path / "guardian.log"}
notify:
  method: stdout
checks:
  - type: command
    name: broken_check
    check_command: "exit 1"
""")
    ok = run(config_path, json_output=True)
    assert ok is False

    captured = capsys.readouterr()
    # stdout must be pure, parseable JSON — any notify/log noise belongs on
    # stderr, otherwise downstream tooling piping stdout into json.loads breaks.
    payload = json.loads(captured.out)
    assert payload["escalated"] is True
    assert payload["results"][0]["name"] == "broken_check"
    assert payload["results"][0]["status"] == "escalated"


def test_run_records_history_and_pings_heartbeat(tmp_path, monkeypatch):
    pinged = []
    monkeypatch.setattr("guardian.ping_heartbeat", lambda url: pinged.append(url))

    history_db = tmp_path / "history.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
state_file: {tmp_path / "state.json"}
log_file: {tmp_path / "guardian.log"}
history_db: {history_db}
heartbeat_url: "https://hc-ping.com/fake-uuid"
notify:
  method: stdout
checks:
  - type: command
    name: always_ok
    check_command: "exit 0"
""")
    run(config_path)

    assert history_db.exists()
    report = build_report(history_db)
    assert report[0]["name"] == "always_ok"
    assert report[0]["uptime_pct"] == 100.0
    assert pinged == ["https://hc-ping.com/fake-uuid"]


def test_escalation_auto_runs_ai_investigation_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(
        "guardian.ai_investigate",
        lambda name, detail, evidence, api_key, model: "Root cause: fake root cause from AI",
    )

    notified = []
    monkeypatch.setattr("guardian.make_notifier", lambda cfg: notified.append)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
state_file: {tmp_path / "state.json"}
log_file: {tmp_path / "guardian.log"}
history_db: {tmp_path / "history.db"}
notify:
  method: stdout
ai_investigate:
  enabled: true
checks:
  - type: command
    name: broken_check
    check_command: "exit 1"
""")
    run(config_path)

    assert any("Root cause: fake root cause from AI" in msg for msg in notified)


def test_escalation_skips_ai_investigation_when_not_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    called = []
    monkeypatch.setattr(
        "guardian.ai_investigate",
        lambda *a, **kw: called.append(1) or "should not be called",
    )

    notified = []
    monkeypatch.setattr("guardian.make_notifier", lambda cfg: notified.append)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
state_file: {tmp_path / "state.json"}
log_file: {tmp_path / "guardian.log"}
history_db: {tmp_path / "history.db"}
notify:
  method: stdout
checks:
  - type: command
    name: broken_check
    check_command: "exit 1"
""")
    run(config_path)

    assert called == []
    assert not any("AI investigation" in msg for msg in notified)
