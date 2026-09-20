import { useState } from 'react'
import { api, type Health, type Investigation, type Readiness } from '../api'

const CHIPS: [keyof Readiness['chips'], string][] = [['ros', 'ROS'], ['agent', 'Agent'], ['ollama', 'Ollama'], ['model', 'Model'], ['dds', 'DDS']]

export function ReadyBanner({ readiness, preparing, health, inv, connected }: {
  readiness: Readiness | null; preparing: string | null; health: Health | null; inv: Investigation | null; connected: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const running = !!inv && !['resolved', 'repair_failed', 'rejected', 'inconclusive', 'healthy', 'diagnosed', 'error'].includes(inv.phase)

  let tone = 'idle', title = 'CONNECTING…', sub = ''
  if (!connected) { tone = 'bad'; title = 'BACKEND OFFLINE'; sub = 'reconnecting…' }
  else if (preparing) { tone = 'warn'; title = 'PREPARING DEMO'; sub = preparing }
  else if (!readiness) { tone = 'idle'; title = 'CHECKING SYSTEM…' }
  else if (!readiness.infra_ready) { tone = 'bad'; title = 'NOT READY'; sub = readiness.infra_reason ?? '' }
  else if (running) { tone = 'live'; title = 'INVESTIGATING'; sub = 'RobotOps is diagnosing the failure' }
  else if (inv?.phase === 'resolved') { tone = 'good'; title = 'SYSTEM RECOVERED'; sub = 'recovery independently verified' }
  else if (health && health.overall !== 'HEALTHY') { tone = 'bad'; title = 'FAULT DETECTED'; sub = 'ask RobotOps to diagnose the robot' }
  else if (inv?.phase === 'error') { tone = 'warn'; title = 'INVESTIGATION STOPPED'; sub = `${inv.error ?? 'error'} - nothing was changed` }
  else if (inv?.phase === 'inconclusive') { tone = 'warn'; title = 'INVESTIGATION STOPPED'; sub = 'insufficient evidence - RobotOps did not act on a guess' }
  else if (inv?.phase === 'repair_failed') { tone = 'bad'; title = 'REPAIR NOT VERIFIED'; sub = 'stopped safely - manual attention needed' }
  else if (inv?.phase === 'rejected') { tone = 'warn'; title = 'REPAIR REJECTED'; sub = 'no action was taken' }
  else if (inv?.phase === 'healthy') { tone = 'good'; title = 'NO FAULT FOUND'; sub = 'all checks healthy' }
  else if (readiness.ready) { tone = 'good'; title = 'READY FOR DEMO'; sub = 'ROS healthy · model warm · all checks passed' }
  else { tone = 'warn'; title = 'NOT READY'; sub = readiness.reason ?? '' }

  const canStart = !!readiness?.infra_ready && !running && !preparing && connected && !busy
  const start = async () => {
    setBusy(true); setErr(null)
    try { await api('/api/demo/prepare', {}) } catch (e) { setErr((e as Error).message) }
    setBusy(false)
  }
  return (
    <section className={`ready ready-${tone}`}>
      <div className="ready-main">
        <div className="ready-title">{title}</div>
        <div className="ready-sub">{err ?? sub}</div>
      </div>
      <div className="ready-chips">
        {CHIPS.map(([k, label]) => {
          const v = readiness?.chips?.[k] ?? 'UNKNOWN'
          return <span key={k} className={`rchip rc-${v}`}><i />{label}<b>{v}</b></span>
        })}
      </div>
      <button className="start-btn" disabled={!canStart} onClick={start}
        title={canStart ? 'Reset the robot, warm the model and verify everything' : 'Available when the tooling is ready and no investigation is running'}>
        {busy || preparing ? 'PREPARING…' : 'START DEMO'}
      </button>
      {readiness && readiness.warnings.length > 0 && !preparing && (
        <div className="ready-warn">⚠ {readiness.warnings[0]}{readiness.warnings.length > 1 ? ` (+${readiness.warnings.length - 1} more)` : ''}</div>
      )}
    </section>
  )
}
