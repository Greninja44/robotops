import { useState } from 'react'

const SUGGESTIONS = [
  'Diagnose the robot.',
  "Why isn't my robot moving?",
  'Why is obstacle detection not working?',
  'Find the failure and recover the system.',
]

export function Composer({ busy, reason, onAsk }: { busy: boolean; reason?: string; onAsk: (q: string) => Promise<void> }) {
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const submit = async (text: string) => {
    if (!text.trim() || busy) return
    setErr(null)
    try { await onAsk(text.trim()); setQ('') } catch (e) { setErr((e as Error).message) }
  }
  return (
    <footer className="composer">
      <form onSubmit={(e) => { e.preventDefault(); submit(q) }}>
        <span className="prompt mono">&gt;</span>
        <input data-testid="query" value={q} onChange={(e) => setQ(e.target.value)} disabled={busy} spellCheck={false}
          placeholder={busy ? (reason ?? 'Investigation in progress…') : 'Diagnose why the robot stopped…'} />
        <button className="btn primary" type="submit" data-testid="run" disabled={busy || !q.trim()}>Run</button>
      </form>
      <div className="suggest">
        {SUGGESTIONS.map((s) => <button key={s} className="link" disabled={busy} onClick={() => submit(s)}>{s}</button>)}
        {err && <span className="form-err">{err}</span>}
      </div>
    </footer>
  )
}
