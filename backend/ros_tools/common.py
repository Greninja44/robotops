"""Shared pieces of the tool layer: result schema, input sanitising, robot manifest."""
from __future__ import annotations

import difflib
import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "demo_robot" / "manifest.json"

_ROS_NAME = re.compile(r"^/?[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*$")
_FRAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_/]*$")


class InvalidArgument(ValueError):
    pass


def ros_name(value: Any, what: str = "name") -> str:
    """Validate a node/topic name coming from the LLM. Always returns an absolute name."""
    if not isinstance(value, str) or not value or len(value) > 128:
        raise InvalidArgument(f"{what} must be a non-empty string of at most 128 characters")
    value = value.strip()
    if not _ROS_NAME.match(value):
        raise InvalidArgument(f"{what} {value!r} is not a valid ROS name")
    return value if value.startswith("/") else "/" + value


def frame_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise InvalidArgument("frame must be a non-empty string of at most 64 characters")
    value = value.strip().lstrip("/")
    if not _FRAME.match(value):
        raise InvalidArgument(f"frame {value!r} is not a valid TF frame id")
    return value


def clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def suggest(name: str, candidates: list[str]) -> list[str]:
    return difflib.get_close_matches(name, candidates, n=3, cutoff=0.5)


class Finding(BaseModel):
    """A deterministic observation derived by code from tool data (never by the LLM)."""
    text: str
    anomaly: bool
    subjects: list[str] = Field(default_factory=list)  # node/topic names, "tf:parent->child"


class ToolResult(BaseModel):
    tool: str
    args: dict[str, Any]
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class Recorder:
    """Small helper so each tool can write `r.anomaly(...)` / `r.normal(...)`."""

    def __init__(self, tool: str, args: dict):
        self.tool, self.args = tool, args
        self.data: dict[str, Any] = {}
        self.findings: list[Finding] = []
        self.t0 = time.monotonic()

    def anomaly(self, text: str, *subjects: str):
        self.findings.append(Finding(text=text, anomaly=True, subjects=list(subjects)))

    def normal(self, text: str, *subjects: str):
        self.findings.append(Finding(text=text, anomaly=False, subjects=list(subjects)))

    def ok(self) -> ToolResult:
        return ToolResult(tool=self.tool, args=self.args, success=True, data=self.data,
                          findings=self.findings, duration_ms=int((time.monotonic() - self.t0) * 1000))

    def fail(self, error: str) -> ToolResult:
        return ToolResult(tool=self.tool, args=self.args, success=False, data=self.data,
                          findings=self.findings, error=error,
                          duration_ms=int((time.monotonic() - self.t0) * 1000))


@lru_cache(maxsize=1)
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def component_of(node: str) -> str | None:
    return manifest()["nodes"].get(node, {}).get("component")


def node_of(component: str) -> str | None:
    for n, meta in manifest()["nodes"].items():
        if meta["component"] == component:
            return n
    return None


def component_subjects(component: str) -> set[str]:
    """Everything a component owns: its node, the topics it publishes/subscribes, the TF edges it broadcasts."""
    node = node_of(component)
    if node is None:
        return set()
    m = manifest()
    subj = {node}
    for topic, t in m["topics"].items():
        if node in t["publishers"] or node in t["subscribers"]:
            subj.add(topic)
    for e in m["tf"]:
        if e["broadcaster"] == node:
            subj.add(f"tf:{e['parent']}->{e['child']}")
    return subj


def tool(name: str):
    """Decorator: turns exceptions into structured failures so a broken tool never
    crashes the agent and never produces an invented result."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(client, **args) -> ToolResult:
            r = Recorder(name, dict(args))
            try:
                fn(client, r, **args)
                return r.ok()
            except InvalidArgument as e:
                return r.fail(f"invalid argument: {e}")
            except TypeError as e:  # unexpected/missing kwargs from the LLM
                return r.fail(f"bad arguments: {e}")
            except TimeoutError as e:
                return r.fail(f"timeout: {e}")
            except Exception as e:  # noqa: BLE001
                from .client import RosUnavailable
                if isinstance(e, RosUnavailable):
                    return r.fail(f"ROS unavailable: {e}")
                return r.fail(f"{type(e).__name__}: {e}")
        wrapper.tool_name = name
        return wrapper
    return deco
