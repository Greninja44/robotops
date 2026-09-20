import { useEffect, useRef, useState } from 'react'
import { api, type Check, type InvEvent, type Investigation as Inv } from '../api'
import { actionVerb, clock, sec } from '../lib/format'

type Val = { text: string; tone?: 'ok' | 'bad' | 'warn' }

/** Real values from a tool's structured result. Nothing here is invented: every number comes from the tool data. */
function values(tool: string, d: Record<string, any>): Val[] { // eslint-disable-line @typescript-eslint/no-explicit-any
  try {
    switch (tool) {
      case 'get_ros_health': {
        const miss: string[] = d.expected_nodes_missing ?? []
        return [{ text: `${d.node_count} nodes · ${d.topic_count} topics` }, ...(miss.length ? [{ text: `missing ${miss.join(', ')}`, tone: 'bad' as const }] : [])]
      }
      case 'list_nodes': {
        const miss: string[] = d.expected_missing ?? []
        return [{ text: `${d.count} nodes` }, ...(miss.length ? [{ text: `${miss.join(', ')} missing`, tone: 'bad' as const }] : [])]
      }
      case 'list_topics': return [{ text: `${Object.keys(d.topics ?? {}).length} active topics` }]
      case 'inspect_topic':
        if (d.exists === false) return [{ text: 'does not exist', tone: 'bad' }]
        return [{ text: `${d.publisher_count} publisher${d.publisher_count === 1 ? '' : 's'}`, tone: d.publisher_count ? undefined : 'bad' },
          { text: `${d.subscriber_count} subscriber${d.subscriber_count === 1 ? '' : 's'}`, tone: d.subscriber_count ? undefined : 'bad' }]
      case 'measure_topic_rate':
        return [{ text: `${Number(d.rate_hz).toFixed(1)} Hz`, tone: d.rate_hz > 0 && d.rate_hz >= (d.expected_min_hz ?? 0) ? undefined : 'bad' },
          ...(d.expected_min_hz ? [{ text: `min ${d.expected_min_hz} Hz` }] : [])]
      case 'inspect_node':
        return d.exists === false ? [{ text: 'not running', tone: 'bad' }]
          : [{ text: `publishes ${Object.keys(d.publishes ?? {}).length}` }, { text: `subscribes ${Object.keys(d.subscribes ?? {}).length}` }]
      case 'check_tf':
        return Object.entries(d.transforms ?? {}).map(([k, v]: [string, any]) => // eslint-disable-line @typescript-eslint/no-explicit-any
          v.available ? { text: `${k} ${Math.round(v.age_s * 1000)} ms`, tone: v.age_s < 1 ? undefined : 'bad' as const } : { text: `${k} unavailable`, tone: 'bad' as const })
      case 'inspect_parameters':
        return Object.entries(d.parameters ?? {}).slice(0, 2).map(([k, v]) => ({ text: `${k}=${String(v)}` }))
      case 'get_recent_diagnostics': {
        const st = Object.values(d.statuses ?? {}) as { level: string }[]
        const bad = st.filter((x) => x.level !== 'OK').length
        return [{ text: `${st.length - bad}/${st.length} ok`, tone: bad ? 'warn' : undefined }, ...(bad ? [{ text: `${bad} not ok`, tone: 'bad' as const }] : [])]
      }
      case 'get_recent_logs': {
        const n = (d.entries ?? []).length
        return [{ text: n ? `${n} warning/error log${n > 1 ? 's' : ''}` : 'no warnings', tone: n ? 'bad' : undefined }]
      }
      case 'get_component_status': {
        const comps = Object.entries(d.components ?? {}) as [string, { state: string; exit_code: number | null }][]
        const down = comps.filter(([, c]) => c.state !== 'running')
        return down.length ? down.map(([n, c]) => ({ text: `${n} ${c.state} (exit ${c.exit_code})`, tone: 'bad' as const }))
          : [{ text: `${comps.length}/${comps.length} processes running` }]
      }
    }
  } catch { /* fall through */ }
  return []
}

