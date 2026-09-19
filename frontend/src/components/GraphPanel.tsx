import { useMemo } from 'react'
import {
  ReactFlow, Background, Handle, Position, MarkerType,
  type Node, type Edge, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { Graph } from '../api'

// Hand-placed layout that follows the robot's data flow (left -> right).
const LAYOUT: Record<string, [number, number]> = {
  '/velocity_commander': [0, 40],
  '/cmd_vel': [240, 48],
  '/base_controller': [450, 40],
  '/wheel_states': [690, 48],
  '/wheel_odometry': [900, 40],
  'tf:odom->base_link': [1140, 48],
  '/odom': [910, 175],
  '/lidar_driver': [0, 250],
  '/scan': [470, 258],
  '/obstacle_monitor': [900, 300],
  '/obstacle_distance': [1140, 308],
  '/tf_broadcaster': [0, 400],
  'tf:base_link->laser': [450, 408],
}

export type Marks = {
  rootCause?: string        // node id
  evidence: Set<string>     // subjects cited in the diagnosis
  probing: Set<string>      // subjects of the tool call in progress
}

type RosData = { label: string; kind: 'node' | 'topic' | 'tf'; state: string; sub?: string; mark?: string }

function RosNode({ data }: NodeProps<Node<RosData>>) {
  return (
    <div className={`gnode gnode-${data.kind} st-${data.state} ${data.mark ? `mark-${data.mark}` : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="gnode-label">{data.label}</div>
      {data.sub && <div className="gnode-sub">{data.sub}</div>}
      {data.mark === 'root' && <div className="gnode-flag">ROOT CAUSE</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

const nodeTypes = { ros: RosNode }

export function GraphPanel({ graph, marks }: { graph: Graph | null; marks: Marks }) {
  const { nodes, edges } = useMemo(() => build(graph, marks), [graph, marks])
  return (
    <section className="panel graph-panel">
      <div className="panel-head">
        <h2>ROS System Graph</h2>
        <div className="legend">
          <span><i className="lg lg-ok" />healthy</span>
          <span><i className="lg lg-bad" />failed / missing</span>
          <span><i className="lg lg-probe" />being inspected</span>
          <span><i className="lg lg-root" />root cause</span>
        </div>
      </div>
      <div className="graph-canvas">
        {graph && !graph.ros_available && <div className="overlay-msg">ROS graph unavailable</div>}
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.12 }}
          proOptions={{ hideAttribution: true }} nodesDraggable={false} nodesConnectable={false}
          elementsSelectable={false} zoomOnScroll={false} panOnDrag={false} preventScrolling={false}>
          <Background color="#1c2531" gap={22} size={1} />
        </ReactFlow>
      </div>
    </section>
  )
}

function build(graph: Graph | null, marks: Marks): { nodes: Node<RosData>[]; edges: Edge[] } {
  if (!graph) return { nodes: [], edges: [] }
  const nodes: Node<RosData>[] = []
  const edges: Edge[] = []
  let extra = 0
  const place = (id: string): { x: number; y: number } => {
    const p = LAYOUT[id]
    if (p) return { x: p[0] * 0.8, y: p[1] * 0.95 }
    const pos = { x: 240 + (extra % 4) * 230, y: -90 - Math.floor(extra / 4) * 70 }
    extra++
    return pos
  }
  const mark = (id: string) =>
    marks.rootCause === id ? 'root' : marks.probing.has(id) ? 'probe' : marks.evidence.has(id) ? 'evidence' : undefined

  for (const n of graph.nodes) {
    nodes.push({
      id: n.id, type: 'ros', position: place(n.id),
      data: {
        label: n.label, kind: 'node', mark: mark(n.id),
        state: n.alive ? (n.expected ? 'ok' : 'extra') : 'missing',
        sub: n.alive ? undefined : 'NOT RUNNING',
      },
    })
  }
  for (const t of graph.topics) {
    const flowing = t.rate_hz !== null && t.min_rate_hz !== null ? t.rate_hz >= t.min_rate_hz : t.publishers > 0
    const state = !t.expected ? 'bad' : t.rate_hz === null ? 'ok' : flowing ? 'ok' : 'bad'
    nodes.push({
      id: t.id, type: 'ros', position: place(t.id),
      data: {
        label: t.label, kind: 'topic', state, mark: mark(t.id),
        sub: t.rate_hz !== null ? `${t.rate_hz.toFixed(1)} Hz · ${t.publishers}→${t.subscribers}` : `${t.publishers} pub · ${t.subscribers} sub`,
      },
    })
  }
  for (const tf of graph.tf ?? []) {
    const id = `tf:${tf.parent}->${tf.child}`
    nodes.push({
      id, type: 'ros', position: place(id),
      data: {
        label: `TF ${tf.parent}→${tf.child}`, kind: 'tf', state: tf.fresh ? 'ok' : 'bad', mark: mark(id),
        sub: tf.age_s === null ? 'unavailable' : tf.fresh ? `${Math.round(tf.age_s * 1000)} ms old` : `stale ${tf.age_s.toFixed(1)} s`,
      },
    })
    edges.push(edge(`${tf.broadcaster}>${id}`, tf.broadcaster, id, tf.fresh))
    if (tf.child === 'laser') edges.push(edge(`${id}>/obstacle_monitor`, id, '/obstacle_monitor', tf.fresh))
  }
  const topicOk = new Map(graph.topics.map((t) => [t.id, t.rate_hz === null || (t.min_rate_hz !== null && t.rate_hz >= t.min_rate_hz)]))
  const ids = new Set(nodes.map((n) => n.id))
  for (const e of graph.edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue
    const topic = e.kind === 'pub' ? e.target : e.source
    edges.push(edge(`${e.source}>${e.target}`, e.source, e.target, e.live, e.live && (topicOk.get(topic) ?? true)))
  }
  return { nodes, edges }
}

function edge(id: string, source: string, target: string, live: boolean, flowing = live): Edge {
  const color = !live ? '#ef4444' : flowing ? '#2dd4bf' : '#f59e0b'
  return {
    id, source, target, animated: live && flowing,
    style: { stroke: color, strokeWidth: 1.6, strokeDasharray: live ? undefined : '5 5', opacity: live ? 0.9 : 0.8 },
    markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
  }
}
