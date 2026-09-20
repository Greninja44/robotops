import { useEffect, useRef } from 'react'
import type { InvEvent, Investigation } from '../api'

type Chip = { text: string; tone?: 'ok' | 'bad' | 'warn' }

const TITLE: Record<string, (a: Record<string, string>) => string> = {
  get_ros_health: () => 'Inspect ROS graph',
  list_nodes: () => 'List ROS nodes',
  list_topics: () => 'List topics',
  inspect_node: (a) => `Inspect ${a.node ?? 'node'}`,
  inspect_topic: (a) => `Inspect ${a.topic ?? 'topic'}`,
  measure_topic_rate: (a) => `Measure ${a.topic ?? 'topic'} rate`,
  check_tf: (a) => (a.parent_frame ? `Check TF ${a.parent_frame}→${a.child_frame}` : 'Check TF transforms'),
  inspect_parameters: (a) => `Read ${a.node ?? 'node'} parameters`,
  get_recent_diagnostics: () => 'Read /diagnostics',
  get_recent_logs: (a) => (a.node ? `Read ${a.node} logs` : 'Read recent logs'),
  get_component_status: () => 'Check process status',
}

/** Real values from the tool's structured result, shown as compact chips (no invented numbers). */
function liveValues(tool: string, args: Record<string, string>, d: Record<string, any>): Chip[] { // eslint-disable-line @typescript-eslint/no-explicit-any
  try {
    switch (tool) {
      case 'get_ros_health': {
        const miss: string[] = d.expected_nodes_missing ?? []
        return [{ text: `${d.node_count} nodes · ${d.topic_count} topics` },
          ...(miss.length ? [{ text: `missing ${miss.join(', ')}`, tone: 'bad' as const }] : [{ text: 'all expected nodes present', tone: 'ok' as const }])]
      }
      case 'list_nodes': {
        const miss: string[] = d.expected_missing ?? []
        return [{ text: `${d.count} nodes running` }, ...(miss.length ? [{ text: `${miss.join(', ')} missing`, tone: 'bad' as const }] : [])]
      }
      case 'list_topics':
        return [{ text: `${Object.keys(d.topics ?? {}).length} active topics` }]
      case 'inspect_topic': {
        if (d.exists === false) return [{ text: `${args.topic} does not exist`, tone: 'bad' }]
        return [{ text: `${d.publisher_count} publisher${d.publisher_count === 1 ? '' : 's'}`, tone: d.publisher_count ? 'ok' : 'bad' },
          { text: `${d.subscriber_count} subscriber${d.subscriber_count === 1 ? '' : 's'}`, tone: d.subscriber_count ? 'ok' : 'bad' }]
      }
      case 'measure_topic_rate':
        return [{ text: `${Number(d.rate_hz).toFixed(1)} Hz`, tone: d.rate_hz >= (d.expected_min_hz ?? 0) && d.rate_hz > 0 ? 'ok' : 'bad' },
          ...(d.expected_min_hz ? [{ text: `min ${d.expected_min_hz} Hz` }] : [])]
      case 'inspect_node':
        return d.exists === false ? [{ text: 'NOT RUNNING', tone: 'bad' }]
          : [{ text: `publishes ${Object.keys(d.publishes ?? {}).length}`, tone: 'ok' }, { text: `subscribes ${Object.keys(d.subscribes ?? {}).length}`, tone: 'ok' }]
      case 'check_tf':
        return Object.entries(d.transforms ?? {}).map(([k, v]: [string, any]) => // eslint-disable-line @typescript-eslint/no-explicit-any
          v.available ? { text: `${k} ${Math.round(v.age_s * 1000)} ms`, tone: v.age_s < 1 ? 'ok' as const : 'bad' as const } : { text: `${k} unavailable`, tone: 'bad' as const })
      case 'inspect_parameters':
        return Object.entries(d.parameters ?? {}).slice(0, 2).map(([k, v]) => ({ text: `${k}=${String(v)}` }))
      case 'get_recent_diagnostics': {
        const st = Object.values(d.statuses ?? {}) as { level: string }[]
        const bad = st.filter((x) => x.level !== 'OK').length
        return [{ text: `${st.length - bad}/${st.length} OK`, tone: bad ? 'warn' : 'ok' }, ...(bad ? [{ text: `${bad} degraded`, tone: 'bad' as const }] : [])]
      }
      case 'get_recent_logs': {
        const n = (d.entries ?? []).length
        return [{ text: n ? `${n} warning/error log${n > 1 ? 's' : ''}` : 'no warnings', tone: n ? 'bad' : 'ok' }]
      }
      case 'get_component_status': {
        const comps = Object.entries(d.components ?? {}) as [string, { state: string; exit_code: number | null }][]
        const down = comps.filter(([, c]) => c.state !== 'running')
        return down.length ? down.map(([n, c]) => ({ text: `${n} ${c.state} (exit ${c.exit_code})`, tone: 'bad' as const }))
          : [{ text: `${comps.length}/${comps.length} processes running`, tone: 'ok' }]
      }
    }
  } catch { /* fall through */ }
  return []
}

