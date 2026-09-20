export const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString('en-GB', { hour12: false })

export const sec = (s: number | null | undefined, d = 1) => (s === null || s === undefined ? '-' : `${s.toFixed(d)} s`)

export const actionVerb = (action: string) => (action === 'restart_component' ? 'restart' : action.replace(/_/g, ' '))
