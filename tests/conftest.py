import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ros_tools.common import Finding, ToolResult  # noqa: E402


def make_result(tool="inspect_topic", findings=None, success=True, **data):
    fs = [Finding(text=t, anomaly=a, subjects=list(s)) for t, a, s in (findings or [])]
    return ToolResult(tool=tool, args={}, success=success, data=data, findings=fs)


@pytest.fixture
def ledger():
    from backend.agent.evidence import EvidenceLedger
    return EvidenceLedger()


@pytest.fixture
def tmp_audit(tmp_path):
    from backend.safety.audit import AuditLog
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture(autouse=True)
def isolated_incident_memory(tmp_path, monkeypatch):
    """Every test gets its own incident log: Agent.run() calls memory.record() unconditionally, so without
    this every fast unit test that exercises the agent loop would append synthetic incidents to the real
    logs/incidents.jsonl on disk (found the hard way: it grew to hundreds of lines from a single test run,
    inflating the "compact system prompt" test's measured size). No test needs to request this explicitly."""
    from backend.agent import memory
    monkeypatch.setattr(memory, "PATH", tmp_path / "incidents.jsonl")


def pytest_configure(config):
    config.addinivalue_line("markers", "ros: needs a running ROS 2 demo robot (scripts/start_demo.sh)")
    config.addinivalue_line("markers", "llm: needs a reachable Ollama with the configured model")


def _demo_up() -> bool:
    try:
        import httpx
        return httpx.get("http://127.0.0.1:8766/status", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def pytest_collection_modifyitems(config, items):
    if any("ros" in i.keywords for i in items) and not _demo_up():
        skip = pytest.mark.skip(reason="demo robot not running (scripts/start_demo.sh)")
        for i in items:
            if "ros" in i.keywords:
                i.add_marker(skip)
