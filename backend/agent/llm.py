"""Async Ollama client for the agent.

Default protocol ("json"): the model must answer with ONE grammar-constrained JSON decision
(`format` = JSON schema), e.g. {"reason_summary": "...", "action": "tool", "tool": "inspect_topic", "arguments": {...}}.
That removes the prose the model otherwise writes before every tool call (the dominant latency, see docs/PERFORMANCE.md)
while the model still chooses every tool and the final diagnosis. `ROBOTOPS_PROTOCOL=tools` keeps Ollama's native
tool-calling path for comparison.
"""
from __future__ import annotations

import contextvars
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

OLLAMA_URL = os.environ.get("ROBOTOPS_OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("ROBOTOPS_MODEL", "qwen3:4b")
TIMEOUT_S = float(os.environ.get("ROBOTOPS_LLM_TIMEOUT", "60"))     # per request; one retry on timeout
THINK = os.environ.get("ROBOTOPS_THINK", "0") == "1"                # native-tools protocol only
PROTOCOL = os.environ.get("ROBOTOPS_PROTOCOL", "json")               # "json" | "tools"
NUM_CTX = int(os.environ.get("ROBOTOPS_NUM_CTX", "6144"))            # small enough to stay 100 % on a 6 GB GPU
KEEP_ALIVE = os.environ.get("ROBOTOPS_KEEP_ALIVE", "60m")
MAX_DECISION_TOKENS = int(os.environ.get("ROBOTOPS_MAX_TOKENS", "320"))

# The agent sets this per investigation so a timeout/retry shows up live in the timeline.
retry_notifier: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "retry_notifier", default=None)


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMReply:
    content: str                       # visible text (never private reasoning)
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)
    eval_tokens: int = 0
    seconds: float = 0.0
    malformed: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)   # per-call timing/token breakdown (Ollama-reported + wall clock)


def _strip_think(text: str) -> str:
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.replace("<think>", "").strip()


# ---------------------------------------------------------------------------- transport
async def _post(path: str, body: dict) -> tuple[dict, dict]:
    """POST with one controlled retry on timeout. Returns (json, {"wall_s", "attempts"})."""
    t0 = time.monotonic()
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                r = await c.post(f"{OLLAMA_URL}{path}", json=body)
            break
        except httpx.TimeoutException as e:
            note = retry_notifier.get()
            if attempt == 2:
                if note:
                    note({"kind": "model_timeout", "final": True, "timeout_s": TIMEOUT_S})
                raise LLMUnavailable(f"model timed out after {TIMEOUT_S:.0f}s (2 attempts)") from e
            if note:
                note({"kind": "model_timeout", "final": False, "timeout_s": TIMEOUT_S})
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Ollama unreachable at {OLLAMA_URL}: {type(e).__name__}") from e
    if r.status_code != 200:
        raise LLMUnavailable(f"Ollama returned HTTP {r.status_code}: {r.text[:200]}")
    return r.json(), {"wall_s": round(time.monotonic() - t0, 2), "attempts": attempt}


def _metrics(data: dict, extra: dict) -> dict:
    ns = 1e9
    m = {
        "wall_s": extra["wall_s"],
        "ollama_total_s": round((data.get("total_duration") or 0) / ns, 2),
        "load_s": round((data.get("load_duration") or 0) / ns, 2),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "prompt_eval_s": round((data.get("prompt_eval_duration") or 0) / ns, 2),
        "output_tokens": data.get("eval_count", 0),
        "generation_s": round((data.get("eval_duration") or 0) / ns, 2),
        "attempts": extra["attempts"],
    }
    m["tokens_per_s"] = round(m["output_tokens"] / m["generation_s"], 1) if m["generation_s"] else None
    return m


