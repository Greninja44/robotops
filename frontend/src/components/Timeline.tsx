import { useEffect, useRef } from 'react'
import type { InvEvent, Investigation } from '../api'

const TOOL_LABEL: Record<string, string> = {
  get_ros_health: 'Inspecting ROS system health',
  list_nodes: 'Listing ROS nodes',
  list_topics: 'Listing ROS topics',
  inspect_node: 'Inspecting node',
  inspect_topic: 'Checking topic',
  measure_topic_rate: 'Measuring topic rate',
  check_tf: 'Checking TF transforms',
  inspect_parameters: 'Reading parameters of',
  get_recent_diagnostics: 'Reading /diagnostics',
  get_recent_logs: 'Reading recent /rosout logs',
  get_component_status: 'Checking process manager',
}

function argText(args: Record<string, unknown>) {
  const v = Object.entries(args ?? {}).filter(([k]) => k !== 'duration' && k !== 'seconds').map(([, v]) => v)
  return v.filter(Boolean).join(' → ')
}

type Item =
  | { type: 'step'; call: InvEvent; result?: InvEvent }
  | { type: 'event'; ev: InvEvent }

function group(events: InvEvent[]): Item[] {
  const items: Item[] = []
  const byStep = new Map<number, Extract<Item, { type: 'step' }>>()
  for (const ev of events) {
    if (ev.kind === 'tool_call') {
      const it = { type: 'step' as const, call: ev }
      byStep.set(ev.step, it)
      items.push(it)
    } else if (ev.kind === 'tool_result') {
      const it = byStep.get(ev.step)
      if (it) it.result = ev
    } else {
      items.push({ type: 'event', ev })
    }
  }
  return items
}

const PHASE_TEXT: Record<string, string> = {
  investigating: 'Investigating — the model chooses the next diagnostic tool',
  diagnosing: 'Diagnosis submitted — validating against the evidence ledger',
  awaiting_approval: 'Repair proposed — waiting for human approval',
  repairing: 'Approved — executing repair',
  verifying: 'Verifying recovery independently',
  resolved: 'RECOVERY VERIFIED',
  repair_failed: 'REPAIR FAILED — stopped safely',
  rejected: 'Repair not approved — no action taken',
  inconclusive: 'Inconclusive — insufficient evidence, no repair attempted',
  healthy: 'No fault found',
  diagnosed: 'Diagnosed — no repair recommended',
  error: 'Stopped with an error',
}

export function Timeline({ inv }: { inv: Investigation | null }) {
  const end = useRef<HTMLDivElement>(null)
  const n = inv?.events.length ?? 0
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [n])

  if (!inv) {
    return <div className="timeline empty">No investigation yet. Inject a fault, then ask RobotOps what is wrong.</div>
  }
  const running = !['resolved', 'repair_failed', 'rejected', 'inconclusive', 'healthy', 'diagnosed', 'error'].includes(inv.phase)
  const items = group(inv.events)
  return (
    <div className="timeline">
      <div className="tl-query">“{inv.query}”</div>
      {items.map((it, i) => it.type === 'step' ? <Step key={i} call={it.call} result={it.result} /> : <EventRow key={i} ev={it.ev} />)}
      {running && inv.phase === 'investigating' && !items.some((it) => it.type === 'step' && !it.result) && (
        <div className="tl-row thinking"><span className="dot pulse" />Model is choosing the next check…</div>
      )}
      <div ref={end} />
    </div>
  )
}

function Step({ call, result }: { call: InvEvent; result?: InvEvent }) {
  const failed = result && !result.success
  const anomalies = result?.evidence?.filter((e: { anomaly: boolean }) => e.anomaly).length ?? 0
  return (
    <div className={`tl-step ${!result ? 'pending' : failed ? 'failed' : anomalies ? 'anomalous' : 'clean'}`}>
      <div className="tl-step-head">
        <span className="tl-icon">{!result ? <span className="spinner" /> : failed ? '✕' : '✓'}</span>
        <span className="tl-title">{TOOL_LABEL[call.tool] ?? call.tool} <b>{argText(call.args)}</b></span>
        <span className="tl-tool mono">{call.tool}{result ? ` · ${result.duration_ms} ms` : ''}</span>
      </div>
      {call.reason && <div className="tl-reason">Hypothesis: {call.reason}</div>}
      {failed && <div className="tl-err">{result.error}</div>}
      {result && result.evidence?.length > 0 && (
        <ul className="tl-evidence">
          {result.evidence.map((e: { id: string; text: string; anomaly: boolean }) => (
            <li key={e.id} className={e.anomaly ? 'anom' : ''}>
              <span className="eid mono">{e.id}</span>{e.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EventRow({ ev }: { ev: InvEvent }) {
  switch (ev.kind) {
    case 'phase': {
      const t = PHASE_TEXT[ev.phase]
      if (!t || ev.phase === 'investigating' && ev.previous === 'observing') return null
      const cls = ev.phase === 'resolved' ? 'good' : ['repair_failed', 'error', 'inconclusive'].includes(ev.phase) ? 'bad' : ''
      return <div className={`tl-phase ${cls}`}>{t}{ev.reason && ev.phase !== 'resolved' ? ` · ${ev.reason}` : ''}</div>
    }
    case 'note':
      return <div className="tl-row note">● {ev.text}</div>
    case 'warning':
      return <div className="tl-row warn">⚠ {ev.text}</div>
    case 'error':
      return <div className="tl-row bad">✕ {ev.message}</div>
    case 'diagnosis_rejected':
      return (
        <div className="tl-rejected">
          <b>Evidence validator rejected the diagnosis</b>
          <ul>{ev.errors.map((e: string, i: number) => <li key={i}>{e}</li>)}</ul>
          <span className="muted">The agent was told why and continues investigating.</span>
        </div>
      )
    case 'diagnosis':
      return <div className="tl-row good">✓ Root cause identified: <b>{ev.diagnosis.faulty_component}</b> (evidence score {ev.diagnosis.confidence.toFixed(2)})</div>
    case 'approval':
      return <div className={`tl-row ${ev.state === 'approved' ? 'good' : 'bad'}`}>{ev.state === 'approved' ? '✓' : '✕'} Proposal {ev.state} by {ev.decided_by ?? 'nobody'}</div>
    case 'repair_started':
      return <div className="tl-row note"><span className="spinner" /> Executing {ev.action} on <b>{ev.target}</b></div>
    case 'repair_result':
      return <div className={`tl-row ${ev.executed ? 'good' : 'bad'}`}>{ev.executed ? '✓ Process manager restarted the component' : `✕ Repair not executed: ${ev.error}`} — exit code alone is not success; verifying…</div>
    case 'verification_attempt':
      return <div className={`tl-row ${ev.failed.length ? 'warn' : 'good'}`}>Verification pass {ev.attempt}: {ev.passed}/{ev.total} checks passed{ev.failed.length ? ` — waiting on ${ev.failed.slice(0, 2).join('; ')}` : ''}</div>
    default:
      return null
  }
}
