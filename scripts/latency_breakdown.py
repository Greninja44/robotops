#!/usr/bin/env python3
"""Where does diagnosis time go? Aggregates model / tool / other time from persisted investigations.

  python scripts/latency_breakdown.py benchmarks/random/random_<ts>.json
"""
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def one(inv_id: str) -> dict | None:
    f = ROOT / "logs" / "investigations" / f"{inv_id}.json"
    if not f.exists():
        return None
    inv = json.loads(f.read_text())
    ev = inv["events"]
    t0 = inv["created_at"]
    diag_t = next((e["ts"] for e in ev if e["kind"] == "diagnosis"), None)
    if diag_t is None:
        return None
    model = sum(e["wall_s"] for e in ev if e["kind"] == "llm_call" and e["ts"] <= diag_t + 0.01)
    tools = {}          # tool name -> list of call durations (s)
    for e in ev:
        if e["kind"] == "tool_result" and e["ts"] <= diag_t + 0.01:
            tools.setdefault(e["tool"], []).append(e["duration_ms"] / 1000)
    return {"diagnosis_s": diag_t - t0, "model_s": model, "tools_s": sum(sum(v) for v in tools.values()), "tools": tools,
            "out_tokens": sum(e.get("output_tokens", 0) for e in ev if e["kind"] == "llm_call" and e["ts"] <= diag_t + 0.01),
            "prompt_tokens": sum(e.get("prompt_tokens", 0) for e in ev if e["kind"] == "llm_call" and e["ts"] <= diag_t + 0.01),
            "model_calls": sum(1 for e in ev if e["kind"] == "llm_call" and e["ts"] <= diag_t + 0.01)}


def main():
    runs = json.loads(Path(sys.argv[1]).read_text())["runs"]
    rows = [r for r in (one(x["investigation_id"]) for x in runs if x.get("investigation_id")) if r]
    if not rows:
        sys.exit("no persisted investigations found")
    n = len(rows)
    tot = {k: st.mean(r[k] for r in rows) for k in ("diagnosis_s", "model_s", "tools_s", "out_tokens", "prompt_tokens", "model_calls")}
    other = tot["diagnosis_s"] - tot["model_s"] - tot["tools_s"]
    print(f"{n} investigations (mean per diagnosis)")
    print(f"  diagnosis time      {tot['diagnosis_s']:.1f} s")
    print(f"  model calls         {tot['model_calls']:.1f} calls, {tot['model_s']:.1f} s ({100 * tot['model_s'] / tot['diagnosis_s']:.0f}%)  "
          f"[{tot['prompt_tokens']:.0f} prompt tokens, {tot['out_tokens']:.0f} output tokens]")
    print(f"  ROS tool execution  {tot['tools_s']:.1f} s ({100 * tot['tools_s'] / tot['diagnosis_s']:.0f}%)")
    print(f"  orchestration/other {other:.1f} s ({100 * other / tot['diagnosis_s']:.0f}%)")
    per_tool = {}
    for r in rows:
        for t, calls in r["tools"].items():
            per_tool.setdefault(t, []).extend(calls)
    print("  tool time by tool (mean per CALL):")
    for t, v in sorted(per_tool.items(), key=lambda kv: -st.mean(kv[1])):
        print(f"    {t:24s} {st.mean(v):5.2f} s  (n={len(v)})")


if __name__ == "__main__":
    main()
