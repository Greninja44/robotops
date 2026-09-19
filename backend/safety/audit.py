"""Append-only JSONL audit log of agent decisions, tool calls, approvals, repairs, verification."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

AUDIT_PATH = Path(__file__).resolve().parents[2] / "logs" / "audit.jsonl"


class AuditLog:
    def __init__(self, path: Path = AUDIT_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, investigation_id: str | None, kind: str, **payload):
        rec = {"ts": time.time(), "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "investigation": investigation_id, "kind": kind, **payload}
        line = json.dumps(rec, default=str)
        with self._lock, self.path.open("a") as f:
            f.write(line + "\n")
        return rec

    def tail(self, n: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out