# ---------------------------------------------------------------------------- JSON decision protocol
def decision_schema() -> dict:
    from backend.ros_tools import registry
    from backend.safety import policies
    return {"type": "object", "properties": {
        "reason_summary": {"type": "string", "maxLength": 140},
        "action": {"enum": ["tool", "diagnose"]},
        "tool": {"enum": list(registry.READ_ONLY_TOOLS)},
        "arguments": {"type": "object", "properties": {
            "topic": {"type": "string"}, "node": {"type": "string"}, "duration": {"type": "number"},
            "parent_frame": {"type": "string"}, "child_frame": {"type": "string"}}},
        "root_cause": {"type": "string", "maxLength": 200},
        "faulty_component": {"enum": policies.components() + ["none"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "recommended_action": {"enum": list(policies.ACTIONS) + ["none"]},
    }, "required": ["reason_summary", "action"]}


def _plain_messages(messages: list[dict]) -> list[dict]:
    """Tool results become user turns; assistant turns are the JSON decisions themselves."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "user", "content": f"TOOL RESULT {m.get('tool_name', '')}:\n{m['content']}"})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def parse_decision(text: str) -> tuple[ToolCall | None, str | None, str]:
    """-> (tool_call, problem, reason_summary). A 'diagnose' decision becomes a submit_diagnosis call."""
    try:
        d = json.loads(text)
    except ValueError:
        return None, "output was not valid JSON (truncated?)", ""
    if not isinstance(d, dict):
        return None, "output was not a JSON object", ""
    reason = d.get("reason_summary") if isinstance(d.get("reason_summary"), str) else ""
    if d.get("action") == "tool":
        args = d.get("arguments") if isinstance(d.get("arguments"), dict) else {}
        if not isinstance(d.get("tool"), str):
            return None, "action 'tool' without a tool name", reason
        return ToolCall(d["tool"], {**args, "reason": reason}), None, reason
    if d.get("action") == "diagnose":
        return ToolCall("submit_diagnosis", {
            "root_cause": d.get("root_cause") or reason, "faulty_component": d.get("faulty_component") or "",
            "evidence_ids": d.get("evidence_ids") or [], "recommended_action": d.get("recommended_action") or "none",
        }), None, reason
    return None, "unknown action", reason


async def decide(messages: list[dict], tools: list[dict] | None = None, model: str = MODEL) -> LLMReply:
    """One model decision under the JSON protocol. `tools` is accepted for interface parity and ignored."""
    body = {"model": model, "messages": _plain_messages(messages), "stream": False, "think": False,
            "format": decision_schema(), "keep_alive": KEEP_ALIVE,
            "options": {"temperature": 0.1, "seed": 7, "num_ctx": NUM_CTX, "num_predict": MAX_DECISION_TOKENS}}
    data, extra = await _post("/api/chat", body)
    text = _strip_think((data.get("message") or {}).get("content") or "")
    reply = LLMReply(content=text, eval_tokens=data.get("eval_count", 0),
                     seconds=(data.get("total_duration") or 0) / 1e9, metrics=_metrics(data, extra))
    call, problem, _ = parse_decision(text)
    if call:
        reply.tool_calls.append(call)
    else:
        reply.malformed.append(problem or "invalid decision")
    reply.raw_message = {"role": "assistant", "content": text}
    return reply


# ---------------------------------------------------------------------------- native tool-calling protocol (comparison)
async def chat(messages: list[dict], tools: list[dict], model: str = MODEL) -> LLMReply:
    body = {"model": model, "messages": messages, "tools": tools, "stream": False, "think": THINK,
            "keep_alive": KEEP_ALIVE, "options": {"temperature": 0.1, "seed": 7, "num_ctx": NUM_CTX}}
    data, extra = await _post("/api/chat", body)
    msg = data.get("message") or {}
    reply = LLMReply(content=_strip_think(msg.get("content") or ""), eval_tokens=data.get("eval_count", 0),
                     seconds=(data.get("total_duration") or 0) / 1e9, metrics=_metrics(data, extra))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                reply.malformed.append(f"arguments for {fn.get('name')} are not valid JSON")
                continue
        if not isinstance(args, dict) or not isinstance(fn.get("name"), str):
            reply.malformed.append("tool call without a name or with non-object arguments")
            continue
        reply.tool_calls.append(ToolCall(fn["name"], args))
    reply.raw_message = {"role": "assistant", "content": reply.content,
                         **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})}
    return reply


def default_chat():
    return chat if PROTOCOL == "tools" else decide


# ---------------------------------------------------------------------------- health / warm-up
async def status() -> dict:
    """Reachability + whether the model is installed AND resident (warm) in Ollama."""
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            tags = await c.get(f"{OLLAMA_URL}/api/tags")
            ps = await c.get(f"{OLLAMA_URL}/api/ps")
        models = [m["name"] for m in tags.json().get("models", [])]
        loaded = [m for m in ps.json().get("models", []) if m.get("name") == MODEL]
        placement = None
        if loaded:
            size, vram = loaded[0].get("size", 0), loaded[0].get("size_vram", 0)
            placement = f"{round(100 * vram / size)}% GPU" if size else None
        return {"reachable": True, "model": MODEL, "model_available": MODEL in models, "models": models,
                "warm": bool(loaded), "placement": placement,
                "context": loaded[0].get("context_length") if loaded else None}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "model": MODEL, "model_available": False, "warm": False,
                "error": type(e).__name__}


async def warmup() -> dict:
    """Load the model and run a tiny constrained generation with the exact production options.
    Returns {"ok", "seconds", "error"?}; a healthy warm model answers in ~1-2 s."""
    t0 = time.monotonic()
    body = {"model": MODEL, "messages": [{"role": "user", "content": 'Reply with {"action":"diagnose","reason_summary":"ok"}'}],
            "stream": False, "think": False, "format": decision_schema(), "keep_alive": KEEP_ALIVE,
            "options": {"temperature": 0, "seed": 7, "num_ctx": NUM_CTX, "num_predict": 40}}
    try:
        async with httpx.AsyncClient(timeout=max(TIMEOUT_S, 120)) as c:   # first load can be slow on a cold GPU
            r = await c.post(f"{OLLAMA_URL}/api/chat", json=body)
        if r.status_code != 200:
            return {"ok": False, "seconds": round(time.monotonic() - t0, 2), "error": f"HTTP {r.status_code}"}
        json.loads(r.json()["message"]["content"])
        return {"ok": True, "seconds": round(time.monotonic() - t0, 2)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "seconds": round(time.monotonic() - t0, 2), "error": f"{type(e).__name__}: {e}"}
