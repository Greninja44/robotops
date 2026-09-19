import { useState } from 'react'
import { api, type Health, type Investigation, type Status } from '../api'

const COMPONENT_LABEL: Record<string, string> = {
  controller: 'Controller', lidar: 'LiDAR', odometry: 'Odometry', tf: 'TF', navigation: 'Obstacle Detection', ros_graph: 'ROS Graph',
}

export function HealthBar({ health }: { health: Health | null }) {
  const comps = health?.components ?? {}
  return (
    <section className="healthbar">
      <div className={`overall st-${health?.overall ?? 'UNKNOWN'}`}>
        <span className="overall-label">System health</span>
        <span className="overall-value">{health?.overall ?? 'UNKNOWN'}</span>
      </div>
      {Object.keys(COMPONENT_LABEL).map((k) => {
        const c = comps[k] ?? { state: 'UNKNOWN', detail: 'no data' }
        return (
          <div key={k} className={`hcard st-${c.state}`} title={c.detail}>
            <div className="hcard-top"><span className="led" />{COMPONENT_LABEL[k]}</div>
            <div className="hcard-state">{c.state}</div>
            <div className="hcard-detail">{c.detail}</div>
          </div>
        )
      })}
    </section>
  )
}

export function StatusPills({ status, connected }: { status: Status | null; connected: boolean }) {
  const pill = (ok: boolean | undefined, label: string, title?: string) =>
    <span className={`pill ${ok ? 'ok' : 'bad'}`} title={title}><i />{label}</span>
  return (
    <div className="pills">
      {pill(connected, 'Backend')}
      {pill(status?.ros.available, 'ROS 2')}
      {pill(status?.supervisor.reachable, 'Demo robot')}
      {pill(status?.llm.model_available, status?.llm.model ?? 'LLM', status?.llm.reachable ? 'Ollama reachable' : 'Ollama unreachable')}
    </div>
  )
}

const EXAMPLES = [
  'Diagnose the robot.',
  "Why isn't my robot moving?",
  'Why is obstacle detection not working?',
  'Find the failure and recover the system.',
]

export function AskPanel({ busy, onAsk }: { busy: boolean; onAsk: (q: string) => Promise<void> }) {
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const submit = async (text: string) => {
    if (!text.trim() || busy) return
    setErr(null)
    try { await onAsk(text.trim()); setQ('') } catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="ask">
      <form onSubmit={(e) => { e.preventDefault(); submit(q) }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} disabled={busy}
          placeholder={busy ? 'Investigation in progress…' : 'Describe the problem, e.g. “My robot stopped moving”'} />
        <button type="submit" disabled={busy || !q.trim()}>Investigate</button>
      </form>
      <div className="chips">
        {EXAMPLES.map((e) => <button key={e} disabled={busy} onClick={() => submit(e)}>{e}</button>)}
      </div>
      {err && <div className="form-err">{err}</div>}
    </div>
  )
}

