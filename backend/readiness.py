"""Demo readiness / preflight. One implementation shared by the dashboard, `demo_preflight.sh` and the START DEMO gate.

Every check returns {"id", "label", "status": pass|warn|fail, "detail"}. The demo is READY only if nothing failed.
Warnings (e.g. moderately busy machine) are shown but do not block. Nothing here fakes a result: each check
measures the real system (ROS graph, Ollama, GPU, load, supervisor).
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

from backend.agent import llm
from backend.ros_tools import supervisor
from backend.ros_tools.common import manifest
from backend.safety import policies

ROOT = Path(__file__).resolve().parents[1]
PASS, WARN, FAIL = "pass", "warn", "fail"
# Our own long-running tools that compete for the GPU/CPU and are safe for us to stop before a demo.
OWN_HEAVY = ("run_benchmark.py", "profile_diagnosis.py", "diagnose_cli.py")
WARMUP_OK_S, WARMUP_WARN_S = 6.0, 20.0


def check(id_: str, label: str, status: str, detail: str) -> dict:
    return {"id": id_, "label": label, "status": status, "detail": detail}


def _run(cmd: list[str], timeout: float = 4.0) -> str | None:
    """Fixed-argument subprocess (no shell, no user input)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------- host
def gpu_info() -> dict | None:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"])
    if not out:
        return None
    try:
        name, used, total, util = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"name": name, "used_mb": int(used), "total_mb": int(total), "free_mb": int(total) - int(used),
                "util_pct": int(util)}
    except ValueError:
        return None


