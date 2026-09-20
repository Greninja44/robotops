# GitHub submission notes

Nothing on this page has been applied automatically: repository settings, visibility and licence are the owner's decisions.

## Suggested repository description

> Evidence-driven AI reliability engineer for ROS 2 — diagnose, repair and verify robot failures.

## Suggested topics

`ros2` · `robotics` · `ai-agents` · `llm` · `observability` · `fastapi` · `react` · `ollama` · `robotics-ai` · `root-cause-analysis`

(Only things the project actually is: a ROS 2 tool, an LLM agent running on Ollama, a FastAPI + React application, and a root-cause-analysis / observability tool for robots.)

Apply with the GitHub CLI if you agree:

```bash
gh repo edit Greninja44/robotops \
  --description "Evidence-driven AI reliability engineer for ROS 2 — diagnose, repair and verify robot failures." \
  --add-topic ros2 --add-topic robotics --add-topic ai-agents --add-topic llm --add-topic observability \
  --add-topic fastapi --add-topic react --add-topic ollama --add-topic robotics-ai --add-topic root-cause-analysis
```

## Before submitting: things only the owner can decide

1. **Licence.** The repository currently has **no `LICENSE` file**. None was created, because choosing licence terms is a legal decision that belongs to the author. Without a licence, the code is "all rights reserved" by default: others may view it if it is public, but have no right to use, copy or modify it. Add the licence of your choice (for example via GitHub → *Add file* → *Create new file* → `LICENSE`, which offers templates), or confirm that the hackathon's rules make one unnecessary.
2. **Visibility / access.** The repository is currently **private**. Judges evaluate the repository, so either make it public or invite them as collaborators (or add whichever account the hackathon designates). RobotOps did not change the visibility.
3. **Demo video (optional).** The README embeds a GIF (`docs/media/hero-demo.gif`); the full-quality MP4 is `docs/media/hero-demo.mp4`. If the hackathon form asks for a video link, upload that MP4 (or record your own with [RECORDING.md](RECORDING.md)) and paste the link there. A longer narrated video is not part of the repository.
4. **Author identity.** Commits carry the author name and GitHub no-reply address configured in your local git. Check that this is what you want to publish.
5. **History.** The full development history is kept (31 commits over two days, merged through 6 pull requests). Nothing was rewritten or back-dated.

## Pre-submission checklist

- [ ] `LICENSE` added (or decision recorded)
- [ ] Repository public, or judges invited
- [ ] Description and topics set
- [ ] README first screen looks right on github.com (GIF plays, Mermaid diagram renders, screenshots load)
- [ ] `./demo_preflight.sh` prints `ROBOTOPS DEMO READY` on the machine you will show it on
- [ ] Fast tests pass: `.venv/bin/python -m pytest tests -m "not ros"`

## Clean-clone check

Performed on 2026-09-20 from a fresh `git clone` of the submission branch into an empty directory, using only the README's instructions:

| Step | Result |
|---|---|
| `./scripts/setup.sh` | 15 s: prerequisites found, `.venv` created (sees system `rclpy`), Python and frontend dependencies installed, `qwen3:4b` found |
| `./run_demo.sh` | 25 s from a cold start of the stack (Ollama and the model were already resident): model warm-up, robot, dashboard build, backend, preflight |
| `./demo_preflight.sh` | `ROBOTOPS DEMO READY` (a warning that an unrelated CPU-heavy job was running on the machine was reported, as designed) |
| `.venv/bin/python -m pytest tests -m "not ros"` | 142 passed |
| `npx tsc -b`, `npm run lint`, `npm run build` (frontend) | all clean |
| `./scripts/verify_demo.sh --full` | PASS, 23 checks, including a complete LLM investigation → approval → repair → independent verification |

Not re-verified from a clean machine: installing ROS 2 Lyrical and Ollama themselves (prerequisites, documented in [SETUP.md](SETUP.md)), and the 19 live-ROS tests (unchanged code; run earlier on the same commit lineage).
