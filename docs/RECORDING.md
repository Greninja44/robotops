# Recording backup footage (a REAL run, not a mock-up)

Purpose: if the venue network, GPU or laptop misbehaves, you still have genuine footage of RobotOps
diagnosing and repairing a real (simulated) ROS 2 failure. **Do not present the recording as a live demo** - say
"this is a recording of a real run from <date>".

## Before recording (2 minutes)

```bash
./run_demo.sh            # waits until the model is warm and everything is verified
./demo_preflight.sh      # must print  ROBOTOPS DEMO READY
```

* Close anything heavy (other training/eval jobs, browsers with many tabs). The preflight warns about competing load;
  a busy machine makes the recording slower and less representative.
* Browser: Chrome/Edge, **100 % zoom, window 1600x1000** (or full-screen 1920x1080), dark mode on, hide bookmarks bar.
  Open `http://127.0.0.1:8000`.
* The status bar must read **Ready** with green ROS / Agent / Ollama / Model warm / DDS indicators.
* Do a rehearsal run first so the model and caches are warm, then press **Start demo** (Demo controls) again to get a clean slate.

## Recording tool

Any screen recorder works. Suggested (Windows host, WSL2 backend): **Xbox Game Bar** (`Win+G`, then record; capture the browser
window) or **OBS Studio** (Window Capture -> browser, 1080p30, MP4). Record audio only if you narrate live.

## The take (about 60-75 seconds of footage)

| # | Do | What the viewer should see |
|---|---|---|
| 1 | Start recording. Show the console for 3 s | status bar `Ready`, all components healthy, the ROS graph |
| 2 | Demo controls -> **Controller crash** | System shows Controller *failed*, the graph marks `base_controller` unavailable, status bar `Fault detected` |
| 3 | Type **Robot stopped moving. Diagnose it.** and press **Run** | the Investigation stream fills in with timestamped tool calls and real values (e.g. `/cmd_vel  1 publisher · 0 subscribers`) |
| 4 | Wait for the proposal (about 10-20 s) | **Root cause** with evidence, **Proposed action** `restart_component /base_controller`, risk, **Reject / Approve restart**; status bar `Awaiting approval` |
| 5 | Pause 2 s (let viewers read), click **Approve restart** | `approved`, `repair` entries, status bar `Verifying recovery` |
| 6 | Hold on the result for 5 s | **Verification** checks, `24 / 24 checks passed`, `Recovery verified · 5 s`, status bar `Recovery verified`, System all healthy |
| 7 | Stop recording | |

Tips: move the mouse slowly, do not scroll during the run, keep the query exactly as above (the scenario is the most-tested path).

## After recording

1. Watch it once. If the take has a visible glitch (timeout banner, inconclusive result, wrong component), delete it and record again -
   the point is a *representative real run*, so re-recording after a failure is fine, but keep the failed take's numbers in mind
   when you quote reliability (see `benchmarks/hero/` and `benchmarks/random/` for the measured rates).
2. Save the run's evidence next to the video: `logs/investigations/<id>.json` (full event stream) and the matching lines of `logs/audit.jsonl`.
   The investigation id is shown by `curl -s localhost:8000/api/investigations/current | python3 -m json.tool | head`.
3. Name the file `robotops_real_run_<YYYY-MM-DD>.mp4`. Keep a copy off the laptop (USB stick / cloud).
4. On stage, keep the recording ready in a second window, but start with the live demo. If something fails, say so, and switch.

## Automated capture of screenshots (optional)

`scripts/ui_hero_demo.py --runs 1 --shots docs/screenshots/hero` drives the real dashboard through the same sequence and saves
the key frames (ready, fault, approval, resolved). Useful for slides; it is not a substitute for a screen recording.