def host_load() -> dict:
    load1 = float(Path("/proc/loadavg").read_text().split()[0])
    cpus = os.cpu_count() or 1
    mem = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines())}
    return {"load1": load1, "cpus": cpus, "load_per_cpu": round(load1 / cpus, 2),
            "mem_available_mb": mem["MemAvailable"] // 1024, "swap_used_mb": (mem["SwapTotal"] - mem["SwapFree"]) // 1024}


def busy_processes(min_cpu: float = 60.0) -> list[dict]:
    """Processes above min_cpu % CPU, split into ours (safe to stop) and foreign (warn only)."""
    out = _run(["ps", "-eo", "pid,pcpu,rss,args", "--sort=-pcpu"])
    res = []
    for line in (out or "").splitlines()[1:8]:
        m = re.match(r"\s*(\d+)\s+([\d.]+)\s+(\d+)\s+(.*)", line)
        if not m or float(m.group(2)) < min_cpu:
            continue
        args = m.group(4)
        if "ollama" in args.lower() or "llama" in args.lower():
            continue  # the model server itself
        res.append({"pid": int(m.group(1)), "cpu": float(m.group(2)), "rss_mb": int(m.group(3)) // 1024,
                    "cmd": args[:90], "ours": any(x in args for x in OWN_HEAVY)})
    return res


def stop_own_heavy_processes() -> list[int]:
    """Terminate OUR benchmark/profiler processes (never anything else). Returns stopped PIDs."""
    stopped = []
    for p in busy_processes(min_cpu=0.0):
        pass
    out = _run(["ps", "-eo", "pid,args"]) or ""
    for line in out.splitlines()[1:]:
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if m and any(x in m.group(2) for x in OWN_HEAVY) and "pgrep" not in m.group(2) and int(m.group(1)) != os.getpid():
            try:
                os.kill(int(m.group(1)), 15)
                stopped.append(int(m.group(1)))
            except OSError:
                pass
    return stopped


# ---------------------------------------------------------------------------- checks
def check_environment() -> list[dict]:
    out = []
    distro, rmw, dom = os.environ.get("ROS_DISTRO"), os.environ.get("RMW_IMPLEMENTATION"), os.environ.get("ROS_DOMAIN_ID")
    ok = distro and rmw == "rmw_cyclonedds_cpp" and dom
    out.append(check("ros_env", "ROS environment", PASS if ok else FAIL,
                     f"ROS {distro}, {rmw}, domain {dom}" if ok else
                     f"missing ROS env (ROS_DISTRO={distro}, RMW={rmw}, ROS_DOMAIN_ID={dom}) - source scripts/env.sh"))
    uri = os.environ.get("CYCLONEDDS_URI", "")
    path = uri.replace("file://", "")
    good = bool(path) and Path(path).is_file() and "MaxMessageSize" in Path(path).read_text()
    out.append(check("dds_config", "CycloneDDS config (WSL2 fragmentation)", PASS if good else FAIL,
                     path if good else "CYCLONEDDS_URI missing or lacks MaxMessageSize - large messages (/scan) would be dropped"))
    return out


def check_robot(client) -> list[dict]:
    """Discovery, expected nodes/topics, TF, odometry, diagnostics: reuse the production verifier (24 live checks)."""
    from backend.agent import verification
    out = []
    try:
        nodes = client.node_names()
    except Exception as e:  # noqa: BLE001
        return [check("ros_client", "ROS 2 client", FAIL, f"{type(e).__name__}: {e}")]
    out.append(check("ros_client", "ROS 2 client", PASS, "rclpy observer running"))
    expected = list(manifest()["nodes"])
    missing = [n for n in expected if n not in nodes]
    out.append(check("discovery", "DDS discovery / expected nodes", PASS if not missing else FAIL,
                     f"all {len(expected)} nodes visible" if not missing else f"missing: {', '.join(missing)}"))
    checks = verification.run_checks(client, None)
    groups = {"topics": lambda c: "publishing" in c["check"] or "subscribed" in c["check"],
              "tf": lambda c: c["check"].startswith("TF "),
              "motion": lambda c: c["check"].startswith("odometry"),
              "diagnostics": lambda c: "diagnostics" in c["check"]}
    labels = {"topics": "Expected topics (rates + subscribers)", "tf": "TF transforms", "motion": "Robot moving (odometry)",
              "diagnostics": "Component diagnostics"}
    for gid, sel in groups.items():
        sub = [c for c in checks if sel(c)]
        bad = [c for c in sub if not c["passed"]]
        out.append(check(gid, labels[gid], PASS if not bad else FAIL,
                         f"{len(sub) - len(bad)}/{len(sub)} ok" if not bad else "; ".join(f"{c['check']}: {c['detail']}" for c in bad[:3])))
    bad_all = [c for c in checks if not c["passed"]]
    out.append(check("verification", "Verification subsystem (24 live checks)", PASS if not bad_all else WARN,
                     f"{len(checks)}/{len(checks)} pass on the healthy robot" if not bad_all
                     else f"{len(checks) - len(bad_all)}/{len(checks)} pass (robot currently unhealthy)"))
    return out


async def check_llm(warm: bool) -> list[dict]:
    out = []
    st = await llm.status()
    if not st["reachable"]:
        return [check("ollama", "Ollama", FAIL, f"not reachable at {llm.OLLAMA_URL}")]
    out.append(check("ollama", "Ollama", PASS, f"reachable at {llm.OLLAMA_URL}"))
    if not st["model_available"]:
        return out + [check("model", f"Model {llm.MODEL}", FAIL, f"not installed (have: {', '.join(st['models'])})")]
    if warm:
        w = await llm.warmup()
        if not w["ok"]:
            return out + [check("model", f"Model {llm.MODEL}", FAIL, f"warm-up failed: {w.get('error')}")]
        st = await llm.status()
        s = w["seconds"]
        out.append(check("warmup", "Model warm-up generation", PASS if s <= WARMUP_OK_S else WARN if s <= WARMUP_WARN_S else FAIL,
                         f"answered in {s:.1f} s"))
    if st["warm"]:
        place = st.get("placement") or "?"
        out.append(check("model", f"Model {llm.MODEL}", PASS if place.startswith("100") else WARN,
                         f"WARM, {place}, context {st.get('context')}" + ("" if place.startswith("100") else " (partly on CPU: slower)")))
    else:
        out.append(check("model", f"Model {llm.MODEL}", FAIL, "COLD - not loaded in Ollama (first call would pay load latency)"))
    return out


def check_host() -> list[dict]:
    out = []
    g = gpu_info()
    if g is None:
        out.append(check("gpu", "GPU / VRAM", WARN, "nvidia-smi not available"))
    else:
        out.append(check("gpu", "GPU / VRAM", PASS if g["free_mb"] >= 1200 else WARN if g["free_mb"] >= 500 else FAIL,
                         f"{g['name']}: {g['used_mb']}/{g['total_mb']} MiB used, {g['util_pct']}% util"))
    h = host_load()
    bad = h["load_per_cpu"] > 1.2 or h["mem_available_mb"] < 800
    warn = h["load_per_cpu"] > 0.5 or h["mem_available_mb"] < 2000
    out.append(check("load", "System load", FAIL if bad else WARN if warn else PASS,
                     f"load {h['load1']:.1f} on {h['cpus']} CPUs, {h['mem_available_mb']} MB RAM free, {h['swap_used_mb']} MB swap used"))
    procs = busy_processes()
    if procs:
        ours = [p for p in procs if p["ours"]]
        out.append(check("background", "Background processes", FAIL if ours else WARN,
                         "; ".join(f"pid {p['pid']} {p['cpu']:.0f}% CPU {'(RobotOps benchmark/profiler - will be stopped by START DEMO)' if p['ours'] else p['cmd']}"
                                   for p in procs[:3])))
    else:
        out.append(check("background", "Background processes", PASS, "no competing heavy processes"))
    return out


def check_services(api_url: str | None) -> list[dict]:
    out = []
    try:
        f = supervisor.faults() if hasattr(supervisor, "faults") else None
        r = httpx.get(f"{supervisor.SUPERVISOR_URL}/faults", timeout=3).json()["faults"]
        out.append(check("injector", "Fault injector", PASS if len(r) >= 5 else FAIL, f"{len(r)} fault types available"))
        st = supervisor.status(3.0)
        running = [n for n, c in st.items() if c["state"] == "running"]
        allow = len(policies.REPAIRABLE)
        out.append(check("repair", "Repair service (allowlisted)", PASS if len(running) == len(st) and allow else FAIL,
                         f"supervisor up, {len(running)}/{len(st)} processes running, {allow} allowlisted components"))
        _ = f
    except Exception as e:  # noqa: BLE001
        out.append(check("injector", "Fault injector", FAIL, f"demo supervisor unreachable ({type(e).__name__})"))
        out.append(check("repair", "Repair service (allowlisted)", FAIL, "demo supervisor unreachable"))
    if api_url:
        try:
            s = httpx.get(f"{api_url}/api/status", timeout=3)
            out.append(check("backend", "Backend API", PASS if s.status_code == 200 else FAIL, f"{api_url} HTTP {s.status_code}"))
        except Exception as e:  # noqa: BLE001
            out.append(check("backend", "Backend API", FAIL, f"not reachable at {api_url} ({type(e).__name__})"))
        try:
            page = httpx.get(f"{api_url}/", timeout=3).text
            out.append(check("frontend", "Frontend", PASS if 'id="root"' in page else FAIL,
                             "dashboard served" if 'id="root"' in page else "dashboard not built (cd frontend && npm run build)"))
        except Exception as e:  # noqa: BLE001
            out.append(check("frontend", "Frontend", FAIL, f"not reachable ({type(e).__name__})"))
    return out


# ---------------------------------------------------------------------------- aggregate
def summarize(checks: list[dict]) -> dict:
    failed = [c for c in checks if c["status"] == FAIL]
    warned = [c for c in checks if c["status"] == WARN]
    return {"ready": not failed, "reason": "; ".join(f"{c['label']}: {c['detail']}" for c in failed[:2]) or None,
            "warnings": [f"{c['label']}: {c['detail']}" for c in warned], "checks": checks, "ts": time.time()}


async def run_preflight(client, *, warm: bool = True, api_url: str | None = None) -> dict:
    """Full preflight. `warm=True` runs a real generation to prove the model responds."""
    checks = check_environment()
    checks += await asyncio.to_thread(check_robot, client)
    checks += await check_llm(warm)
    checks += await asyncio.to_thread(check_host)
    checks += await asyncio.to_thread(check_services, api_url)
    return summarize(checks)


def chips(checks: list[dict], health_overall: str | None = None) -> dict:
    """The five dashboard readiness chips derived from the same checks."""
    by = {c["id"]: c for c in checks}

    def st(*ids):
        cs = [by[i] for i in ids if i in by]
        if not cs:
            return "UNKNOWN"
        return "FAIL" if any(c["status"] == FAIL for c in cs) else "WARN" if any(c["status"] == WARN for c in cs) else "READY"
    model = by.get("model")
    return {"ros": st("ros_env", "ros_client"), "agent": st("backend", "injector", "repair") if "backend" in by else st("injector", "repair"),
            "ollama": st("ollama"),
            "model": ("WARM" if model and model["status"] != FAIL else "COLD") if model else "UNKNOWN",
            "dds": st("dds_config", "discovery")}


async def _main():
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("ROBOTOPS_API", "http://127.0.0.1:8000"))
    ap.add_argument("--no-warm", action="store_true")
    a = ap.parse_args()
    from backend.ros_tools.client import get_client
    client = get_client()
    try:
        client.start()
        await asyncio.sleep(3)
    except Exception:  # noqa: BLE001 - reported by the checks
        pass
    res = await run_preflight(client, warm=not a.no_warm, api_url=a.api)
    icon = {PASS: "\033[32m[x]\033[0m", WARN: "\033[33m[!]\033[0m", FAIL: "\033[31m[ ]\033[0m"}
    for c in res["checks"]:
        print(f"{icon[c['status']]} {c['label']:<44} {c['detail']}")
    print()
    bar = "=" * 44
    if res["ready"]:
        print(f"\033[32m{bar}\nROBOTOPS DEMO READY\n{bar}\033[0m")
        for w in res["warnings"]:
            print(f"  warning: {w}")
    else:
        print(f"\033[31m{bar}\nDEMO NOT READY\nReason: {res['reason']}\n{bar}\033[0m")
    client.shutdown()
    sys.exit(0 if res["ready"] else 1)


if __name__ == "__main__":
    asyncio.run(_main())
