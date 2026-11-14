import { useEffect, useMemo, useState } from 'react'
import { api, type Check, type FixPlan, type Version } from '../api'
import { Disclosure } from '../components/ui'
import { checkPassed, FIX_TICK_LABELS } from '../help'

// The first picture's failed checks that one redraw can fix together: a tick box for each, one button that asks the picture
// editor for all the ticked changes at once (the backend joins their wording, POST /api/versions/{vid}/fix-plan). The
// current version stays; the redraw is a new version that records which checks it was made to fix.

export function tickable(checks: Check[]): Check[] {
  return checks.filter((c) => !checkPassed(c) && !!c.fix_snippet)
}

export function FixTicks({
  version,
  checks,
  busy,
  onRun,
}: {
  version: Version
  checks: Check[]
  busy: boolean
  onRun: (ids: string[], instruction: string) => void | Promise<void>
}) {
  const list = useMemo(() => tickable(checks), [checks])
  const [ticked, setTicked] = useState<string[]>([])
  const [fetched, setFetched] = useState<{ key: string; plan: FixPlan } | null>(null)
  const [planError, setPlanError] = useState(false)

  // A different version is a different set of problems: start with nothing ticked.
  useEffect(() => {
    setTicked([])
    setFetched(null)
  }, [version.id])

  const key = ticked.join('|')
  useEffect(() => {
    if (ticked.length === 0) {
      setFetched(null)
      setPlanError(false)
      return
    }
    let cancelled = false
    api
      .fixPlan(version.id, ticked)
      .then((p) => {
        if (!cancelled) {
          setFetched({ key, plan: p })
          setPlanError(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFetched(null)
          setPlanError(true)
        }
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version.id, key])

  if (list.length === 0) return null
  const toggle = (id: string) => setTicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  // The wording shown always belongs to the boxes ticked now; while a new plan is on its way there is none.
  const plan = fetched && fetched.key === key ? fetched.plan : null
  const used = plan?.used ?? []
  const n = used.length
  const text = plan?.instruction ?? null

  return (
    <div className="fix-ticks" aria-label="Fix several problems in one redraw">
      <div className="field-label">Fix several in one redraw</div>
      <div className="muted small">
        Tick the problems to fix. One redraw changes only those things and keeps the rest. It makes a new version; this one stays.
      </div>
      <ul className="fix-ticks-list">
        {list.map((c) => {
          const id = c.id ?? c.name
          const isTicked = ticked.includes(id)
          const dropped = plan?.dropped.find((d) => d.check === id)
          return (
            <li key={id}>
              <label className="fix-tick">
                <input type="checkbox" checked={isTicked} disabled={busy} onChange={() => toggle(id)} />
                <span>{FIX_TICK_LABELS[id] ?? c.help ?? id}</span>
              </label>
              {isTicked && dropped && <div className="muted small fix-tick-note">{dropped.text}</div>}
            </li>
          )
        })}
      </ul>
      {planError && <div className="muted small">The redraw wording could not be made. Try again.</div>}
      {text && (
        <Disclosure label="What the picture editor will be asked">
          <p className="small fix-tick-words">{text}</p>
        </Disclosure>
      )}
      <button type="button" className="small-btn" disabled={busy || !text || n === 0} onClick={() => void onRun(used, text ?? '')}>
        {n === 0 ? 'Redraw with the ticked fixes' : n === 1 ? 'Redraw with 1 fix' : `Redraw with ${n} fixes`}
      </button>
      {ticked.length > 0 && n === 0 && !planError && plan && <div className="muted small">None of the ticked problems can be fixed this way.</div>}
    </div>
  )
}
