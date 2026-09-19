"""HTTP client for the demo robot's process supervisor (restart + status only)."""
from __future__ import annotations

import os

import httpx

SUPERVISOR_URL = os.environ.get("ROBOTOPS_SUPERVISOR_URL", "http://127.0.0.1:8766")


def status(timeout: float = 3.0) -> dict:
    r = httpx.get(f"{SUPERVISOR_URL}/status", timeout=timeout)
    r.raise_for_status()
    return r.json()["components"]


def restart(component: str, timeout: float = 15.0) -> dict:
    r = httpx.post(f"{SUPERVISOR_URL}/restart", json={"component": component}, timeout=timeout)
    return r.json()


def inject(fault: str, timeout: float = 15.0) -> dict:
    r = httpx.post(f"{SUPERVISOR_URL}/inject", json={"fault": fault}, timeout=timeout)
    return r.json()


def reset(timeout: float = 40.0) -> dict:
    r = httpx.post(f"{SUPERVISOR_URL}/reset", timeout=timeout)
    return r.json()
