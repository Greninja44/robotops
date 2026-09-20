#!/usr/bin/env python3
"""N random-fault diagnoses through the REAL dashboard (real ROS, real model).

Per run: START DEMO -> "Random Failure" button -> ask "Diagnose the robot." -> APPROVE if a repair is proposed.
Scoring reads demo_robot/state/ground_truth.json AFTER the investigation finished (the agent never sees it), and
the audit log's recorded model inputs are scanned for any fault identifier (requires ROBOTOPS_AUDIT_PROMPTS=1 on the backend).

  python scripts/ui_random_demo.py --runs 15 --out benchmarks/random/random_<date>.json
"""
import argparse
import glob
import json
import os
import time
from pathlib import Path

import httpx
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

URL = os.environ.get("ROBOTOPS_API", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = ROOT / "demo_robot" / "state" / "ground_truth.json"
AUDIT = ROOT / "logs" / "audit.jsonl"
QUERY = "Diagnose the robot."
EXPECTED = {"controller_crash": "base_controller", "lidar_failure": "lidar_driver", "tf_failure": "tf_broadcaster",
            "topic_misconfig": "base_controller", "node_crash": "obstacle_monitor"}
TERMINAL = {"resolved", "repair_failed", "rejected", "inconclusive", "healthy", "diagnosed", "error"}


def current() -> dict | None:
    try:
        return httpx.get(f"{URL}/api/investigations/current", timeout=5).json()
    except Exception:  # noqa: BLE001
        return None


def leak_audit(inv_id: str) -> list[str]:
    """Fault identifiers found in anything recorded as model input for this investigation."""
    hits = set()
    for line in AUDIT.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("investigation") == inv_id and r.get("kind") == "llm_request":
            blob = json.dumps(r["messages"])
            hits |= {f for f in EXPECTED if f in blob}
            if "ground_truth" in blob or "injected" in blob.lower():
                hits.add("ground_truth/injected")
    return sorted(hits)


def one(pg, n: int) -> dict:
    rec = {"run": n, "stage": "start"}
    try:
        pg.wait_for_function("() => { const b = [...document.querySelectorAll('button')].find(x => x.innerText === 'START DEMO'); return b && !b.disabled }", timeout=120000)
        t0 = time.time()
        pg.get_by_role("button", name="START DEMO").click()
        pg.wait_for_function("() => document.querySelector('.ready-title')?.innerText.includes('PREPARING')", timeout=15000)
        pg.wait_for_function("() => document.querySelector('.ready-title')?.innerText.includes('READY FOR DEMO')", timeout=120000)
        rec["prepare_s"] = round(time.time() - t0, 1)

        rec["stage"] = "inject random"
        pg.get_by_role("button", name="Random Failure").click()
        pg.wait_for_function("() => document.querySelector('.ready-title')?.innerText.includes('FAULT DETECTED')", timeout=45000)
        pg.wait_for_timeout(1500)

        rec["stage"] = "investigate"
        pg.get_by_placeholder("Describe the problem").fill(QUERY)
        t_ask = time.time()
        pg.get_by_role("button", name="Investigate", exact=True).click()
        pg.wait_for_function("() => document.querySelector('.ready-title')?.innerText.includes('INVESTIGATING')", timeout=15000)

        rec["stage"] = "wait proposal/terminal"
        approve = pg.get_by_role("button", name="APPROVE", exact=True)
        end = time.time() + 120
        while time.time() < end:
            inv = current()
            if inv and (inv["phase"] == "awaiting_approval" or inv["phase"] in TERMINAL):
                break
            time.sleep(0.5)
        inv = current()
        rec["to_decision_s"] = round(time.time() - t_ask, 1)
        if inv["phase"] == "awaiting_approval":
            approve.wait_for(state="visible", timeout=15000)
            approve.click()
            rec["stage"] = "wait recovery"
            end = time.time() + 120
            while time.time() < end:
                inv = current()
                if inv["phase"] in TERMINAL:
                    break
                time.sleep(0.5)
        rec["total_s"] = round(time.time() - t_ask, 1)
        inv = current()
        gt = json.loads(GROUND_TRUTH.read_text())["active_fault"]      # scoring only, after the fact
        d = inv.get("diagnosis") or {}
        rec.update(injected=gt, expected_component=EXPECTED.get(gt), phase=inv["phase"], diagnosed=d.get("faulty_component"),
                   root_cause=d.get("root_cause"), diagnosis_s=inv["diagnosis_seconds"], tool_calls=inv["tool_calls"],
                   model_calls=inv["llm_calls"], tools=[e["tool"] for e in inv["events"] if e["kind"] == "tool_call"],
                   rejected_diagnoses=sum(1 for e in inv["events"] if e["kind"] == "diagnosis_rejected"),
                   timeouts=sum(1 for e in inv["events"] if e["kind"] == "model_timeout"),
                   repair_executed=bool((inv.get("repair") or {}).get("executed")),
                   verified=bool((inv.get("verification") or {}).get("verified")), error=inv.get("error"),
                   investigation_id=inv["id"], leaks=leak_audit(inv["id"]))
        if inv["phase"] == "inconclusive":
            rec["outcome"] = "inconclusive"
        elif inv["phase"] == "error":
            rec["outcome"] = "error"
        else:
            rec["outcome"] = "correct" if d.get("faulty_component") == EXPECTED.get(gt) else "incorrect"
        rec["health_after"] = httpx.get(f"{URL}/api/health", timeout=5).json()["overall"]
    except PWTimeout as e:
        rec["outcome"] = "harness_timeout"
        rec["error"] = f"{rec['stage']}: {str(e).splitlines()[0][:100]}"
    except Exception as e:  # noqa: BLE001
        rec["outcome"] = "harness_error"
        rec["error"] = f"{type(e).__name__}: {str(e)[:140]} ({rec['stage']})"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    exe = (glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")) or [None])[-1]
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.goto(URL)
        for n in range(1, a.runs + 1):
            r = one(pg, n)
            results.append(r)
            print(f"run {n:2d}: injected={r.get('injected')} diagnosed={r.get('diagnosed')} -> {r['outcome'].upper()} | "
                  f"diagnosis {r.get('diagnosis_s')} s | total {r.get('total_s')} s | tools {r.get('tool_calls')} model_calls {r.get('model_calls')} | "
                  f"repair {r.get('repair_executed')} verified {r.get('verified')} | leaks {r.get('leaks')} {r.get('error') or ''}", flush=True)
            if r["outcome"] in ("harness_timeout", "harness_error"):
                pg.reload()
        b.close()
    from collections import Counter
    print("\noutcomes:", dict(Counter(r["outcome"] for r in results)))
    print("faults injected:", dict(Counter(r.get("injected") for r in results)))
    print("leaks found:", sum(1 for r in results if r.get("leaks")))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"started": time.strftime("%Y-%m-%d %H:%M:%S"), "query": QUERY, "runs": results}, indent=2, default=str))


if __name__ == "__main__":
    main()
