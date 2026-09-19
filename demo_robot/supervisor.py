#!/usr/bin/env python3
"""Process supervisor for the demo robot (think: a tiny systemd/launch manager).

HTTP API on 127.0.0.1:8766 (stdlib only):
  GET  /status              process table (running / exit code / pid / restarts)
  POST /restart {component} restart one component with its canonical configuration
  POST /inject  {fault}     inject a fault; "random" picks one without revealing it
  POST /reset               restart everything with canonical configuration
  GET  /faults              available fault ids

Ground truth for injected faults is written only to state/ground_truth.json so the
benchmark can score results. The RobotOps agent never reads that file and the
/inject response for a random fault does not name the fault.
"""
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NODES = ROOT / "nodes.py"
LOG_DIR = ROOT.parent / "logs" / "demo"
STATE_DIR = ROOT / "state"
GROUND_TRUTH = STATE_DIR / "ground_truth.json"
PORT = int(os.environ.get("ROBOTOPS_SUPERVISOR_PORT", "8766"))

COMPONENTS = ["velocity_commander", "base_controller", "wheel_odometry",
              "lidar_driver", "tf_broadcaster", "obstacle_monitor"]

# fault id -> (description, how)
FAULTS = {
    "controller_crash": "base controller process crashes",
    "lidar_failure": "lidar driver stalls (process alive, no data)",
    "tf_failure": "TF broadcaster hangs (process alive, no transforms)",
    "topic_misconfig": "base controller relaunched with wrong cmd_vel topic",
    "node_crash": "obstacle monitor process crashes",
}


class Supervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.procs = {}      # name -> Popen
        self.meta = {}       # name -> dict(started_at, restarts, args)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    def _spawn(self, name, extra_args=()):
        args = [sys.executable, str(NODES), name]
        if extra_args:
            args += ["--ros-args", *extra_args]
        log = open(LOG_DIR / f"{name}.log", "ab")
        p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        log.close()
        m = self.meta.setdefault(name, {"restarts": -1})
        m.update(started_at=time.time(), args=list(extra_args))
        m["restarts"] += 1
        self.procs[name] = p

    def _stop(self, name, timeout=4.0):
        p = self.procs.get(name)
        if p is None or p.poll() is not None:
            return
        p.send_signal(signal.SIGINT)
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(2)

    def start_all(self):
        with self.lock:
            for n in COMPONENTS:
                self._spawn(n)

    def stop_all(self):
        with self.lock:
            for n in COMPONENTS:
                self._stop(n)

    def restart(self, name, extra_args=()):
        if name not in COMPONENTS:
            raise ValueError(f"unknown component {name!r}")
        with self.lock:
            self._stop(name)
            self._spawn(name, extra_args)
        return {"component": name, "pid": self.procs[name].pid}

    def reset(self):
        with self.lock:
            self.stop_all()
            self.meta.clear()
            self.start_all()
        GROUND_TRUTH.write_text(json.dumps({"active_fault": None, "history": self._history()}, indent=2))

    def status(self):
        with self.lock:
            out = {}
            for n in COMPONENTS:
                p, m = self.procs.get(n), self.meta.get(n, {})
                rc = None if p is None else p.poll()
                out[n] = {"state": "not_started" if p is None else ("running" if rc is None else "exited"),
                          "pid": p.pid if p else None, "exit_code": rc,
                          "restarts": max(m.get("restarts", 0), 0),
                          "uptime_s": round(time.time() - m["started_at"], 1) if p and rc is None else None,
                          "launch_args": m.get("args", [])}
            return out

    def _signal(self, name):
        p = self.procs.get(name)
        if p is None or p.poll() is not None:
            raise RuntimeError(f"{name} is not running")
        p.send_signal(signal.SIGUSR1)

    def inject(self, fault):
        if fault == "random":
            fault = random.choice(list(FAULTS))
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}")
        with self.lock:
            if fault == "controller_crash":
                self._signal("base_controller")
            elif fault == "lidar_failure":
                self._signal("lidar_driver")
            elif fault == "tf_failure":
                self._signal("tf_broadcaster")
            elif fault == "node_crash":
                self._signal("obstacle_monitor")
            elif fault == "topic_misconfig":
                self._stop("base_controller")
                self._spawn("base_controller", ["-p", "cmd_vel_topic:=/cmd_vel_nav"])
        hist = self._history() + [{"fault": fault, "time": time.time()}]
        GROUND_TRUTH.write_text(json.dumps({"active_fault": fault, "history": hist[-50:]}, indent=2))
        return fault

    def _history(self):
        try:
            return json.loads(GROUND_TRUTH.read_text()).get("history", [])
        except (OSError, ValueError):
            return []


SUP = Supervisor()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[supervisor] " + fmt % args + "\n")

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/status":
            self._reply(200, {"components": SUP.status()})
        elif self.path == "/faults":
            self._reply(200, {"faults": FAULTS})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        body = self._body()
        try:
            if self.path == "/restart":
                self._reply(200, {"ok": True, **SUP.restart(str(body.get("component", "")))})
            elif self.path == "/inject":
                requested = str(body.get("fault", ""))
                fault = SUP.inject(requested)
                # A random fault is never named in the response.
                self._reply(200, {"ok": True, "injected": "random (hidden)" if requested == "random" else fault})
            elif self.path == "/reset":
                SUP.reset()
                self._reply(200, {"ok": True})
            else:
                self._reply(404, {"error": "not found"})
        except (ValueError, RuntimeError) as e:
            self._reply(400, {"ok": False, "error": str(e)})


def main():
    SUP.start_all()
    GROUND_TRUTH.write_text(json.dumps({"active_fault": None, "history": SUP._history()}, indent=2))
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"[supervisor] demo robot up, API on http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        SUP.stop_all()
        print("[supervisor] stopped", flush=True)


if __name__ == "__main__":
    main()
