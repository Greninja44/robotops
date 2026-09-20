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
mode = {"stall": "first"}     # first | all | none | bad
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
        if mode["stall"] == "bad":                      # a diagnosis citing evidence that does not exist -> rejected until INCONCLUSIVE
            content = {"reason_summary": "guess", "action": "diagnose", "root_cause": "unknown", "faulty_component": "base_controller",
                       "evidence_ids": ["E99"], "recommended_action": "restart_component"}
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
            pg.wait_for_selector('[data-testid="query"]:not([disabled])', timeout=60000)

            def ask():
                pg.locator('[data-testid="query"]').fill("Diagnose the robot.")
                pg.locator('[data-testid="run"]').click()

            # 1) first request stalls -> retried -> continues
            httpx.post("http://127.0.0.1:9099/control", json={"stall": "first"})
            ask()
            pg.get_by_text("no response after 3s - retrying").wait_for(timeout=20000)
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(shots / "01_timeout_retrying.png"))
            print("retry alert visible: YES")
            pg.get_by_text("retried, continued").wait_for(timeout=30000)
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(shots / "02_timeout_retried_and_continued.png"))
            print("retry-succeeded state visible: YES")
            pg.wait_for_selector('[data-testid="status-bar"]:not([data-state="investigating"])', timeout=90000)
            # 2) every request stalls -> two attempts -> safe stop
            httpx.post("http://127.0.0.1:9099/control", json={"stall": "all"})
            pg.locator('[data-testid="start-demo"]').click()
            pg.wait_for_selector('[data-testid="status-bar"][data-state="ready"]', timeout=120000)
            ask()
            pg.get_by_text("investigation stopped, nothing changed").wait_for(timeout=60000)
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