export function RootCause({ inv }: { inv: Investigation | null }) {
  const d = inv?.diagnosis
  const [showBasis, setShowBasis] = useState(false)
  return (
    <section className="panel card">
      <div className="panel-head"><h2>Root Cause</h2></div>
      {!d ? (
        <div className="placeholder">{inv && !['inconclusive', 'error'].includes(inv.phase) ? 'Gathering evidence…'
          : inv?.phase === 'inconclusive' ? 'Evidence was insufficient — RobotOps will not act on a guess.'
            : inv?.phase === 'error' ? inv.error : 'No diagnosis yet.'}</div>
      ) : (
        <>
          <p className="root-text">{d.root_cause}</p>
          <div className="kv"><span>Faulty component</span><b className="mono">{d.faulty_component}</b></div>
          <div className="evidence-list">
            <div className="sub-h">Evidence (from ROS tools)</div>
            <ul>
              {d.evidence.map((e) => (
                <li key={e.id} className={e.anomaly ? 'anom' : ''}>
                  <span className="eid mono">{e.id}</span>
                  <span>{e.text}</span>
                  <span className="src mono">{e.source}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="score">
            <div className="score-top">
              <span>Evidence score <em>(heuristic)</em></span>
              <b>{d.confidence.toFixed(2)}</b>
            </div>
            <div className="bar"><div style={{ width: `${d.confidence * 100}%` }} /></div>
            <button className="linkish" onClick={() => setShowBasis(!showBasis)}>{showBasis ? 'hide' : 'how is this computed?'}</button>
            {showBasis && <ul className="basis">{d.confidence_basis.map((b, i) => <li key={i}>{b}</li>)}</ul>}
          </div>
        </>
      )}
    </section>
  )
}

export function RepairPanel({ inv }: { inv: Investigation | null }) {
  const p = inv?.proposal
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const decide = async (approve: boolean) => {
    if (!inv || !p) return
    setBusy(true); setErr(null)
    try {
      await api(`/api/investigations/${inv.id}/${approve ? 'approve' : 'reject'}`, { proposal_id: p.id, operator: 'dashboard operator' })
    } catch (e) { setErr((e as Error).message) }
    setBusy(false)
  }
  const pending = inv?.phase === 'awaiting_approval' && p?.state === 'pending'
  return (
    <section className={`panel card ${pending ? 'attention' : ''}`}>
      <div className="panel-head"><h2>Proposed Repair</h2>{p && <span className={`risk risk-${p.risk}`}>Risk: {p.risk}</span>}</div>
      {!p ? <div className="placeholder">A repair is proposed only when the evidence supports it.</div> : (
        <>
          <p className="repair-action"><span className="mono">{p.action}</span> → <b className="mono">{p.target}</b></p>
          <div className="kv"><span>Reason</span><span>{p.reason}</span></div>
          <div className="kv"><span>Expected result</span><span>{p.expected_result}</span></div>
          <div className="kv"><span>Evidence</span><span>{p.evidence.map((e) => e.split(':')[0]).join(', ')}</span></div>
          {pending ? (
            <div className="approve-row">
              <button className="btn-reject" disabled={busy} onClick={() => decide(false)}>REJECT</button>
              <button className="btn-approve" disabled={busy} onClick={() => decide(true)}>APPROVE</button>
            </div>
          ) : (
            <div className={`decision ${p.state}`}>{p.state === 'pending' ? 'waiting…' : `${p.state.toUpperCase()}${p.decided_by ? ` by ${p.decided_by}` : ''}`}</div>
          )}
          {err && <div className="form-err">{err}</div>}
        </>
      )}
    </section>
  )
}

export function VerificationPanel({ inv }: { inv: Investigation | null }) {
  const v = inv?.verification
  const verifying = inv?.phase === 'verifying'
  const lastAttempt = [...(inv?.events ?? [])].reverse().find((e) => e.kind === 'verification_attempt')
  return (
    <section className="panel card">
      <div className="panel-head"><h2>Verification</h2></div>
      {!v && !verifying && <div className="placeholder">After a repair, RobotOps re-measures the live system. A zero exit code is not proof of recovery.</div>}
      {verifying && !v && (
        <div className="placeholder"><span className="spinner" /> Re-measuring nodes, topic rates, TF and odometry…
          {lastAttempt && <div className="muted">pass {lastAttempt.attempt}: {lastAttempt.passed}/{lastAttempt.total}</div>}</div>
      )}
      {v && (
        <>
          <ul className="checks">
            {pickChecks(v.checks).map((c) => (
              <li key={c.check} className={c.passed ? 'pass' : 'fail'}>
                <span>{c.passed ? '✓' : '✕'}</span>{c.check}<em>{c.detail}</em>
              </li>
            ))}
          </ul>
          <div className="muted small">{v.checks.filter((c) => c.passed).length}/{v.checks.length} checks passed · {v.attempts} pass(es)</div>
          <div className={`verdict ${v.verified ? 'good' : 'bad'}`}>{v.verified ? 'RECOVERY VERIFIED' : inv?.phase === 'investigating' ? 'VERIFICATION FAILED — re-investigating' : 'REPAIR FAILED'}</div>
        </>
      )}
    </section>
  )
}

function pickChecks(checks: { check: string; passed: boolean; detail: string; scope: string }[]) {
  const failed = checks.filter((c) => !c.passed)
  const target = checks.filter((c) => c.passed && c.scope === 'target')
  const motion = checks.filter((c) => c.passed && c.check.startsWith('odometry'))
  const seen = new Set<string>()
  return [...failed, ...target, ...motion].filter((c) => !seen.has(c.check) && seen.add(c.check)).slice(0, 7)
}

const FAULTS: [string, string][] = [
  ['controller_crash', 'Controller Failure'],
  ['lidar_failure', 'LiDAR Failure'],
  ['tf_failure', 'TF Failure'],
  ['topic_misconfig', 'Topic Failure'],
  ['node_crash', 'Node Crash'],
]

export function FaultPanel({ busy, lastFault }: { busy: boolean; lastFault: { fault: string; ts: number } | null }) {
  const [msg, setMsg] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  const run = async (path: string, body?: unknown, label?: string) => {
    setWorking(true); setMsg(null)
    try { await api(path, body ?? {}); setMsg(label ?? 'done') } catch (e) { setMsg((e as Error).message) }
    setWorking(false)
  }
  return (
    <section className="panel faults">
      <div className="panel-head"><h2>Fault Injection</h2><span className="muted small">demo only</span></div>
      <div className="fault-grid">
        {FAULTS.map(([id, label]) => (
          <button key={id} disabled={working || busy} onClick={() => run('/api/faults/inject', { fault: id }, `Injected: ${label}`)}>{label}</button>
        ))}
        <button className="random" disabled={working || busy} onClick={() => run('/api/faults/inject', { fault: 'random' }, 'Injected a random fault — its identity is hidden from you and from the agent')}>🎲 Random Failure</button>
      </div>
      <div className="fault-foot">
        <button className="reset" disabled={working || busy} onClick={() => run('/api/demo/reset', {}, 'Robot reset to healthy configuration')}>↺ Reset robot</button>
        <span className="muted small">{msg ?? (lastFault ? `Last injected: ${lastFault.fault}` : '')}</span>
      </div>
    </section>
  )
}