type Row = { state: 'done' | 'active' | 'pending' | 'failed'; title: string; chips?: Chip[]; note?: string; anomaly?: string; sub?: string; ms?: number }

function buildRows(inv: Investigation): Row[] {
  const rows: Row[] = []
  const results = new Map<number, InvEvent>()
  for (const e of inv.events) if (e.kind === 'tool_result') results.set(e.step, e)
  for (const e of inv.events) {
    if (e.kind !== 'tool_call') continue
    const r = results.get(e.step)
    const args = (e.args ?? {}) as Record<string, string>
    const anomaly = r?.evidence?.find((x: { anomaly: boolean }) => x.anomaly)?.text as string | undefined
    rows.push({
      state: !r ? 'active' : r.success ? 'done' : 'failed', title: (TITLE[e.tool] ?? (() => e.tool))(args),
      chips: r?.success ? liveValues(e.tool, args, r.data ?? {}) : [], anomaly: r?.success ? anomaly : undefined,
      note: r && !r.success ? r.error : undefined, sub: e.reason && e.step > 1 ? e.reason : undefined, ms: r?.duration_ms,
    })
  }
  const has = (k: string) => inv.events.some((e) => e.kind === k)
  const phase = inv.phase
  const investigating = ['observing', 'investigating', 'diagnosing'].includes(phase)
  const d = inv.diagnosis
  const timeout = [...inv.events].reverse().find((e) => e.kind === 'model_timeout')
  if (investigating && !d && !rows.some((r) => r.state === 'active')) {
    rows.push({ state: 'active', title: timeout && !timeout.final && inv.events.indexOf(timeout) > inv.events.length - 4 ? 'Model timed out — retrying' : 'Choosing next check', sub: 'the model picks the next diagnostic tool' })
  }
  rows.push({
    state: d ? 'done' : ['inconclusive', 'error'].includes(phase) ? 'failed' : 'pending', title: 'Root cause identified',
    chips: d ? [{ text: d.faulty_component, tone: 'bad' }, { text: `evidence score ${d.confidence.toFixed(2)}` }] : [],
    note: phase === 'inconclusive' ? 'insufficient evidence — no repair attempted' : phase === 'error' ? inv.error ?? undefined : undefined,
  })
  const p = inv.proposal
  const repaired = has('repair_started')
  const rejected = phase === 'rejected'
  rows.push({
    state: repaired ? 'done' : rejected ? 'failed' : phase === 'awaiting_approval' ? 'active' : 'pending',
    title: rejected ? 'Repair rejected' : repaired ? 'Repair approved' : 'Repair awaiting approval',
    chips: p ? [{ text: `${p.action} ${p.target}` }, { text: `risk ${p.risk}`, tone: p.risk === 'low' ? 'ok' : 'warn' }] : [],
  })
  const v = inv.verification
  rows.push({
    state: v?.verified ? 'done' : phase === 'repair_failed' ? 'failed' : ['repairing', 'verifying'].includes(phase) ? 'active' : 'pending',
    title: v?.verified ? 'Recovery verified' : phase === 'repair_failed' ? 'Recovery NOT verified' : 'Recovery verification',
    chips: v ? [{ text: `${v.checks.filter((c) => c.passed).length}/${v.checks.length} checks passed`, tone: v.verified ? 'ok' : 'bad' }] : [],
  })
  return rows
}

