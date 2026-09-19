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
