import type { Health, Investigation, Readiness } from '../api'

export type Tone = 'ok' | 'warn' | 'fail' | 'info' | 'muted'
export interface UiState { key: string; label: string; detail?: string; tone: Tone }

// Failures that "Start demo" repairs (it resets the robot, waits for DDS discovery and warms the model). Anything else - Ollama down,
// no ROS environment, supervisor unreachable - it cannot fix, so the button stays disabled for those.
const RECOVERABLE = new Set(['model', 'warmup', 'discovery', 'robot_health', 'topics', 'tf', 'motion', 'diagnostics', 'verification'])

/** Failing checks that would make "Start demo" pointless. Empty means Start demo is worth pressing. */
export function startBlockers(readiness: Readiness | null) {
  return (readiness?.checks ?? []).filter((c) => c.status === 'fail' && !RECOVERABLE.has(c.id))
}

export const TERMINAL = ['resolved', 'repair_failed', 'rejected', 'inconclusive', 'healthy', 'diagnosed', 'error']

const COMPONENT_NAME: Record<string, string> = {
  controller: 'controller', lidar: 'lidar', odometry: 'odometry', tf: 'tf', navigation: 'obstacle detection', ros_graph: 'ros graph',
}

export function unhealthy(health: Health | null): string[] {
  return Object.entries(health?.components ?? {}).filter(([, v]) => v.state !== 'HEALTHY').map(([k, v]) => `${COMPONENT_NAME[k] ?? k} ${v.state.toLowerCase()}`)
}

/** One line describing what the system is doing right now. `key` is exposed as data-state so tests do not depend on copy. */
export function deriveState(a: {
  connected: boolean; readiness: Readiness | null; preparing: string | null; health: Health | null; inv: Investigation | null
}): UiState {
  const { connected, readiness, preparing, health, inv } = a
  if (!connected) return { key: 'disconnected', label: 'Backend disconnected', detail: 'reconnecting', tone: 'fail' }
  if (preparing) return { key: 'preparing', label: 'Preparing', detail: preparing, tone: 'info' }
  if (!readiness) return { key: 'checking', label: 'Checking system', tone: 'muted' }
  if (!readiness.infra_ready) {
    const fixable = startBlockers(readiness).length === 0
    return { key: 'not-ready', label: 'Not ready', detail: `${readiness.infra_reason ?? ''}${fixable ? ' · Start demo will recover this' : ''}`, tone: 'fail' }
  }
  const running = !!inv && !TERMINAL.includes(inv.phase)
  if (running && inv) {
    const p = inv.proposal
    switch (inv.phase) {
      case 'awaiting_approval': return { key: 'awaiting-approval', label: 'Awaiting approval', detail: p ? `${p.action} ${p.target}` : undefined, tone: 'warn' }
      case 'repairing': return { key: 'repairing', label: 'Repairing', detail: p ? `${p.action} ${p.target}` : undefined, tone: 'info' }
      case 'verifying': return { key: 'verifying', label: 'Verifying recovery', tone: 'info' }
      default: return { key: 'investigating', label: 'Investigating', tone: 'info' }
    }
  }
  const bad = unhealthy(health)
  if (bad.length) {
    const last = inv && inv.phase !== 'resolved' ? ` · last investigation ${inv.phase.replace('_', ' ')}` : ''
    return { key: 'fault', label: 'Fault detected', detail: bad.join(', ') + last, tone: 'fail' }
  }
  switch (inv?.phase) {
    case 'resolved': return { key: 'recovered', label: 'Recovery verified', tone: 'ok' }
    case 'error': return { key: 'stopped', label: 'Investigation stopped', detail: `${inv.error ?? 'error'} · nothing was changed`, tone: 'warn' }
    case 'inconclusive': return { key: 'inconclusive', label: 'Inconclusive', detail: 'insufficient evidence · no action taken', tone: 'warn' }
    case 'repair_failed': return { key: 'stopped', label: 'Recovery not verified', detail: 'stopped safely · needs manual attention', tone: 'fail' }
    case 'rejected': return { key: 'rejected', label: 'Repair rejected', detail: 'no action taken', tone: 'warn' }
    case 'healthy': return { key: 'no-fault', label: 'No fault found', tone: 'ok' }
  }
  if (readiness.ready) return { key: 'ready', label: 'Ready', detail: 'model warm · checks passed', tone: 'ok' }
  return { key: 'not-ready', label: 'Not ready', detail: readiness.reason ?? undefined, tone: 'warn' }
}
