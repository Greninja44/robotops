import { useEffect, useMemo, useRef } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, Handle, Position, MarkerType, useReactFlow, useUpdateNodeInternals,
  type Node, type Edge, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { Graph } from '../api'

// Layout follows the data flow. Rows: motion chain, odometry, sensing, sensor transform. Units are px at zoom 1.
const COL = 172
const LAYOUT: Record<string, [number, number]> = {
  '/velocity_commander': [0, 0], '/cmd_vel': [COL, 0], '/base_controller': [COL * 2, 0], '/wheel_states': [COL * 3, 0], '/wheel_odometry': [COL * 4, 0],
  'tf:odom->base_link': [COL * 3, 140], '/odom': [COL * 4, 140],
  '/lidar_driver': [0, 280], '/scan': [COL, 280], '/obstacle_monitor': [COL * 3, 280], '/obstacle_distance': [COL * 4, 280],
  '/tf_broadcaster': [0, 420], 'tf:base_link->laser': [COL, 420],
}
const SIZE = { node: [148, 40], topic: [148, 40], tf: [148, 40] } as const

export type Marks = {
  rootCause?: string        // node id confirmed as the cause
  evidence: Set<string>     // subjects cited by the diagnosis (suspected / involved)
  probing: Set<string>      // subjects of the tool call in progress
}

type RosData = { label: string; kind: 'node' | 'topic' | 'tf'; state: string; sub: string; mark?: string }
type Side = 'left' | 'right' | 'top' | 'bottom'
const POS: Record<Side, Position> = { left: Position.Left, right: Position.Right, top: Position.Top, bottom: Position.Bottom }

function RosNode({ data }: NodeProps<Node<RosData>>) {
  return (
    <div className={`gn gn-${data.kind} st-${data.state}${data.mark ? ` mark-${data.mark}` : ''}`}>
      {(Object.keys(POS) as Side[]).map((s) => (
        <span key={s}>
          <Handle id={`${s}-t`} type="target" position={POS[s]} />
          <Handle id={`${s}-s`} type="source" position={POS[s]} />
        </span>
      ))}
      <div className="gn-name">{data.label}</div>
      <div className="gn-sub">{data.sub}</div>
    </div>
  )
}
const nodeTypes = { ros: RosNode }

function sideFor(a: { x: number; y: number }, b: { x: number; y: number }): [Side, Side] {
  const dx = b.x - a.x, dy = b.y - a.y
  if (Math.abs(dy) > Math.abs(dx) * 0.7) return dy > 0 ? ['bottom', 'top'] : ['top', 'bottom']
  return dx >= 0 ? ['right', 'left'] : ['left', 'right']
}

export function GraphPanel({ graph, marks }: { graph: Graph | null; marks: Marks }) {
  const { nodes, edges } = useMemo(() => build(graph, marks), [graph, marks])
  return (
    <section className="graph">
      <div className="pane-head">
        <h2>ROS graph</h2>
        <div className="legend">
          <span><i className="dot ok" />healthy</span>
          <span><i className="dot fail" />failed</span>
          <span><i className="ring probe" />inspecting</span>
          <span><i className="ring suspect" />involved</span>
        </div>
      </div>
      <div className="graph-canvas">
        {graph && !graph.ros_available && <div className="graph-empty">ROS graph unavailable</div>}
        <ReactFlowProvider>
          <Canvas nodes={nodes} edges={edges} />
        </ReactFlowProvider>
      </div>
    </section>
  )
}

/** Keeps the whole graph in view: refits when the set of nodes changes and whenever the panel is resized. */
function Canvas({ nodes, edges }: { nodes: Node<RosData>[]; edges: Edge[] }) {
  const { fitView } = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  const wrap = useRef<HTMLDivElement>(null)
  const signature = nodes.map((n) => n.id).join('|')
  // The nodes are rebuilt whenever a mark changes (e.g. when a diagnosis cites evidence). React Flow then loses the measured
  // handle positions and silently stops drawing every edge until they are measured again, so ask for a re-measure.
  useEffect(() => {
    const id = requestAnimationFrame(() => updateNodeInternals(nodes.map((n) => n.id)))
    return () => cancelAnimationFrame(id)
  }, [nodes, updateNodeInternals])
  useEffect(() => {
    const id = requestAnimationFrame(() => fitView({ padding: 0.06, minZoom: 0.4, maxZoom: 1.25 }))
    return () => cancelAnimationFrame(id)
  }, [signature, fitView])
  useEffect(() => {
    if (!wrap.current) return
    let t: number | undefined
    const ro = new ResizeObserver(() => { window.clearTimeout(t); t = window.setTimeout(() => fitView({ padding: 0.06, minZoom: 0.4, maxZoom: 1.25 }), 60) })
    ro.observe(wrap.current)
    return () => { ro.disconnect(); window.clearTimeout(t) }
  }, [fitView])
  return (
    <div ref={wrap} className="graph-fit">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.06, minZoom: 0.4, maxZoom: 1.25 }}
        minZoom={0.4} maxZoom={1.25} proOptions={{ hideAttribution: true }} nodesDraggable={false} nodesConnectable={false}
        elementsSelectable={false} zoomOnScroll={false} zoomOnPinch={false} zoomOnDoubleClick={false} panOnDrag={false} preventScrolling={false}>
        <Background color="#1b2129" gap={24} size={1} />
      </ReactFlow>
    </div>
  )
}