const shorten = (t: string, n: number) => {
  if (t.length <= n) return t
  const cut = t.slice(0, n)
  return cut.slice(0, Math.max(cut.lastIndexOf(' '), n - 20)).replace(/[\s,;:(-]+$/, '') + '…'
}

type Entry = {
  key: string; t: number; kind: 'tool' | 'event' | 'alert' | 'pending'; name: string; target?: string; vals?: Val[]; note?: string
  tone?: 'ok' | 'bad' | 'warn'; hint?: string; ms?: number; count?: number
}

function targetOf(args: Record<string, string>) {
  const parts = [args.topic, args.node, args.parent_frame && `${args.parent_frame}→${args.child_frame}`].filter(Boolean)
  return parts.join(' ') || undefined
}

function entries(inv: Inv): Entry[] {
  const out: Entry[] = []
  const results = new Map<number, InvEvent>()
  for (const e of inv.events) if (e.kind === 'tool_result') results.set(e.step, e)
  const finalTimeout = inv.events.some((e) => e.kind === 'model_timeout' && e.final)
  inv.events.forEach((e, i) => {
    const later = inv.events.slice(i + 1)
    switch (e.kind) {
      case 'tool_call': {
        const r = results.get(e.step)
        const args = (e.args ?? {}) as Record<string, string>
        const anomaly = r?.evidence?.find((x: { anomaly: boolean }) => x.anomaly)?.text as string | undefined
        out.push({
          key: `t${e.step}`, t: e.ts, kind: r ? 'tool' : 'pending', name: e.tool, target: targetOf(args), hint: e.reason ?? undefined, ms: r?.duration_ms,
          vals: r?.success ? values(e.tool, r.data ?? {}) : [], note: r?.success ? anomaly : r?.error, tone: r && !r.success ? 'warn' : undefined,
        })
        break
      }
      case 'model_timeout':
        if (e.final) out.push({ key: `m${e.seq}`, t: e.ts, kind: 'alert', name: 'model timeout', note: `no response after ${e.timeout_s}s and one retry - investigation stopped, nothing changed`, tone: 'bad' })
        else if (!finalTimeout) out.push({ key: `m${e.seq}`, t: e.ts, kind: 'alert', name: 'model timeout', tone: 'warn',
          note: later.some((x) => x.kind === 'llm_call') ? `no response after ${e.timeout_s}s - retried, continued` : `no response after ${e.timeout_s}s - retrying` })
        break
      case 'diagnosis_rejected':
        out.push({ key: `r${e.seq}`, t: e.ts, kind: 'alert', name: 'evidence check', tone: 'warn', note: shorten(String(e.errors?.[0] ?? 'diagnosis not accepted').split('\n')[0], 120) })
        break
      case 'warning': out.push({ key: `w${e.seq}`, t: e.ts, kind: 'alert', name: 'warning', tone: 'warn', note: e.text }); break
      case 'error':
        if (!(finalTimeout && String(e.message).startsWith('LLM unavailable'))) out.push({ key: `e${e.seq}`, t: e.ts, kind: 'alert', name: 'error', tone: 'bad', note: e.message })
        break
      case 'diagnosis':
        out.push({ key: `d${e.seq}`, t: e.ts, kind: 'event', name: 'diagnosis', target: e.diagnosis.faulty_component, vals: [{ text: `evidence score ${e.diagnosis.confidence.toFixed(2)}` }] })
        break
      case 'phase':
        if (['inconclusive', 'repair_failed', 'rejected', 'healthy'].includes(e.phase)) {
          out.push({ key: `f${e.seq}`, t: e.ts, kind: 'alert', name: e.phase.replace('_', ' '), tone: e.phase === 'repair_failed' ? 'bad' : 'warn',
            note: e.reason ?? undefined })
        }
        if (e.phase === 'awaiting_approval' && e.proposal) out.push({ key: `p${e.seq}`, t: e.ts, kind: 'event', name: 'proposal', target: `${e.proposal.action} ${e.proposal.target}`, vals: [{ text: `risk ${e.proposal.risk}` }] })
        break
      case 'approval':
        out.push({ key: `a${e.seq}`, t: e.ts, kind: 'event', name: e.state === 'approved' ? 'approved' : e.state, target: e.decided_by ?? undefined, tone: e.state === 'approved' ? 'ok' : 'warn' })
        break
      case 'repair_started': out.push({ key: `s${e.seq}`, t: e.ts, kind: 'event', name: 'repair', target: `${e.action} ${e.target}` }); break
      case 'repair_result':
        if (!e.executed) out.push({ key: `x${e.seq}`, t: e.ts, kind: 'alert', name: 'repair', tone: 'bad', note: e.error })
        break
      case 'verification': {
        const passed = (e.checks ?? []).filter((c: Check) => c.passed).length
        out.push({ key: `v${e.seq}`, t: e.ts, kind: 'event', name: 'verification', tone: e.verified ? 'ok' : 'bad',
          vals: [{ text: `${passed}/${(e.checks ?? []).length} checks passed`, tone: e.verified ? undefined : 'bad' }] })
        break
      }
    }
  })
  const merged: Entry[] = []
  for (const e of out) {
    const prev = merged[merged.length - 1]
    if (prev && e.kind === 'alert' && prev.kind === 'alert' && prev.name === e.name && prev.note === e.note) prev.count = (prev.count ?? 1) + 1
    else merged.push({ ...e })
  }
  return merged
}

export function Investigation({ inv, running }: { inv: Inv | null; running: boolean }) {
  return (
    <section className="inv">
      <div className="pane-head">
        <h2>Investigation</h2>
        {inv && <span className="meta mono">{inv.tool_calls} tool calls{inv.diagnosis_seconds ? ` · diagnosed in ${sec(inv.diagnosis_seconds)}` : ''}</span>}
      </div>
      <Stream inv={inv} running={running} />
      {(inv?.diagnosis || inv?.proposal || inv?.verification || inv?.phase === 'verifying') && (
        <div className="decisions">
          {inv?.diagnosis && <RootCause inv={inv} />}
          {inv?.proposal && <Proposed inv={inv} />}
          {(inv?.verification || inv?.phase === 'verifying') && <Verification inv={inv} />}
        </div>
      )}
    </section>
  )
}

function Stream({ inv, running }: { inv: Inv | null; running: boolean }) {
  const end = useRef<HTMLDivElement>(null)
  const n = inv?.events.length ?? 0
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [n])
  if (!inv) return <div className="stream empty">No investigation. Inject a fault from Demo controls, then run a query.</div>
  const list = entries(inv)
  const waiting = running && inv.phase === 'investigating' && !list.some((e) => e.kind === 'pending')
  return (
    <div className="stream" data-testid="stream">
      <div className="query">{inv.query}</div>
      {list.map((e) => (
        <div key={e.key} className={`ev ev-${e.kind}${e.tone ? ` tone-${e.tone}` : ''}`} title={e.hint}>
          <time className="mono">{clock(e.t)}</time>
          <div className="ev-body">
            <div className="ev-title">
              {e.kind === 'pending' && <span className="spinner" />}
              <span className="mono ev-name">{e.name}{e.count && e.count > 1 ? ` ×${e.count}` : ''}</span>
              {e.target && <span className="mono ev-target">{e.target}</span>}
              {e.ms !== undefined && <span className="mono ev-ms">{e.ms} ms</span>}
            </div>
            {e.vals && e.vals.length > 0 && <div className="ev-vals mono">{e.vals.map((v, i) => <span key={i} className={v.tone ? `v-${v.tone}` : ''}>{v.text}</span>)}</div>}
            {e.note && <div className="ev-note">{e.note}</div>}
          </div>
        </div>
      ))}
      {waiting && <div className="ev ev-pending"><time className="mono" /><div className="ev-body"><div className="ev-title"><span className="spinner" /><span className="muted">selecting next check</span></div></div></div>}
      <div ref={end} />
    </div>
  )
}

