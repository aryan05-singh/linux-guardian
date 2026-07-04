import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from checks.builtin import (
    cpu_load_check,
    memory_usage_check,
    port_open_check,
    process_running_check,
    ssl_cert_expiry_check,
)


def test_port_open_detects_listening_port():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        check = port_open_check({"name": "p", "host": "127.0.0.1", "port": port})
        assert check()["ok"] is True
    finally:
        server.close()


def test_port_open_detects_closed_port():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()  # free the port, nothing listening now

    check = port_open_check({"name": "p", "host": "127.0.0.1", "port": port, "timeout": 1})
    assert check()["ok"] is False


def test_process_running_no_match():
    check = process_running_check({"name": "proc", "pattern": "definitely_not_a_real_process_xyz123"})
    assert check()["ok"] is False


def test_memory_usage_thresholds():
    always_fails = memory_usage_check({"name": "mem", "threshold_pct": 0})
    assert always_fails()["ok"] is False

    always_ok = memory_usage_check({"name": "mem", "threshold_pct": 100})
    assert always_ok()["ok"] is True


def test_cpu_load_thresholds():
    always_fails = cpu_load_check({"name": "cpu", "threshold": 0})
    assert always_fails()["ok"] is False

    always_ok = cpu_load_check({"name": "cpu", "threshold": 10_000})
    assert always_ok()["ok"] is True


def test_ssl_cert_expiry_unreachable_host_fails_safely():
    check = ssl_cert_expiry_check({"name": "cert", "hostname": "thishostdoesnotexist.invalid"})
    result = check()
    assert result["ok"] is False
    assert "could not verify cert" in result["detail"]
