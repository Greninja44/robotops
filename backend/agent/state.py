"""Investigation state + event stream (what the UI timeline and the audit log show)."""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Callable

from .evidence import EvidenceLedger


class Phase(str, Enum):
    OBSERVING = "observing"
    INVESTIGATING = "investigating"
    DIAGNOSING = "diagnosing"
    AWAITING_APPROVAL = "awaiting_approval"
    REPAIRING = "repairing"
    VERIFYING = "verifying"
    # terminal
    RESOLVED = "resolved"              # repair executed and recovery independently verified
    REPAIR_FAILED = "repair_failed"    # verification failed and no rounds left
    REJECTED = "rejected"              # operator rejected the proposed repair
    DIAGNOSED = "diagnosed"            # root cause found, no repair recommended
    HEALTHY = "healthy"                # agent found no fault
    INCONCLUSIVE = "inconclusive"      # not enough evidence -> no repair
    ERROR = "error"                    # LLM / ROS unavailable etc.


TERMINAL = {Phase.RESOLVED, Phase.REPAIR_FAILED, Phase.REJECTED, Phase.DIAGNOSED, Phase.HEALTHY,
            Phase.INCONCLUSIVE, Phase.ERROR}

# Allowed transitions of the state machine. Anything else is a bug.
TRANSITIONS = {
    Phase.OBSERVING: {Phase.INVESTIGATING, Phase.ERROR},
    Phase.INVESTIGATING: {Phase.DIAGNOSING, Phase.INCONCLUSIVE, Phase.ERROR},
    Phase.DIAGNOSING: {Phase.AWAITING_APPROVAL, Phase.DIAGNOSED, Phase.HEALTHY, Phase.INVESTIGATING,
                       Phase.INCONCLUSIVE, Phase.ERROR},
    Phase.AWAITING_APPROVAL: {Phase.REPAIRING, Phase.REJECTED, Phase.ERROR},
    Phase.REPAIRING: {Phase.VERIFYING, Phase.REPAIR_FAILED, Phase.ERROR},
    Phase.VERIFYING: {Phase.RESOLVED, Phase.REPAIR_FAILED, Phase.INVESTIGATING, Phase.ERROR},
}


class InvalidTransition(RuntimeError):
    pass


class Investigation:
    def __init__(self, query: str, listener: Callable[[dict], None] | None = None):
        self.id = uuid.uuid4().hex[:10]
        self.query = query
        self.phase = Phase.OBSERVING
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.events: list[dict] = []
        self.ledger = EvidenceLedger()
        self.messages: list[dict] = []     # LLM conversation (not exposed)
        self.tool_calls = 0
        self.llm_calls = 0
        self.llm_seconds = 0.0
        self.round = 1
        self.diagnosis: dict | None = None
        self.proposal: dict | None = None
        self.repair: dict | None = None
        self.verification: dict | None = None
        self.error: str | None = None
        self.diagnosed_at: float | None = None
        self._listener = listener

    def emit(self, kind: str, **data: Any) -> dict:
        ev = {"seq": len(self.events) + 1, "ts": time.time(), "kind": kind, "phase": self.phase.value, **data}
        self.events.append(ev)
        if self._listener:
            self._listener({"type": "investigation_event", "investigation_id": self.id, "event": ev})
        return ev

    def transition(self, phase: Phase, **data):
        if phase not in TRANSITIONS.get(self.phase, set()):
            raise InvalidTransition(f"{self.phase.value} -> {phase.value}")
        prev, self.phase = self.phase, phase
        if phase in TERMINAL:
            self.finished_at = time.time()
        self.emit("phase", previous=prev.value, **data)

    @property
    def done(self) -> bool:
        return self.phase in TERMINAL

    def summary(self) -> dict:
        return {
            "id": self.id, "query": self.query, "phase": self.phase.value, "created_at": self.created_at,
            "finished_at": self.finished_at, "round": self.round,
            "tool_calls": self.tool_calls, "llm_calls": self.llm_calls, "llm_seconds": round(self.llm_seconds, 1),
            "diagnosis": self.diagnosis, "proposal": self.proposal, "repair": self.repair,
            "verification": self.verification, "error": self.error,
            "diagnosis_seconds": round(self.diagnosed_at - self.created_at, 1) if self.diagnosed_at else None,
            "evidence": [e.model_dump() for e in self.ledger.items.values()],
            "events": self.events,
        }