const ICON = { done: '✓', active: '●', pending: '○', failed: '✕' }

export function Timeline({ inv }: { inv: Investigation | null }) {
  const end = useRef<HTMLDivElement>(null)
  const n = inv?.events.length ?? 0
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [n])

  if (!inv) return <div className="timeline empty">No investigation yet. Inject a fault, then ask RobotOps what is wrong.</div>
  const rows = buildRows(inv)
  const finalTimeout = inv.events.some((e) => e.kind === 'model_timeout' && e.final)
  // a timeout that was retried successfully is reported as recovered; the raw "LLM unavailable" error is not repeated after a final-timeout alert
  const alerts = inv.events.map((e, i) => ({ e, later: inv.events.slice(i + 1) }))
    .filter(({ e }) => (e.kind === 'model_timeout' && !(finalTimeout && !e.final)) || e.kind === 'diagnosis_rejected' || e.kind === 'warning'
      || (e.kind === 'error' && !(finalTimeout && String(e.message).startsWith('LLM unavailable'))))
  return (
    <div className="timeline">
      <div className="tl-query">“{inv.query}”</div>
      <ol className="steps">
        {rows.map((r, i) => (
          <li key={i} className={`step s-${r.state}`}>
            <span className="num mono">{String(i + 1).padStart(2, '0')}</span>
            <span className="ico">{r.state === 'active' ? <span className="spinner" /> : ICON[r.state]}</span>
            <div className="body">
              <div className="title">{r.title}{r.ms !== undefined && <em className="mono">{r.ms} ms</em>}</div>
              {r.chips && r.chips.length > 0 && <div className="chips-row">{r.chips.map((c, j) => <span key={j} className={`vchip ${c.tone ?? ''}`}>{c.text}</span>)}</div>}
              {r.anomaly && <div className="anomaly">{r.anomaly}</div>}
              {r.sub && <div className="sub">{r.sub}</div>}
              {r.note && <div className="note-bad">{r.note}</div>}
            </div>
          </li>
        ))}
      </ol>
      {alerts.map(({ e, later }, i) => <Alert key={i} ev={e} continued={later.some((x) => x.kind === 'llm_call')} />)}
      <div ref={end} />
    </div>
  )
}

function Alert({ ev, continued }: { ev: InvEvent; continued: boolean }) {
  switch (ev.kind) {
    case 'model_timeout':
      if (ev.final) return <div className="alert bad"><b>MODEL RESPONSE TIMEOUT</b> — no response after {ev.timeout_s}s and one retry. Investigation stopped safely; nothing was changed.</div>
      return continued
        ? <div className="alert warn"><b>MODEL RESPONSE TIMEOUT</b> — no response after {ev.timeout_s}s. Retried automatically and continued.</div>
        : <div className="alert warn"><b>MODEL RESPONSE TIMEOUT</b> — no response after {ev.timeout_s}s. Retrying investigation…</div>
    case 'diagnosis_rejected':
      return (
        <div className="alert warn"><b>Evidence check</b> — diagnosis not accepted yet: {ev.errors[0]}<span className="muted"> The agent continues investigating.</span></div>
      )
    case 'warning':
      return <div className="alert warn">{ev.text}</div>
    case 'error':
      return <div className="alert bad">{ev.message}</div>
    default:
      return null
  }
}