function RootCause({ inv }: { inv: Inv }) {
  const d = inv.diagnosis!
  const [basis, setBasis] = useState(false)
  const compact = !!inv.verification || ['repairing', 'verifying'].includes(inv.phase)      // decision made: leave room for the outcome
  const [open, setOpen] = useState<boolean | null>(null)
  const showEvidence = open ?? !compact
  return (
    <div className="sect" data-testid="root-cause">
      <h3>Root cause<button className="link" onClick={() => setOpen(!showEvidence)}>{showEvidence ? 'hide evidence' : `evidence (${d.evidence.length})`}</button></h3>
      <p className="cause">{d.root_cause}</p>
      {showEvidence && <table className="evid">
        <tbody>
          {d.evidence.slice(0, 4).map((e) => (
            <tr key={e.id} className={e.anomaly ? 'anom' : ''} title={e.text}>
              <td className="mono id">{e.id}</td><td className="txt">{e.text}</td><td className="mono src">{e.source}</td>
            </tr>
          ))}
        </tbody>
      </table>}
      <div className="score" title="Heuristic computed in code from the cited evidence; not a model-reported confidence.">
        <span>Evidence score</span><b className="mono">{d.confidence.toFixed(2)}</b>
        <button className="link" onClick={() => setBasis(!basis)}>{basis ? 'hide basis' : 'basis'}</button>
      </div>
      {basis && <ul className="basis">{d.confidence_basis.map((b, i) => <li key={i}>{b}</li>)}</ul>}
    </div>
  )
}

