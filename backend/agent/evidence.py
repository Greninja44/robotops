"""Evidence ledger: every finding produced by a tool gets a stable ID (E1, E2, ...).

The LLM only ever sees findings through this ledger and must cite IDs. Because
IDs are assigned here, from real tool output, a diagnosis cannot reference an
observation that was never made.
"""
from __future__ import annotations

import json
import time

from pydantic import BaseModel

from backend.ros_tools.common import ToolResult


class Evidence(BaseModel):
    id: str
    text: str
    anomaly: bool
    subjects: list[str]
    source: str          # tool name
    step: int            # investigation step that produced it
    args: dict
    timestamp: float


class EvidenceLedger:
    def __init__(self):
        self.items: dict[str, Evidence] = {}
        self._n = 0

    def add(self, result: ToolResult, step: int) -> list[Evidence]:
        added = []
        for f in result.findings:
            self._n += 1
            e = Evidence(id=f"E{self._n}", text=f.text, anomaly=f.anomaly, subjects=f.subjects,
                         source=result.tool, step=step, args=result.args, timestamp=time.time())
            self.items[e.id] = e
            added.append(e)
        return added

    def get(self, eid: str) -> Evidence | None:
        return self.items.get(eid.strip().upper()) if isinstance(eid, str) else None

    def anomalies(self) -> list[Evidence]:
        return [e for e in self.items.values() if e.anomaly]

    def __len__(self):
        return len(self.items)


def format_for_llm(result: ToolResult, evidence: list[Evidence], max_data_chars: int = 700) -> str:
    """Compact tool-result message: cited-able findings first, then truncated raw data."""
    if not result.success:
        return f"TOOL FAILED ({result.tool}): {result.error}"
    lines = [f"{e.id} [{'ANOMALY' if e.anomaly else 'ok'}] {e.text}" for e in evidence]
    data = json.dumps(result.data, default=str, separators=(",", ":"))
    if len(data) > max_data_chars:
        data = data[:max_data_chars] + "...(truncated)"
    return "Findings (cite these IDs):\n" + ("\n".join(lines) or "(none)") + f"\nData: {data}"
