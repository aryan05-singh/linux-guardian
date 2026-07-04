import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_investigate import _run_allowed_command, gather_base_evidence, investigate


class _FakeContentBlock:
    def __init__(self, type_, text=None, input=None, id=None):
        self.type = type_
        self.text = text
        self.input = input or {}
        self.id = id or "tool-1"


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def _install_fake_anthropic(monkeypatch, create_fn):
    fake_module = types.ModuleType("anthropic")

    class _FakeMessages:
        def create(self, **kwargs):
            return create_fn(**kwargs)

    class _FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.messages = _FakeMessages()

    fake_module.Anthropic = _FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)


def test_run_allowed_command_rejects_unknown_name():
    output = _run_allowed_command("rm_everything")
    assert "unknown diagnostic command" in output


def test_run_allowed_command_runs_uptime():
    output = _run_allowed_command("uptime")
    assert "<error" not in output


def test_gather_base_evidence_missing_log(tmp_path):
    evidence = gather_base_evidence(tmp_path / "missing.log")
    assert evidence["recent_log_tail"] == "<log file not found>"


def test_gather_base_evidence_reads_tail(tmp_path):
    log = tmp_path / "guardian.log"
    log.write_text("line1\nline2\nline3\n")
    evidence = gather_base_evidence(log, window_lines=2)
    assert "line2" in evidence["recent_log_tail"]
    assert "line3" in evidence["recent_log_tail"]
    assert "line1" not in evidence["recent_log_tail"]


def test_investigate_returns_direct_answer_without_tool_calls(monkeypatch):
    def create_fn(**kwargs):
        return _FakeResponse(
            [_FakeContentBlock("text", text="Root cause: disk full\nConfidence: high\n"
                                             "Evidence: df shows 95%\nRecommended action: clean logs")]
        )

    _install_fake_anthropic(monkeypatch, create_fn)
    report = investigate("disk_check", "95% used", {"recent_log_tail": "..."}, api_key="fake")
    assert "Root cause: disk full" in report


def test_investigate_handles_one_tool_use_round_trip(monkeypatch):
    calls = {"n": 0}

    def create_fn(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(
                [_FakeContentBlock("tool_use", input={"command_name": "uptime"}, id="t1")]
            )
        return _FakeResponse(
            [_FakeContentBlock(
                "text",
                text="Root cause: transient load spike\nConfidence: medium\n"
                     "Evidence: uptime showed high load\nRecommended action: monitor",
            )]
        )

    _install_fake_anthropic(monkeypatch, create_fn)
    report = investigate("cpu_check", "high load", {}, api_key="fake")
    assert calls["n"] == 2
    assert "transient load spike" in report


def test_investigate_gives_up_after_max_tool_calls(monkeypatch):
    def create_fn(**kwargs):
        # Always asks for another tool call, never concludes.
        return _FakeResponse([_FakeContentBlock("tool_use", input={"command_name": "uptime"}, id="t1")])

    _install_fake_anthropic(monkeypatch, create_fn)
    report = investigate("stuck_check", "still failing", {}, api_key="fake", max_tool_calls=2)
    assert "inconclusive" in report.lower()
