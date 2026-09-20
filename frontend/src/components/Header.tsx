import type { Graph, Health, Readiness, Status } from '../api'
import type { UiState } from '../lib/uiState'

const READINESS: [keyof Readiness['chips'], string][] = [['ros', 'ROS'], ['agent', 'Agent'], ['ollama', 'Ollama'], ['model', 'Model'], ['dds', 'DDS']]

export function Header({ health, graph, status }: { health: Health | null; graph: Graph | null; status: Status | null }) {
  const overall = health?.overall ?? 'UNKNOWN'
  const nodes = graph?.nodes.filter((n) => n.alive).length
  return (
    <header className="hdr">
      <div className="brand"><b>RobotOps</b><span>ROS 2 / demo_robot</span></div>
      <div className="hdr-meta mono">
        {status?.llm && <span>model {status.llm.model}</span>}
        {nodes !== undefined && <span>{nodes} nodes</span>}
        {graph && <span>{graph.topics.length} topics</span>}
      </div>
      <div className={`sys sys-${overall}`} data-testid="system-state"><i className="dot" />{overall.charAt(0) + overall.slice(1).toLowerCase()}</div>
    </header>
  )
}

export function StatusBar({ state, readiness }: { state: UiState; readiness: Readiness | null }) {
  const busy = ['investigating', 'repairing', 'verifying', 'preparing'].includes(state.key)
  return (
    <div className={`statusbar tone-${state.tone}`} data-testid="status-bar" data-state={state.key}>
      <div className="st-main">
        {busy ? <span className="spinner" /> : <i className="dot" />}
        <b>{state.label}</b>
        {state.detail && <span className="st-detail" title={state.detail}>{state.detail}</span>}
      </div>
      <div className="st-ready mono">
        {READINESS.map(([k, label]) => {
          const v = readiness?.chips?.[k] ?? 'UNKNOWN'
          const cls = v === 'READY' || v === 'WARM' ? 'ok' : v === 'WARN' ? 'warn' : v === 'UNKNOWN' ? 'muted' : 'fail'
          return <span key={k} className={`rd rd-${cls}`} title={`${label}: ${v.toLowerCase()}`}><i className="dot" />{label}{k === 'model' && v !== 'UNKNOWN' ? ` ${v.toLowerCase()}` : ''}</span>
        })}
        {readiness && readiness.warnings.length > 0 && <span className="rd rd-warn" title={readiness.warnings.join('\n')}>{readiness.warnings.length} warning{readiness.warnings.length > 1 ? 's' : ''}</span>}
      </div>
    </div>
  )
}
