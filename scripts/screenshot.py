#!/usr/bin/env python3
"""Capture dashboard screenshots (dev tool). usage: screenshot.py out.png [--wait SECONDS]"""
import sys
from playwright.sync_api import sync_playwright

out = sys.argv[1]
wait = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 2.5
with sync_playwright() as p:
    import glob, os
    exe = (glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")) or [None])[-1]
    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto("http://127.0.0.1:8000/")
    pg.wait_for_timeout(int(wait * 1000))
    pg.screenshot(path=out, full_page=True)
    b.close()
