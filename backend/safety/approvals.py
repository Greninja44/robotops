"""Human approval registry.

A proposal is bound to one exact (action, target). The repair executor refuses
to run anything whose proposal is not in state APPROVED, and each approval can
be consumed exactly once.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field


class ApprovalError(Exception):
    pass


@dataclass
class Proposal:
    id: str
    investigation_id: str
    action: str
    target: str
    state: str = "pending"          # pending | approved | rejected | expired | consumed
    decided_by: str | None = None
    decided_at: float | None = None
    created_at: float = field(default_factory=time.time)
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class ApprovalRegistry:
    def __init__(self):
        self._p: dict[str, Proposal] = {}

    def create(self, investigation_id: str, action: str, target: str) -> Proposal:
        p = Proposal(id=uuid.uuid4().hex[:12], investigation_id=investigation_id, action=action, target=target)
        self._p[p.id] = p
        return p

    def get(self, pid: str) -> Proposal | None:
        return self._p.get(pid)

    def decide(self, pid: str, approve: bool, by: str = "operator") -> Proposal:
        p = self._p.get(pid)
        if p is None:
            raise ApprovalError(f"no such proposal {pid}")
        if p.state != "pending":
            raise ApprovalError(f"proposal {pid} is already {p.state}")
        p.state = "approved" if approve else "rejected"
        p.decided_by, p.decided_at = by, time.time()
        p.event.set()
        return p

    def expire(self, pid: str):
        p = self._p.get(pid)
        if p and p.state == "pending":
            p.state = "expired"
            p.event.set()

    def consume(self, pid: str, action: str, target: str) -> Proposal:
        """Called by the repair executor immediately before acting."""
        p = self._p.get(pid)
        if p is None or p.state != "approved":
            raise ApprovalError(f"repair refused: proposal {pid} is not approved "
                                f"(state={p.state if p else 'missing'})")
        if (p.action, p.target) != (action, target):
            raise ApprovalError("repair refused: action/target differ from what was approved")
        p.state = "consumed"
        return p
