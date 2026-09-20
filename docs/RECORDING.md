# Recording a real run (demo GIF / video)

The GIF and MP4 in [`docs/media/`](media) are recordings of a **real run** of the dashboard against the live ROS 2 demo robot: no frames are composited,
edited or mocked up. This page explains how they were made so they can be reproduced or replaced.

## Automated capture (what produced `docs/media/`)

```bash
./run_demo.sh                                   # wait for: ROBOTOPS DEMO READY   (close heavy jobs first; ./demo_preflight.sh warns about load)
.venv/bin/python scripts/ui_hero_demo.py --video docs/media/hero-demo.webm
```

`--video` prepares the system (the same call as the **Start demo** button), then drives the real dashboard in headless Chromium at 1440x900 through the hero
sequence: inject **Controller crash** → type *Robot stopped moving. Diagnose it.* → **Run** → wait for the proposal → **Approve restart** → wait for **Recovery verified**.
It adds short scripted reading pauses at the key states (about 2.5 s ready, 2 s after the fault, 4.5 s at the approval, 6 s at the end) and records exactly one run
(about 30 s). The result of the run (ok / diagnosis time / total time) is printed; if it is not `ok=True`, discard the take.

Convert to a GIF and an MP4 (ffmpeg):

```bash
ffmpeg -i docs/media/hero-demo.webm \
  -vf "fps=6,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  docs/media/hero-demo.gif
ffmpeg -i docs/media/hero-demo.webm -c:v libx264 -crf 28 -pix_fmt yuv420p -movflags +faststart docs/media/hero-demo.mp4
```

The committed GIF is 960 px wide at 6 fps with 96 colours (about 4.8 MB); the MP4 (about 0.7 MB) is the higher-quality copy. Raw `.webm` files are git-ignored.

## Manual capture (screen recorder)

Any recorder works (OBS Studio, Xbox Game Bar on the Windows host of a WSL2 setup, `peek`, ...).

1. `./run_demo.sh` and `./demo_preflight.sh` must print `ROBOTOPS DEMO READY`. Close anything heavy: a busy machine makes the recording slower and less representative.
2. Browser at 100 % zoom, window about 1440x900, dark mode, no bookmarks bar; open <http://127.0.0.1:8000>. The status bar must read **Ready** with green ROS / Agent / Ollama / Model / DDS indicators.
3. Do one rehearsal so the model and caches are warm, then press **Start demo** again for a clean slate and start recording.
4. Follow the steps in [Reproduce the demo](../README.md#reproduce-the-demo). Move the mouse slowly, do not scroll, keep the query exactly as written.
5. Stop recording after **Recovery verified** has been visible for a few seconds.

If a take shows a glitch (timeout banner, inconclusive result, wrong component), keep the failed take's numbers in mind when quoting reliability and re-record;
see `benchmarks/hero/` and `benchmarks/random/` for the measured rates.

## Evidence that belongs with a recording

`logs/investigations/<id>.json` holds the full event stream of every investigation (the id is shown by
`curl -s localhost:8000/api/investigations/current | python3 -m json.tool | head`), and `logs/audit.jsonl` the audit lines.
[`docs/examples/controller_failure_trace.json`](examples/controller_failure_trace.json) is the record of the run in `docs/media/hero-demo.*`.

## Screenshots

`scripts/ui_hero_demo.py --runs 1 --shots docs/screenshots/hero` saves the key frames (ready, fault, approval, resolved);
`scripts/ui_states.py` captures every dashboard state including the timeout and inconclusive ones (`docs/screenshots/states/`).
