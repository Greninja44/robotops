import { useMemo } from 'react'
import { api, useRobotOps, type Investigation } from './api'
import { GraphPanel, type Marks } from './components/GraphPanel'
import { Timeline } from './components/Timeline'
import { ReadyBanner } from './components/Ready'
import { Incident } from './components/Incident'
import { AskPanel, FaultPanel, HealthBar, RepairPanel, RootCause, StatusPills, VerificationPanel } from './components/Panels'

const TERMINAL = ['resolved', 'repair_failed', 'rejected', 'inconclusive', 'healthy', 'diagnosed', 'error']

function marksFor(inv: Investigation | null): Marks {
  const m: Marks = { evidence: new Set(), probing: new Set() }
  if (!inv) return m
  const calls = inv.events.filter((e) => e.kind === 'tool_call')
  const results = new Set(inv.events.filter((e) => e.kind === 'tool_result').map((e) => e.step))
  const pending = calls.find((c) => !results.has(c.step))
  if (pending) {
    const a = pending.args ?? {}
    for (const v of [a.topic, a.node]) if (typeof v === 'string') m.probing.add(v.startsWith('/') ? v : `/${v}`)
    if (a.parent_frame && a.child_frame) m.probing.add(`tf:${a.parent_frame}->${a.child_frame}`)
    if (pending.tool === 'check_tf' && !a.parent_frame) { m.probing.add('tf:odom->base_link'); m.probing.add('tf:base_link->laser') }
  }
  const d = inv.diagnosis
  if (d && d.faulty_component !== 'none' && inv.phase !== 'resolved') {
    m.rootCause = `/${d.faulty_component}`
    for (const e of d.evidence) for (const s of e.subjects) m.evidence.add(s)
  }
  return m
}

export default function App() {
  const { health, graph, inv, connected, status, lastFault, readiness, preparing } = useRobotOps()
  const running = !!inv && !TERMINAL.includes(inv.phase)
  const locked = !readiness?.infra_ready || !!preparing      // model/ROS not ready: no demo controls
  const busy = running || locked
  const marks = useMemo(() => marksFor(inv), [inv])
  const ask = async (q: string) => { await api('/api/investigations', { query: q }) }

  return (
    <div className="app">
      <header>
        <div className="brand">
          <img src="/favicon.svg" alt="" />
          <div>
            <h1>RobotOps</h1>
            <p>Autonomous AI Reliability Engineer for ROS 2</p>
          </div>
        </div>
        <StatusPills status={status} connected={connected} />
      </header>

      {!connected && <div className="banner">Backend disconnected — reconnecting…</div>}
      <ReadyBanner readiness={readiness} preparing={preparing} health={health} inv={inv} connected={connected} />
      <HealthBar health={health} />

      <main>
        <div className="col-left">
          <GraphPanel graph={graph} marks={marks} recovered={inv?.phase === 'resolved'} />
          <Incident inv={inv} />
          <div className="cards">
            <RootCause inv={inv} />
            <RepairPanel inv={inv} />
            <VerificationPanel inv={inv} />
          </div>
        </div>
        <div className="col-right">
          <section className="panel timeline-panel">
            <div className="panel-head">
              <h2>AI Investigation</h2>
              {inv && <span className="muted small mono">{inv.tool_calls} tool calls · {inv.phase.replace('_', ' ')}{running ? '' : inv.diagnosis_seconds ? ` · diagnosed in ${inv.diagnosis_seconds}s` : ''}</span>}
            </div>
            <AskPanel busy={busy} onAsk={ask} />
            <Timeline inv={inv} />
          </section>
          <FaultPanel busy={busy} lastFault={lastFault} />
        </div>
      </main>
    </div>
  )
}
