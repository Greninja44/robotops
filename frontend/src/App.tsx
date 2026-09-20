import { useMemo } from 'react'
import { api, useRobotOps, type Investigation as Inv } from './api'
import { GraphPanel, type Marks } from './components/GraphPanel'
import { Header, StatusBar } from './components/Header'
import { SystemPanel } from './components/SystemPanel'
import { Investigation } from './components/Investigation'
import { Composer } from './components/Composer'
import { TERMINAL, deriveState } from './lib/uiState'

/** Which graph elements to emphasise: what the agent is inspecting now, what the diagnosis cites, and the confirmed root cause. */
function marksFor(inv: Inv | null): Marks {
  const m: Marks = { evidence: new Set(), probing: new Set() }
  if (!inv) return m
  const done = new Set(inv.events.filter((e) => e.kind === 'tool_result').map((e) => e.step))
  const pending = inv.events.find((e) => e.kind === 'tool_call' && !done.has(e.step))
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
  const { health, graph, inv, connected, status, readiness, preparing } = useRobotOps()
  const running = !!inv && !TERMINAL.includes(inv.phase)
  const locked = !readiness?.infra_ready || !!preparing            // model / ROS not ready: no demo controls
  const state = useMemo(() => deriveState({ connected, readiness, preparing, health, inv }), [connected, readiness, preparing, health, inv])
  const marks = useMemo(() => marksFor(inv), [inv])
  const ask = async (q: string) => { await api('/api/investigations', { query: q }) }

  return (
    <div className="app">
      <Header health={health} graph={graph} status={status} />
      <StatusBar state={state} readiness={readiness} />
      <main className="body">
        <SystemPanel health={health} graph={graph} readiness={readiness} preparing={preparing} running={running} locked={locked} connected={connected} />
        <GraphPanel graph={graph} marks={marks} />
        <Investigation inv={inv} running={running} />
      </main>
      <Composer busy={running || locked} reason={locked ? 'Waiting for the system to be ready…' : undefined} onAsk={ask} />
    </div>
  )
}
