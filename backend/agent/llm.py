"""Minimal async Ollama chat client with tool calling, timeouts and output checks."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import httpx

OLLAMA_URL = os.environ.get("ROBOTOPS_OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("ROBOTOPS_MODEL", "qwen3:4b")
TIMEOUT_S = float(os.environ.get("ROBOTOPS_LLM_TIMEOUT", "120"))
THINK = os.environ.get("ROBOTOPS_THINK", "1") == "1"


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMReply:
    content: str                       # visible text (never the private reasoning)
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)
    eval_tokens: int = 0
    seconds: float = 0.0
    malformed: list[str] = field(default_factory=list)


def _strip_think(text: str) -> str:
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.replace("<think>", "").strip()


async def chat(messages: list[dict], tools: list[dict], model: str = MODEL) -> LLMReply:
    body = {"model": model, "messages": messages, "tools": tools, "stream": False, "think": THINK,
            "options": {"temperature": 0.1, "seed": 7, "num_ctx": 12288}}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
            r = await c.post(f"{OLLAMA_URL}/api/chat", json=body)
    except httpx.TimeoutException as e:
        raise LLMUnavailable(f"model timed out after {TIMEOUT_S:.0f}s") from e
    except httpx.HTTPError as e:
        raise LLMUnavailable(f"Ollama unreachable at {OLLAMA_URL}: {type(e).__name__}") from e
    if r.status_code != 200:
        raise LLMUnavailable(f"Ollama returned HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    msg = data.get("message") or {}
    reply = LLMReply(content=_strip_think(msg.get("content") or ""), eval_tokens=data.get("eval_count", 0),
                     seconds=(data.get("total_duration") or 0) / 1e9)
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
    # Keep the reasoning out of the conversation history we send back, too.
    reply.raw_message = {"role": "assistant", "content": reply.content,
                         **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})}
    return reply


async def status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
        models = [m["name"] for m in r.json().get("models", [])]
        return {"reachable": True, "model": MODEL, "model_available": MODEL in models, "models": models}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "model": MODEL, "model_available": False, "error": type(e).__name__}