function build(graph: Graph | null, marks: Marks): { nodes: Node<RosData>[]; edges: Edge[] } {
  if (!graph) return { nodes: [], edges: [] }
  const nodes: Node<RosData>[] = []
  let extra = 0
  const place = (id: string): { x: number; y: number } => {
    const p = LAYOUT[id]
    if (p) return { x: p[0], y: p[1] }
    const pos = { x: (extra % 5) * COL, y: 520 + Math.floor(extra / 5) * 60 }
    extra++
    return pos
  }
  const mark = (id: string) =>
    marks.rootCause === id ? 'root' : marks.probing.has(id) ? 'probe' : marks.evidence.has(id) ? 'suspect' : undefined
  const add = (id: string, kind: RosData['kind'], data: Omit<RosData, 'kind' | 'mark'>) => {
    const [width, height] = SIZE[kind]
    nodes.push({ id, type: 'ros', position: place(id), width, height, draggable: false, selectable: false, data: { ...data, kind, mark: mark(id) } })
  }

  for (const n of graph.nodes) {
    add(n.id, 'node', { label: n.label, state: n.alive ? (n.expected ? 'ok' : 'extra') : 'missing', sub: n.alive ? 'node' : 'node · unavailable' })
  }
  for (const t of graph.topics) {
    const flowing = t.rate_hz !== null && t.min_rate_hz !== null ? t.rate_hz >= t.min_rate_hz : t.publishers > 0
    const state = !t.expected ? 'bad' : t.rate_hz === null ? 'ok' : flowing ? 'ok' : 'bad'
    add(t.id, 'topic', {
      label: t.label, state,
      sub: t.rate_hz !== null ? `${t.rate_hz.toFixed(1)} Hz · ${t.publishers}→${t.subscribers}` : `${t.publishers}→${t.subscribers}`,
    })
  }
  for (const tf of graph.tf ?? []) {
    const id = `tf:${tf.parent}->${tf.child}`
    add(id, 'tf', {
      label: `tf ${tf.parent}→${tf.child}`, state: tf.fresh ? 'ok' : 'bad',
      sub: tf.age_s === null ? 'unavailable' : tf.fresh ? `${Math.round(tf.age_s * 1000)} ms` : `stale ${tf.age_s.toFixed(1)} s`,
    })
  }

  const at = new Map(nodes.map((n) => [n.id, n.position]))
  const edges: Edge[] = []
  const topicOk = new Map(graph.topics.map((t) => [t.id, t.rate_hz === null || (t.min_rate_hz !== null && t.rate_hz >= t.min_rate_hz)]))
  const link = (id: string, source: string, target: string, live: boolean, flowing: boolean) => {
    const a = at.get(source), b = at.get(target)
    if (!a || !b) return
    const [ss, ts] = sideFor(a, b)
    const color = !live ? '#f85149' : flowing ? '#4b5563' : '#d29922'
    edges.push({
      id, source, target, sourceHandle: `${ss}-s`, targetHandle: `${ts}-t`, type: 'smoothstep', pathOptions: { borderRadius: 4 },
      style: { stroke: color, strokeWidth: 1.25, strokeDasharray: live ? undefined : '4 4' },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 12, height: 12 },
    } as Edge)
  }
  for (const tf of graph.tf ?? []) {
    const id = `tf:${tf.parent}->${tf.child}`
    link(`${tf.broadcaster}>${id}`, tf.broadcaster, id, tf.fresh, tf.fresh)
    if (tf.child === 'laser') link(`${id}>/obstacle_monitor`, id, '/obstacle_monitor', tf.fresh, tf.fresh)
  }
  for (const e of graph.edges) {
    const topic = e.kind === 'pub' ? e.target : e.source
    link(`${e.source}>${e.target}`, e.source, e.target, e.live, e.live && (topicOk.get(topic) ?? true))
  }
  return { nodes, edges }
}
