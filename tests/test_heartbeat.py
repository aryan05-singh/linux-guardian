import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from heartbeat import ping_heartbeat


def test_ping_heartbeat_calls_urlopen(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append((url, timeout))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ping_heartbeat("https://hc-ping.com/fake-uuid")
    assert calls == [("https://hc-ping.com/fake-uuid", 10)]


def test_ping_heartbeat_swallows_network_errors(monkeypatch, capsys):
    def fake_urlopen(url, timeout):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ping_heartbeat("https://hc-ping.com/fake-uuid")  # must not raise

    captured = capsys.readouterr()
    assert "heartbeat" in captured.err