function Proposed({ inv }: { inv: Inv }) {
  const p = inv.proposal!
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const decide = async (approve: boolean) => {
    setBusy(true); setErr(null)
    try { await api(`/api/investigations/${inv.id}/${approve ? 'approve' : 'reject'}`, { proposal_id: p.id, operator: 'dashboard operator' }) }
    catch (e) { setErr((e as Error).message) }
    setBusy(false)
  }
  const pending = inv.phase === 'awaiting_approval' && p.state === 'pending'
  if (!pending && p.state !== 'pending') {
    return (
      <div className="sect proposal decided-line" data-testid="proposal">
        <span className="mono"><b>{p.action}</b> <span className="muted">{p.target}</span></span>
        <span className="muted nowrap" title={p.decided_by ? `${p.state} by ${p.decided_by}` : p.state}>{p.state} · risk <span className={`risk-${p.risk}`}>{p.risk}</span></span>
      </div>
    )
  }
  return (
    <div className={`sect proposal${pending ? ' needs-action' : ''}`} data-testid="proposal">
      <h3>Proposed action{pending && <em>requires approval</em>}</h3>
      <div className="action mono"><b>{p.action}</b> <span>{p.target}</span></div>
      <dl className="kv">
        <dt>Reason</dt><dd className="clamp" title={p.reason}>{p.reason}</dd>
        <dt>Expected</dt><dd className="clamp" title={p.expected_result}>{p.expected_result}</dd>
        <dt>Risk</dt><dd className={`risk-${p.risk}`}>{p.risk}</dd>
      </dl>
      {pending ? (
        <div className="buttons">
          <button className="btn" data-testid="reject" disabled={busy} onClick={() => decide(false)}>Reject</button>
          <button className="btn primary" data-testid="approve" disabled={busy} onClick={() => decide(true)}>Approve {actionVerb(p.action)}</button>
        </div>
      ) : (
        <div className="decided">{p.state === 'pending' ? 'waiting' : `${p.state}${p.decided_by ? ` by ${p.decided_by}` : ''}`}</div>
      )}
      {err && <div className="form-err">{err}</div>}
    </div>
  )
}

function pick(checks: Check[]) {
  const failed = checks.filter((c) => !c.passed)
  const target = checks.filter((c) => c.passed && c.scope === 'target')
  const motion = checks.filter((c) => c.passed && c.check.startsWith('odometry'))
  const seen = new Set<string>()
  return [...failed, ...target, ...motion].filter((c) => !seen.has(c.check) && seen.add(c.check)).slice(0, 6)
}

function Verification({ inv }: { inv: Inv }) {
  const v = inv.verification
  const last = [...inv.events].reverse().find((e) => e.kind === 'verification_attempt')
  if (!v) return <div className="sect"><h3>Verification</h3><div className="muted"><span className="spinner" /> re-measuring nodes, topic rates, TF, odometry{last ? ` · pass ${last.attempt}: ${last.passed}/${last.total}` : ''}</div></div>
  const ok = v.checks.filter((c) => c.passed).length
  const t = (k: string, phase?: string) => inv.events.find((e) => e.kind === k && (!phase || e.phase === phase))?.ts as number | undefined
  const awaiting = inv.events.find((e) => e.kind === 'phase' && e.phase === 'awaiting_approval')?.ts as number | undefined
  const approved = t('approval')
  const repairStart = inv.events.find((e) => e.kind === 'phase' && e.phase === 'repairing')?.ts as number | undefined
  const fin = inv.finished_at
  const recovery = repairStart && fin ? fin - repairStart : null
  const total = fin ? fin - inv.created_at : null
  const wait = awaiting && approved ? approved - awaiting : null
  const resolved = inv.phase === 'resolved'
  return (
    <div className="sect" data-testid="verification">
      <h3>Verification</h3>
      <ul className="checks">
        {pick(v.checks).map((c) => <li key={c.check} className={c.passed ? 'pass' : 'fail'}><span>{c.passed ? '✓' : '✕'}</span><span className="mono">{c.check}</span><em className="mono">{c.detail}</em></li>)}
      </ul>
      <div className="score"><span>{ok} / {v.checks.length} checks passed</span></div>
      <div className={`verdict ${resolved ? 'ok' : 'fail'}`} data-testid="recovery">
        {resolved ? `Recovery verified${recovery ? ` · ${sec(recovery)}` : ''}` : inv.phase === 'investigating' ? 'Not verified · re-investigating' : 'Recovery not verified'}
      </div>
      {resolved && (
        <dl className="kv summary" data-testid="incident">
          <dt>Root cause</dt><dd className="mono">{inv.diagnosis?.faulty_component}</dd>
          <dt>Diagnosis</dt><dd>{sec(inv.diagnosis_seconds)}</dd>
          <dt>Tool calls</dt><dd>{inv.tool_calls}</dd>
          <dt>Repair</dt><dd className="mono">{inv.diagnosis?.recommended_action?.action ?? '-'}</dd>
          <dt>Recovery</dt><dd>{sec(recovery)}</dd>
          <dt>Total</dt><dd>{sec(total)}{wait !== null ? <span className="muted"> · incl. {sec(wait)} awaiting approval</span> : null}</dd>
        </dl>
      )}
    </div>
  )
}
