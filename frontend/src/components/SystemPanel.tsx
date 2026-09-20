import { useEffect, useState } from 'react'
import { api, type Graph, type Health, type Readiness } from '../api'
import { startBlockers } from '../lib/uiState'

const COMPONENTS: [string, string][] = [
  ['controller', 'Controller'], ['lidar', 'LiDAR'], ['odometry', 'Odometry'], ['tf', 'TF'], ['navigation', 'Obstacle detection'], ['ros_graph', 'ROS graph'],
]
const FAULTS: [string, string][] = [
  ['controller_crash', 'Controller crash'], ['lidar_failure', 'LiDAR failure'], ['tf_failure', 'TF failure'],
  ['topic_misconfig', 'Topic mismatch'], ['node_crash', 'Node crash'],
]

export function SystemPanel({ health, graph, readiness, preparing, running, locked, connected }: {
  health: Health | null; graph: Graph | null; readiness: Readiness | null; preparing: string | null; running: boolean; locked: boolean; connected: boolean
}) {
  const nodes = graph?.nodes.filter((n) => n.alive).length ?? 0
  const expected = graph?.nodes.filter((n) => n.expected).length ?? 0
  const connections = graph?.edges.filter((e) => e.live).length ?? 0
  return (
    <aside className="sysp">
      <section>
        <div className="pane-head"><h2>System</h2></div>
        <ul className="comps" data-testid="components">
          {COMPONENTS.map(([k, label]) => {
            const c = health?.components?.[k] ?? { state: 'UNKNOWN', detail: 'no data' }
            return (
              <li key={k} className={`c-${c.state}`} data-testid={`component-${k}`} data-status={c.state}>
                <span className="c-line"><i className="dot" /><span className="c-name">{label}</span><span className="c-state">{c.state.toLowerCase()}</span></span>
                {c.state !== 'HEALTHY' && <span className="c-detail mono" title={c.detail}>{c.detail}</span>}
              </li>
            )
          })}
        </ul>
      </section>
      <section>
        <div className="pane-head"><h2>ROS graph</h2></div>
        <div className="counts mono"><span>{nodes}/{expected} nodes</span><span>{graph?.topics.length ?? 0} topics</span><span>{connections} connections</span><span>{graph?.tf?.length ?? 0} transforms</span></div>
      </section>
      <section className="topics-sec">
        <div className="pane-head"><h2>Topics</h2></div>
        <ul className="topics">
          {(graph?.topics ?? []).map((t) => {
            const low = t.expected && t.rate_hz !== null && t.min_rate_hz !== null && t.rate_hz < t.min_rate_hz
            return (
              <li key={t.id} className={low || !t.expected ? 'bad' : ''}>
                <span className="mono t-name">{t.label}</span>
                <span className="mono t-rate">{t.rate_hz !== null ? `${t.rate_hz.toFixed(1)} Hz` : '-'}</span>
                <span className="mono t-io" title={`${t.publishers} publisher(s), ${t.subscribers} subscriber(s)`}>{t.publishers}→{t.subscribers}</span>
              </li>
            )
          })}
        </ul>
      </section>
      <DemoControls readiness={readiness} preparing={preparing} running={running} locked={locked} connected={connected} />
    </aside>
  )
}

function DemoControls({ readiness, preparing, running, locked, connected }: {
  readiness: Readiness | null; preparing: string | null; running: boolean; locked: boolean; connected: boolean
}) {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('robotops.demo') !== '0' } catch { return true } })
  const [msg, setMsg] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  useEffect(() => { try { localStorage.setItem('robotops.demo', open ? '1' : '0') } catch { /* private mode */ } }, [open])
  const run = async (path: string, body: unknown, ok: string) => {
    setWorking(true); setMsg(null)
    try { await api(path, body); setMsg(ok) } catch (e) { setMsg((e as Error).message) }
    setWorking(false)
  }
  const disabled = working || running || locked
  const canStart = !!readiness && startBlockers(readiness).length === 0 && !running && !preparing && connected && !working
  return (
    <section className="demo">
      <button className="pane-head toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <h2>Demo controls</h2><span className="muted">{open ? 'hide' : 'show'}</span>
      </button>
      {open && (
        <div className="demo-body">
          <div className="row2">
            <button className="btn" data-testid="start-demo" disabled={!canStart} onClick={() => run('/api/demo/prepare', {}, '')}
              title="Reset the robot, warm the model and verify everything">{preparing || working ? 'Preparing…' : 'Start demo'}</button>
            <button className="btn" data-testid="reset-robot" disabled={disabled} onClick={() => run('/api/demo/reset', {}, 'robot reset')}>Reset robot</button>
          </div>
          <div className="faults">
            {FAULTS.map(([id, label]) => (
              <button key={id} className="btn ghost" data-testid={`inject-${id}`} disabled={disabled} onClick={() => run('/api/faults/inject', { fault: id }, `injected: ${label.toLowerCase()}`)}>{label}</button>
            ))}
            <button className="btn ghost" data-testid="inject-random" disabled={disabled}
              onClick={() => run('/api/faults/inject', { fault: 'random' }, 'injected a random fault (identity hidden)')}>Inject random fault</button>
          </div>
          {msg && <div className="demo-msg mono">{msg}</div>}
        </div>
      )}
    </section>
  )
}
