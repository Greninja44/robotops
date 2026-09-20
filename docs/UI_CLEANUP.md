# UI cleanup plan (frontend only)

Scope: presentation only. Backend, agent, APIs, safety, verification, benchmarks and fault injection are untouched; `frontend/src/api.ts`
(data layer, WebSocket handling) is unchanged.

## Audit of the previous UI (screenshots of all 9 states were captured first)
| finding | why it matters |
|---|---|
| page overflowed at 1440x900; approval buttons below the fold | a judge must see "needs approval" without scrolling |
| three stacked "hero" layers (brand header, READY banner, health cards) before any content | status is shouted three times, the graph gets ~40 % of the height |
| root cause / proposed repair / verification as three equal cards under the graph, plus an INCIDENT RESOLVED card and a RECOVERY VERIFIED badge | "recovered" announced three times; decision content competes with the log |
| timeline as numbered cards with a repeated italic model sentence and stale evidence alerts | hard to scan, not an event stream |
| glows, gradients, animated edges, pill chips, 6+ accent colours, emoji-era styling, marketing copy | reads as a template, not tooling |
| graph relied on React Flow measuring nodes and fitting once on init | could render with a single node visible |

## Decisions
- Layout: header (40 px) / status bar (30 px) / three columns (System 260 | ROS graph fluid | Investigation 420) / composer. No page scroll; every column scrolls internally.
- Header: name, `ROS 2 / demo_robot`, model, node/topic counts, system state. No tagline.
- Status bar: one line with the current state (`data-state` attribute for tests): ready, preparing, fault, investigating, awaiting approval, repairing, verifying, recovered, stopped, inconclusive; readiness (ROS, Agent, Ollama, Model, DDS) as small dots.
- System column: component list (status dot + word), graph counts, topics with live rates, collapsible DEMO CONTROLS (start demo, reset, fault injection).
- Graph: compact rectangular nodes, orthogonal edges, explicit node sizes (always rendered), refit on structure/size change; suspected = accent/amber outline, confirmed failed = failure colour, recovered = normal. No glow, no animated edges.
- Investigation: timestamped event stream (tool name in monospace, real values), then root cause, proposed action (Reject / Approve restart), verification with the incident summary as a definition list - pinned so approval is always visible.
- Visual system: near-black neutral, 1 px borders, 4 px radius, one accent, status colours only for status, system sans + monospace for ROS names/values/IDs/times, no gradients, animation only for state (new entry fade, spinner, colour transition).
- Copy: technical nouns/verbs ("Investigating", "Controller unavailable", "Recovery verified"); no hype.

## Preserved (checked against the list of states)
fault injection, random fault, agent event stream, evidence, approval/rejection, ROS graph, health status, verification, incident summary, preflight
readiness + START DEMO, model timeout / inconclusive / stopped states, WebSocket live updates and reconnect.

## Result (measured)
| check | result |
|---|---|
| backend / ROS / agent / API / safety / verification files changed | **0** (`git status --short backend` empty; `api.ts` unchanged) |
| frontend `tsc`, `oxlint`, `vite build` | clean |
| fast tests (`pytest -m "not ros"`) | 124 pass |
| page overflow | 0 px at 1440x900 and 1366x768 (measured with Playwright); each column scrolls internally |
| hero scenario through the new UI, real clicks | **10/10 consecutive** (`benchmarks/hero/hero_newui_*.json`), diagnosis 3.9-13.6 s, 24/24 verification every run |
| random faults through the new UI | 5/5 correct, repaired and verified (`benchmarks/random/random_newui_*.json`); one diagnosis took 29.0 s while the machine was busy |
| timeout / stall check (fake stalling model server) | pass: retry shown, retried-and-continued shown, safe stop shown, nothing repaired |
| all states captured | `docs/screenshots/states/` (ready, fault, investigating, awaiting approval, repairing/verifying, recovered, timeout retrying, timeout stopped, inconclusive) |

## Problems found while verifying, and fixed
1. **Start demo was disabled exactly when it was needed.** After a long idle (Ollama had unloaded the model, the backend had lost discovery of the robot) the backend
   reported "not ready", and the UI disabled Start demo - the action that warms the model and resets the robot. The UI now enables it unless something it cannot fix is
   failing (Ollama down, no ROS environment, supervisor unreachable), and the status bar says "Start demo will recover this". Frontend-only fix; verified by a hero run that
   started from exactly that degraded state.
2. The verification result was pushed off screen by the already-decided root cause and proposal: those now collapse to one line each after the decision.
3. Repeated "evidence check" rows collapsed to one row with a count; stopped/inconclusive investigations get an explicit closing entry; text truncates at word boundaries.
4. Graph: nodes have explicit sizes (cannot be hidden by a measurement race) and the view refits when the node set or the panel size changes.

## Removed as "AI look" (and why)
gradient backgrounds and glows, pill chips (5 readiness chips + 4 header pills), giant status text (34 px banner, 28 px health words), animated edges and pulsing,
"AFFECTED" region overlay, the duplicated "recovered" announcements (badge + banner + card), numbered stage cards, italic repeated model sentences,
marketing copy ("AI INVESTIGATION", "Evidence (from ROS tools)"), a purple "random" button, decorative status colours.

## Kept because an engineer needs it
per-component health with the failing detail, live topic rates and pub/sub counts, the graph with failed/inspecting/involved states, timestamped tool calls with real values,
evidence IDs with the tool that produced them, the heuristic evidence score with its basis, risk and expected result before approval, the 24-check verification with a measured
recovery time, model timeout / retry state, and readiness (ROS, Agent, Ollama, Model, DDS).
