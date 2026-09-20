#!/usr/bin/env python3
"""Capture the dashboard in every state (dev tool for UI work). Drives the app through the API only, so it keeps working when the
markup changes. Real robot, real model for the main states; the degraded states (timeout, inconclusive) use a fake stalling model server
on a second backend (port 8001), exactly like scripts/ui_timeout_check.py.

  python scripts/ui_states.py --out /path/to/dir [--size 1440x900] [--only main|degraded]
"""
import argparse
import glob
import importlib.util
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
A = "http://127.0.0.1:8000"


def chrome():
    return (glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")) or [None])[-1]


def wait(pred, timeout=90, every=0.25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = pred()
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
        time.sleep(every)
    raise TimeoutError("condition not met")


def cur(base):
    return httpx.get(f"{base}/api/investigations/current", timeout=5).json()


def main_states(pg, out: Path):
    def shot(name):
        pg.wait_for_timeout(350)
        pg.screenshot(path=str(out / f"{name}.png"))
        print("captured", name)
    httpx.post(f"{A}/api/demo/prepare", json={}, timeout=120)
    pg.goto(A)
    pg.wait_for_timeout(2500)
    shot("01_ready_healthy")
    httpx.post(f"{A}/api/faults/inject", json={"fault": "controller_crash"}, timeout=30)
    wait(lambda: httpx.get(f"{A}/api/health", timeout=5).json()["overall"] != "HEALTHY", 40)
    pg.wait_for_timeout(1500)
    shot("02_fault")
    httpx.post(f"{A}/api/investigations", json={"query": "Robot stopped moving. Diagnose it."}, timeout=10)
    wait(lambda: (cur(A) or {}).get("tool_calls", 0) >= 2 and cur(A)["phase"] in ("investigating", "diagnosing"), 60, 0.15)
    shot("03_investigating")
    inv = wait(lambda: cur(A)["phase"] == "awaiting_approval" and cur(A), 90)
    shot("04_awaiting_approval")
    httpx.post(f"{A}/api/investigations/{inv['id']}/approve", json={"proposal_id": inv["proposal"]["id"], "operator": "ui-states"}, timeout=10)
    wait(lambda: cur(A)["phase"] in ("repairing", "verifying"), 30, 0.1)
    shot("05_repairing_verifying")
    wait(lambda: cur(A)["phase"] == "resolved", 60)
    shot("06_recovered")


def degraded_states(out: Path, size):
    spec = importlib.util.spec_from_file_location("tcheck", ROOT / "scripts" / "ui_timeout_check.py")
    tc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tc)
    fake = ThreadingHTTPServer(("127.0.0.1", 9099), tc.Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    env = {**os.environ, "ROBOTOPS_OLLAMA_URL": "http://127.0.0.1:9099", "ROBOTOPS_LLM_TIMEOUT": str(tc.TIMEOUT_S)}
    be = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "backend.main:app", "--port", "8001"], cwd=ROOT, env=env,
                          stdout=open(ROOT / "logs" / "ui_states_backend.log", "w"), stderr=subprocess.STDOUT)
    B = "http://127.0.0.1:8001"
    try:
        wait(lambda: httpx.get(f"{B}/api/readiness", timeout=2).json().get("infra_ready"), 90, 1)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=chrome(), args=["--no-sandbox"])
            pg = b.new_page(viewport=size)
            pg.goto(B)
            pg.wait_for_timeout(2000)

            def shot(name):
                pg.wait_for_timeout(350)
                pg.screenshot(path=str(out / f"{name}.png"))
                print("captured", name)
            tc.mode["stall"] = "first"
            httpx.post(f"{B}/api/investigations", json={"query": "Diagnose the robot."}, timeout=10)
            wait(lambda: any(e["kind"] == "model_timeout" for e in cur(B)["events"]), 30, 0.2)
            shot("07_timeout_retrying")
            wait(lambda: cur(B)["phase"] in ("inconclusive", "error", "resolved", "awaiting_approval", "healthy"), 60)
            httpx.post(f"{B}/api/demo/prepare", json={}, timeout=120)
            tc.mode["stall"] = "all"
            httpx.post(f"{B}/api/investigations", json={"query": "Diagnose the robot."}, timeout=10)
            wait(lambda: cur(B)["phase"] == "error", 60)
            shot("08_timeout_stopped")
            httpx.post(f"{B}/api/demo/prepare", json={}, timeout=120)
            tc.mode["stall"] = "bad"
            httpx.post(f"{B}/api/investigations", json={"query": "Diagnose the robot."}, timeout=10)
            wait(lambda: cur(B)["phase"] == "inconclusive", 60)
            shot("09_inconclusive")
            b.close()
    finally:
        be.terminate()
        fake.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    w, h = (int(x) for x in a.size.split("x"))
    size = {"width": w, "height": h}
    if a.only != "degraded":
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=chrome(), args=["--no-sandbox"])
            pg = b.new_page(viewport=size)
            main_states(pg, out)
            b.close()
    if a.only != "main":
        degraded_states(out, size)


if __name__ == "__main__":
    sys.exit(main())
