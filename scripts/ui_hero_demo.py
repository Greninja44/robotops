#!/usr/bin/env python3
"""Drive the REAL dashboard through the hero demo, N times in a row (real ROS, real model, real clicks).

  START DEMO -> Controller Failure -> "Robot stopped moving. Diagnose it." -> Investigate -> APPROVE ->
  wait for INCIDENT RESOLVED -> read the incident card -> check the robot is HEALTHY.

  python scripts/ui_hero_demo.py --runs 10 [--stop-on-fail] [--shots docs/screenshots/hero]

Stops at the first failure with --stop-on-fail (the consecutive-success counter then restarts after a fix).
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
QUERY = "Robot stopped moving. Diagnose it."


def banner(pg) -> str:
    return pg.locator(".ready-title").inner_text()


def wait_banner(pg, text: str, timeout_s: float):
    pg.wait_for_function("t => document.querySelector('.ready-title')?.innerText.includes(t)", arg=text,
                         timeout=timeout_s * 1000)


def incident_card(pg) -> dict:
    return pg.evaluate("() => Object.fromEntries([...document.querySelectorAll('.incident-grid > div')]"
                       ".map(d => [d.querySelector('label').innerText, d.querySelector('b').innerText]))")


def one_run(pg, n: int, shots: Path | None) -> dict:
    rec = {"run": n, "ok": False, "stage": "start"}
    t = {}
    try:
        rec["stage"] = "wait for START DEMO"
        start = pg.get_by_role("button", name="START DEMO")
        pg.wait_for_function("() => { const b = [...document.querySelectorAll('button')].find(x => x.innerText === 'START DEMO'); return b && !b.disabled }", timeout=120000)
        rec["stage"] = "start demo"
        t0 = time.time()
        start.click()
        pg.wait_for_function("() => document.querySelector('.ready-title')?.innerText.includes('PREPARING')", timeout=15000)
        wait_banner(pg, "READY FOR DEMO", 120)
        rec["prepare_s"] = round(time.time() - t0, 1)
        if shots and n == 1:
            pg.screenshot(path=str(shots / "01_ready.png"))

        rec["stage"] = "inject"
        t["inject"] = time.time()
        pg.get_by_role("button", name="Controller Failure").click()
        wait_banner(pg, "FAULT DETECTED", 40)
        rec["fault_visible_s"] = round(time.time() - t["inject"], 1)
        pg.wait_for_timeout(1500)
        if shots and n == 1:
            pg.screenshot(path=str(shots / "02_fault.png"))

        rec["stage"] = "investigate"
        pg.get_by_placeholder("Describe the problem").fill(QUERY)
        t["ask"] = time.time()
        pg.get_by_role("button", name="Investigate", exact=True).click()
        pg.wait_for_function("() => document.querySelector('.ready-title')?.innerText.includes('INVESTIGATING')", timeout=15000)

        rec["stage"] = "wait proposal"
        approve = pg.get_by_role("button", name="APPROVE", exact=True)
        approve.wait_for(state="visible", timeout=90000)
        rec["to_proposal_s"] = round(time.time() - t["ask"], 1)
        if shots and n == 1:
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(shots / "03_approval.png"))

        rec["stage"] = "approve"
        t["approve"] = time.time()
        approve.click()
        rec["stage"] = "wait recovery"
        pg.locator(".incident-title").wait_for(state="visible", timeout=90000)
        rec["approve_to_resolved_s"] = round(time.time() - t["approve"], 1)
        rec["ask_to_resolved_s"] = round(time.time() - t["ask"], 1)
        pg.wait_for_timeout(600)
        rec["incident"] = incident_card(pg)
        if shots and n == 1:
            pg.screenshot(path=str(shots / "04_resolved.png"))

        rec["stage"] = "check healthy"
        health = httpx.get(f"{URL}/api/health", timeout=5).json()
        rec["health_after"] = health["overall"]
        if health["overall"] != "HEALTHY":     # keep the evidence: which component, and does it clear on its own?
            rec["health_detail"] = {k: f"{v['state']}: {v['detail']}" for k, v in health["components"].items() if v["state"] != "HEALTHY"}
            time.sleep(4)
            rec["health_4s_later"] = httpx.get(f"{URL}/api/health", timeout=5).json()["overall"]
        inv = httpx.get(f"{URL}/api/investigations/current", timeout=5).json()
        rec.update(phase=inv["phase"], model_calls=inv["llm_calls"], tool_calls=inv["tool_calls"],
                   diagnosis=(inv["diagnosis"] or {}).get("faulty_component"),
                   diagnosis_s=inv["diagnosis_seconds"],
                   verification=f"{sum(c['passed'] for c in inv['verification']['checks'])}/{len(inv['verification']['checks'])}",
                   tools=[e["tool"] for e in inv["events"] if e["kind"] == "tool_call"],
                   timeouts=sum(1 for e in inv["events"] if e["kind"] == "model_timeout"))
        rec["ok"] = (inv["phase"] == "resolved" and health["overall"] == "HEALTHY" and rec["diagnosis"] == "base_controller")
        if not rec["ok"]:
            rec["error"] = f"phase={inv['phase']} health={health['overall']} diagnosis={rec['diagnosis']}"
    except PWTimeout as e:
        rec["error"] = f"timeout at stage '{rec['stage']}': {str(e).splitlines()[0][:120]}"
        try:
            rec["banner"] = banner(pg)
            if shots:
                pg.screenshot(path=str(shots / f"FAIL_run{n}.png"))
            rec["investigation"] = httpx.get(f"{URL}/api/investigations/current", timeout=5).json()
            rec["investigation"].pop("evidence", None)
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {str(e)[:160]} (stage '{rec['stage']}')"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--stop-on-fail", action="store_true")
    ap.add_argument("--shots", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    exe = (glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")) or [None])[-1]
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.goto(URL)
        for n in range(1, a.runs + 1):
            r = one_run(pg, n, shots)
            results.append(r)
            print(f"run {n}: {'OK ' if r['ok'] else 'FAIL'} diagnosis {r.get('diagnosis_s')} s | ask->proposal {r.get('to_proposal_s')} s | "
                  f"approve->resolved {r.get('approve_to_resolved_s')} s | total {r.get('ask_to_resolved_s')} s | prepare {r.get('prepare_s')} s | "
                  f"model_calls {r.get('model_calls')} tools {r.get('tools')} timeouts {r.get('timeouts')} {r.get('error', '')}", flush=True)
            if not r["ok"] and a.stop_on_fail:
                break
            if not r["ok"]:
                pg.reload()
        b.close()
    consecutive = 0
    for r in results:
        consecutive = consecutive + 1 if r["ok"] else 0
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} runs OK; consecutive successes at end: {consecutive}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"started": time.strftime("%Y-%m-%d %H:%M:%S"), "query": QUERY, "runs": results}, indent=2, default=str))


if __name__ == "__main__":
    main()
