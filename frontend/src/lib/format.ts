export const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString('en-GB', { hour12: false })

export const sec = (s: number | null | undefined, d = 1) => (s === null || s === undefined ? '-' : `${s.toFixed(d)} s`)

const ACTION_VERBS: Record<string, string> = { restart_component: 'restart', set_parameter: 'reconfigure' }
export const actionVerb = (action: string) => ACTION_VERBS[action] ?? action.replace(/_/g, ' ')
