import { useCallback, useEffect, useRef, useState } from 'react'

export type HealthState = 'HEALTHY' | 'DEGRADED' | 'FAILED' | 'UNKNOWN'

export interface Health {
  overall: HealthState
  ros_available: boolean
  components: Record<string, { state: HealthState; detail: string }>
}

export interface GraphNode { id: string; label: string; expected: boolean; alive: boolean; role?: string }
export interface GraphTopic {
  id: string; label: string; expected: boolean; rate_hz: number | null; min_rate_hz: number | null
  publishers: number; subscribers: number
}
export interface GraphEdge { source: string; target: string; kind: 'pub' | 'sub'; live: boolean }
export interface GraphTf { parent: string; child: string; broadcaster: string; age_s: number | null; fresh: boolean }
export interface Graph { nodes: GraphNode[]; topics: GraphTopic[]; edges: GraphEdge[]; tf?: GraphTf[]; ros_available: boolean }

export interface Evidence {
  id: string; text: string; anomaly: boolean; subjects: string[]; source?: string; step?: number
}

export interface InvEvent {
  seq: number; ts: number; kind: string; phase: string
  [k: string]: any // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface Diagnosis {
  status: string; root_cause: string; faulty_component: string; evidence: Evidence[]
  confidence: number; confidence_basis: string[]; uncited_anomalies: string[]
  recommended_action: null | {
    action: string; target: string; risk: string; requires_approval: boolean; reason: string; expected_result: string
  }
}

export interface Check { check: string; passed: boolean; detail: string; scope: 'target' | 'system' }

export interface Investigation {
  id: string; query: string; phase: string; created_at: number; finished_at: number | null; round: number
  tool_calls: number; llm_calls: number; llm_seconds: number
  diagnosis: Diagnosis | null
  proposal: null | {
    id: string; action: string; target: string; risk: string; reason: string; expected_result: string
    evidence: string[]; state: string; decided_by?: string | null
  }
  repair: null | { executed: boolean; error?: string; duration_ms?: number }
  verification: null | { verified: boolean; attempts: number; checks: Check[]; failed: Check[] }
  error: string | null
  diagnosis_seconds: number | null
  evidence: Evidence[]
  events: InvEvent[]
}

export interface Readiness {
  ready: boolean; infra_ready: boolean; reason: string | null; infra_reason: string | null
  warnings: string[]; preparing: string | null
  chips: Record<'ros' | 'agent' | 'ollama' | 'model' | 'dds', string>
  checks: { id: string; label: string; status: 'pass' | 'warn' | 'fail'; detail: string }[]
}

export interface Status {
  backend: string
  ros: { available: boolean; error: string | null }
  llm: { reachable: boolean; model: string; model_available: boolean }
  supervisor: { reachable: boolean }
  investigation_running: boolean
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!r.ok) {
    let msg = `${r.status}`
    try { msg = (await r.json()).detail ?? msg } catch { /* not json */ }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return r.json()
}

const SYNC_KINDS = new Set(['phase', 'diagnosis', 'verification', 'repair_result', 'approval', 'error'])

/** Live state from the backend WebSocket, with automatic reconnect. */
export function useRobotOps() {
  const [health, setHealth] = useState<Health | null>(null)
  const [graph, setGraph] = useState<Graph | null>(null)
  const [inv, setInv] = useState<Investigation | null>(null)
  const [connected, setConnected] = useState(false)
  const [status, setStatus] = useState<Status | null>(null)
  const [lastFault, setLastFault] = useState<{ fault: string; ts: number } | null>(null)
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [preparing, setPreparing] = useState<string | null>(null)
  const invId = useRef<string | null>(null)

  const refresh = useCallback(async (id: string) => {
    try {
      const s = await api<Investigation>(`/api/investigations/${id}`)
      if (invId.current === id) setInv(s)
    } catch { /* backend restarting */ }
  }, [])

  useEffect(() => {
    let ws: WebSocket | null = null
    let stopped = false
    let delay = 500
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      ws.onopen = () => { setConnected(true); delay = 500 }
      ws.onclose = () => {
        setConnected(false)
        if (!stopped) setTimeout(connect, delay)
        delay = Math.min(delay * 2, 5000)
      }
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data)
        switch (msg.type) {
          case 'hello':
            if (msg.health) setHealth(msg.health)
            if (msg.graph) setGraph(msg.graph)
            if (msg.readiness) setReadiness(msg.readiness)
            if (msg.investigation) { invId.current = msg.investigation.id; setInv(msg.investigation) }
            break
          case 'readiness':
            setReadiness(msg.readiness)
            setPreparing(msg.readiness.preparing ?? null)
            break
          case 'prepare_step':
            setPreparing(msg.text)
            break
          case 'system':
            setHealth(msg.health); setGraph(msg.graph)
            break
          case 'investigation_started':
            invId.current = msg.investigation.id
            setInv(msg.investigation)
            break
          case 'investigation_event':
            if (msg.investigation_id !== invId.current) break
            setInv((cur) => cur && cur.id === msg.investigation_id
              ? { ...cur, phase: msg.event.phase, events: [...cur.events, msg.event] } : cur)
            if (SYNC_KINDS.has(msg.event.kind)) refresh(msg.investigation_id)
            break
          case 'investigation_done':
            if (msg.investigation.id === invId.current) setInv(msg.investigation)
            break
          case 'fault_injected':
            setLastFault({ fault: msg.fault, ts: msg.ts })
            break
          case 'demo_reset':
            setLastFault(null)
            invId.current = null
            setInv(null)
            break
        }
      }
    }
    connect()
    return () => { stopped = true; ws?.close() }
  }, [refresh])

  useEffect(() => {
    let alive = true
    const poll = async () => {
      try { const s = await api<Status>('/api/status'); if (alive) setStatus(s) } catch { if (alive) setStatus(null) }
    }
    poll()
    const t = setInterval(poll, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  return { health, graph, inv, setInv, connected, status, lastFault, invId, readiness, preparing }
}
