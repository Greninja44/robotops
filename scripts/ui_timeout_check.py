#!/usr/bin/env python3
"""Visual check of the MODEL RESPONSE TIMEOUT states in the real dashboard.

Starts a fake Ollama that stalls investigation requests on purpose, a second RobotOps backend (port 8001) pointed at it with a short
timeout, then drives the real UI and screenshots (1) the "retrying" alert and (2) the final "stopped safely" alert.
The real robot and real ROS tools are used; only the model server is fake.

  python scripts/ui_timeout_check.py --shots docs/screenshots/timeout
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STALL_S, TIMEOUT_S = 7.0, 3.0
mode = {"stall": "first"}     # first | all | none
hits = {"n": 0}


class Fake(BaseHTTPRequestHandler):
    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json({"models": [{"name": "qwen3:4b"}]})
        elif self.path == "/api/ps":
            self._json({"models": [{"name": "qwen3:4b", "size": 100, "size_vram": 100, "context_length": 6144}]})
        else:
            self._json({})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path == "/control":
            mode["stall"] = json.loads(body)["stall"]
            hits["n"] = 0
            return self._json({"ok": True})
        if "Operator report" in body:                       # an investigation request (not the warm-up)
            hits["n"] += 1
            if mode["stall"] == "all" or (mode["stall"] == "first" and hits["n"] == 1):
                time.sleep(STALL_S)                      # stalls past the request timeout
            elif mode["stall"] == "first" and hits["n"] == 2:
                time.sleep(2.0)                          # the retry is slow but succeeds (so "retrying" is visible for ~2 s)
        content = {"reason_summary": "check process status", "action": "tool", "tool": "get_component_status", "arguments": {}}
        try:
            self._json({"message": {"content": json.dumps(content)}, "eval_count": 20, "total_duration": 1_000_000_000})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="docs/screenshots/timeout")
    a = ap.parse_args()
    shots = ROOT / a.shots
    shots.mkdir(parents=True, exist_ok=True)
    fake = ThreadingHTTPServer(("127.0.0.1", 9099), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    env = {**os.environ, "ROBOTOPS_OLLAMA_URL": "http://127.0.0.1:9099", "ROBOTOPS_LLM_TIMEOUT": str(TIMEOUT_S)}
    be = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "backend.main:app", "--port", "8001"], cwd=ROOT, env=env,
                          stdout=open(ROOT / "logs" / "timeout_check_backend.log", "w"), stderr=subprocess.STDOUT)
    ok = False
    try:
        for _ in range(60):
            try:
                if httpx.get("http://127.0.0.1:8001/api/readiness", timeout=2).json().get("infra_ready"):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
        exe = (glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")) or [None])[-1]
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": 1600, "height": 1000})
            pg.goto("http://127.0.0.1:8001")
            pg.wait_for_function("() => [...document.querySelectorAll('button')].some(x => x.innerText === 'Investigate' && !x.disabled) || document.querySelector('input')", timeout=60000)

            def ask():
                pg.get_by_placeholder("Describe the problem").fill("Diagnose the robot.")
                pg.get_by_role("button", name="Investigate", exact=True).click()

            # 1) first request stalls -> retried -> continues
            httpx.post("http://127.0.0.1:9099/control", json={"stall": "first"})
            ask()
            pg.get_by_text("Retrying investigation").wait_for(timeout=20000)
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(shots / "01_timeout_retrying.png"))
            print("retry alert visible: YES")
            pg.get_by_text("Retried automatically and continued").wait_for(timeout=30000)
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(shots / "02_timeout_retried_and_continued.png"))
            print("retry-succeeded state visible: YES")
            pg.wait_for_function("() => !document.querySelector('.ready-title')?.innerText.includes('INVESTIGATING')", timeout=90000)
            # 2) every request stalls -> two attempts -> safe stop
            httpx.post("http://127.0.0.1:9099/control", json={"stall": "all"})
            pg.get_by_role("button", name="START DEMO").click()
            pg.wait_for_function("() => document.querySelector('.ready-title')?.innerText.includes('READY')", timeout=120000)
            ask()
            pg.get_by_text("stopped safely").wait_for(timeout=60000)
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(shots / "03_timeout_stopped_safely.png"))
            print("final timeout alert visible: YES")
            inv = httpx.get("http://127.0.0.1:8001/api/investigations/current").json()
            print("final phase:", inv["phase"], "| error:", inv["error"], "| repair executed:", bool(inv.get("repair")))
            ok = inv["phase"] == "error" and not inv.get("repair")
            b.close()
    finally:
        be.terminate()
        fake.shutdown()
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
