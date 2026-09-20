import type { Investigation } from '../api'

const fmt = (s: number | null | undefined) => (s === null || s === undefined ? '—' : `${s.toFixed(1)} s`)

export function Incident({ inv }: { inv: Investigation | null }) {
  if (!inv || inv.phase !== 'resolved' || !inv.diagnosis || !inv.verification) return null
  const ev = inv.events
  const ts = (k: string, phase?: string) => ev.find((e) => e.kind === k && (!phase || e.phase === phase))?.ts as number | undefined
  const awaiting = ev.find((e) => e.kind === 'phase' && e.phase === 'awaiting_approval')?.ts as number | undefined
  const approved = ts('approval')
  const repairStart = ev.find((e) => e.kind === 'phase' && e.phase === 'repairing')?.ts as number | undefined
  const total = inv.finished_at ? inv.finished_at - inv.created_at : null
  const wait = awaiting && approved ? approved - awaiting : null
  const ok = inv.verification.checks.filter((c) => c.passed).length
  const act = inv.diagnosis.recommended_action
  return (
    <section className="incident">
      <div className="incident-head">
        <span className="incident-badge">✓</span>
        <div><div className="incident-title">INCIDENT RESOLVED</div><div className="incident-sub">{inv.diagnosis.root_cause}</div></div>
      </div>
      <div className="incident-grid">
        <div><label>Root cause</label><b className="mono">{inv.diagnosis.faulty_component}</b></div>
        <div><label>Diagnosis</label><b>{fmt(inv.diagnosis_seconds)}</b></div>
        <div><label>Tools used</label><b>{inv.tool_calls}</b></div>
        <div><label>Repair</label><b className="mono">{act ? act.action : '—'}</b></div>
        <div><label>Verification</label><b>{ok}/{inv.verification.checks.length} checks</b></div>
        <div><label>Approve → verified</label><b>{fmt(repairStart && inv.finished_at ? inv.finished_at - repairStart : null)}</b></div>
        <div><label>Total recovery</label><b>{fmt(total)}</b>{wait !== null && <em>incl. {wait.toFixed(1)} s awaiting approval</em>}</div>
      </div>
    </section>
  )
}
