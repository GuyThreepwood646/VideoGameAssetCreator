import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react'
import type { Check, JobStatus, VersionStatus } from '../api'
import { checkFix, checkHelpKey, checkPassed, checkSentence, checkSeverity, fixTooltip, metricPlain, type CheckContext, type FixAction } from '../help'
import { useRefData } from '../refdata'
import { Info } from './Info'

export type Tone = 'neutral' | 'ok' | 'bad' | 'warn' | 'accent' | 'muted'

export function Chip({ tone = 'neutral', title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  )
}

const STATUS_WORDS: Record<string, string> = {
  done: 'finished',
  error: 'failed',
  running: 'working',
  queued: 'waiting',
  rejected: 'rejected',
  cancelled: 'stopped',
}

export function StatusBadge({ status, stopped, setAside }: { status: VersionStatus | JobStatus; stopped?: boolean; setAside?: boolean }) {
  if (setAside) {
    return (
      <Chip tone="muted" title="You stopped this before it finished and chose to leave it stopped. It is not complete.">
        stopped, set aside
      </Chip>
    )
  }
  if (stopped) {
    return (
      <Chip tone="warn" title="You stopped this before it finished. Resume runs it again with the same settings.">
        stopped
      </Chip>
    )
  }
  const tone: Tone =
    status === 'done' ? 'ok' : status === 'error' ? 'bad' : status === 'running' ? 'accent' : status === 'queued' ? 'warn' : 'muted'
  return (
    <Chip tone={tone} title={status}>
      {STATUS_WORDS[status] ?? status}
    </Chip>
  )
}

// Stop and Resume look and read the same wherever a step can be stopped or picked up again: a red square
// for Stop, a green triangle for Resume, always with the word next to the shape.
type StepButtonProps = { onClick: () => void; disabled?: boolean; small?: boolean; label?: string; title?: string }

export function StopButton({ onClick, disabled, small, label = 'Stop', title, stopping }: StepButtonProps & { stopping?: boolean }) {
  return (
    <button
      type="button"
      className={'stop-btn' + (small ? ' small-btn' : '')}
      disabled={disabled || stopping}
      onClick={onClick}
      title={title ?? 'Stops this step now. Everything you kept stays, and Resume picks it up again.'}
    >
      <span className="stop-icon" aria-hidden="true" />
      {stopping ? 'Stopping...' : label}
    </button>
  )
}

export function ResumeButton({ onClick, disabled, small, label = 'Resume', title, est }: StepButtonProps & { est?: string }) {
  return (
    <button
      type="button"
      className={'resume-btn' + (small ? ' small-btn' : '')}
      disabled={disabled}
      onClick={onClick}
      title={title ?? 'Runs the stopped step again from its start, with the same settings.'}
    >
      <span className="resume-icon" aria-hidden="true" />
      {label}
      {est ? <span className="btn-est">{est}</span> : null}
    </button>
  )
}

export function fmtNum(n: number): string {
  if (Number.isInteger(n)) return n.toLocaleString('en-US')
  return n.toFixed(Math.abs(n) < 1 ? 2 : 1)
}

export function isCheckList(x: unknown): x is Check[] {
  return Array.isArray(x) && x.every((c) => c && typeof c === 'object' && ('name' in c || 'id' in c) && ('passed' in c || 'ok' in c))
}

// Renders scalar metrics as chips (plain names, technical key in the title).
// Nested objects are summarised ("outline match avg 0.9") or skipped.
export function MetricChips({ metrics, max = 12, compact = false }: { metrics: Record<string, unknown>; max?: number; compact?: boolean }) {
  const out: ReactNode[] = []
  for (const [key, value] of Object.entries(metrics)) {
    if (out.length >= max) break
    if (key === 'checks' && isCheckList(value)) {
      const passed = value.filter((c) => checkPassed(c)).length
      out.push(
        <Chip key={key} tone={passed === value.length ? 'ok' : 'bad'} title={value.map((c) => `${checkPassed(c) ? 'pass' : 'FAIL'} ${c.id ?? c.name}`).join('\n')}>
          checks {passed}/{value.length}
        </Chip>,
      )
      continue
    }
    if (typeof value === 'number') {
      out.push(
        <Chip key={key} title={key}>
          <span className="chip-key">{metricPlain(key)}</span> {fmtNum(value)}
        </Chip>,
      )
    } else if (typeof value === 'boolean') {
      out.push(
        <Chip key={key} tone={value ? 'ok' : 'bad'} title={key}>
          {metricPlain(key)} {value ? 'yes' : 'no'}
        </Chip>,
      )
    } else if (typeof value === 'string' && value.length < 32) {
      out.push(
        <Chip key={key} title={key}>
          <span className="chip-key">{metricPlain(key)}</span> {value}
        </Chip>,
      )
    } else if (value && typeof value === 'object' && !Array.isArray(value)) {
      const entries = Object.values(value as Record<string, unknown>)
      const nums = entries.filter((v) => typeof v === 'number') as number[]
      if (nums.length === entries.length && nums.length > 0) {
        const avg = nums.reduce((a, b) => a + b, 0) / nums.length
        out.push(
          <Chip key={key} title={`${key}: ${Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k} ${fmtNum(v as number)}`).join(', ')}`}>
            <span className="chip-key">{metricPlain(key)}</span> avg {fmtNum(avg)}
          </Chip>,
        )
      }
    }
  }
  if (!out.length) return null
  return <div className={'chips' + (compact ? ' chips-compact' : '')}>{out}</div>
}

// Raw technical list (used inside Details).
export function ChecksList({ checks }: { checks: Check[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => {
        const ok = checkPassed(c)
        return (
          <li key={c.id ?? c.name} className={ok ? 'pass' : 'fail'}>
            <span className={ok ? 'pass-mark' : 'fail-mark'}>{ok ? 'PASS' : 'FAIL'}</span> {c.id ?? c.name}
            {c.message ? <span className="muted">: {c.message}</span> : null}
          </li>
        )
      })}
    </ul>
  )
}

export type TrafficState = 'ok' | 'warn' | 'bad' | 'none'

export function TrafficDot({ state, title }: { state: TrafficState; title?: string }) {
  return <span className={'traffic traffic-' + state} title={title} aria-label={title} role={title ? 'img' : undefined} />
}

// What a Fix button asks for: re-run a stage with params, or open a tool (`ui`, 2.5).
export type FixRequest = FixAction

// Plain-language checks: one sentence each with a traffic-light dot, an Info icon
// and a Fix button (labelled by the check's fix_label) when the app knows how to fix a failure.
// A passing check reads as a pass; failures come first.
export function PlainChecks({
  checks,
  ctx,
  onFix,
  busy,
  compact,
}: {
  checks: Check[]
  ctx?: CheckContext
  onFix?: (fix: FixRequest, check: Check) => void
  busy?: boolean
  compact?: boolean
}) {
  const [showPassed, setShowPassed] = useState(!compact)
  const { ref } = useRefData()
  const failing = checks.filter((c) => !checkPassed(c))
  const passing = checks.filter((c) => checkPassed(c))
  const render = (c: Check) => {
    const ok = checkPassed(c)
    const sev = checkSeverity(c)
    const fix = ok ? null : checkFix(c)
    const key = checkHelpKey(c)
    const state: TrafficState = ok ? 'ok' : sev === 'warn' ? 'warn' : 'bad'
    return (
      <li key={c.id ?? c.name} className={'plain-check ' + state}>
        <TrafficDot state={state} title={ok ? 'passed' : sev === 'warn' ? 'warning' : 'needs attention'} />
        <span className="plain-check-text" title={`Technical name: ${c.id ?? c.name}`}>
          {checkSentence(c, ctx)}
          {key && <Info term={'term.' + key} />}
        </span>
        {fix && onFix && (
          <button type="button" className="small-btn" disabled={busy} onClick={() => onFix(fix, c)} title={fixTooltip(fix, ref?.stages)}>
            {fix.label}
          </button>
        )}
      </li>
    )
  }
  return (
    <div className="plain-checks">
      <ul className="checks">
        {failing.map(render)}
        {showPassed && passing.map(render)}
      </ul>
      {compact && passing.length > 0 && (
        <button type="button" className="link small" onClick={() => setShowPassed((v) => !v)}>
          {showPassed ? 'Hide' : 'Show'} {passing.length} passed check{passing.length > 1 ? 's' : ''}
        </button>
      )}
      {checks.length === 0 && <span className="muted small">No checks for this version.</span>}
    </div>
  )
}

// A collapsible area ("Details", "Advanced"). Closed by default.
export function Disclosure({
  label,
  children,
  defaultOpen = false,
  className = '',
  open: controlled,
  onToggle,
}: {
  label: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  className?: string
  open?: boolean
  onToggle?: (open: boolean) => void
}) {
  const [inner, setInner] = useState(defaultOpen)
  const open = controlled ?? inner
  const toggle = () => {
    const next = !open
    if (controlled === undefined) setInner(next)
    onToggle?.(next)
  }
  return (
    <div className={'disclosure ' + (open ? 'open ' : '') + className}>
      <button type="button" className="disclosure-btn" aria-expanded={open} onClick={toggle}>
        <span className="disclosure-caret" aria-hidden="true" />
        {label}
      </button>
      {open && <div className="disclosure-body">{children}</div>}
    </div>
  )
}

export function Field({ label, hint, children, inline = false, term }: { label: ReactNode; hint?: ReactNode; children: ReactNode; inline?: boolean; term?: string }) {
  return (
    <label className={'field' + (inline ? ' field-inline' : '')}>
      <span className="field-label">
        {label}
        {term && <Info term={term} />}
      </span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  )
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null
  return <div className="error">{error instanceof Error ? error.message : String(error)}</div>
}

// Text chips with an input: Enter adds, Backspace on empty removes the last.
export function ChipInput({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState('')
  const add = () => {
    const t = text.trim()
    if (!t) return
    if (!value.includes(t)) onChange([...value, t])
    setText('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      add()
    } else if (e.key === 'Backspace' && !text && value.length) {
      onChange(value.slice(0, -1))
    }
  }
  return (
    <div className="chip-input">
      {value.map((v) => (
        <span key={v} className="chip chip-accent">
          {v}
          <button type="button" className="chip-x" aria-label={`remove ${v}`} onClick={() => onChange(value.filter((x) => x !== v))}>
            x
          </button>
        </span>
      ))}
      <input value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onBlur={add} />
    </div>
  )
}

// Enter submits, Shift+Enter inserts a newline.
export function SubmitTextarea(props: React.ComponentProps<'textarea'> & { onSubmit?: () => void }) {
  const { onSubmit, onKeyDown, ...rest } = props
  return (
    <textarea
      {...rest}
      onKeyDown={(e) => {
        onKeyDown?.(e)
        if (e.key === 'Enter' && !e.shiftKey && !e.defaultPrevented) {
          e.preventDefault()
          if (onSubmit) onSubmit()
          else e.currentTarget.form?.requestSubmit()
        }
      }}
    />
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap">
      <span className="spinner" /> {label}
    </span>
  )
}

export function Checker({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={'checker ' + className}>{children}</div>
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: unknown; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    // One flag per run: a slower earlier run that resolves after a newer one started is ignored.
    let alive = true
    setLoading(true)
    fn().then(
      (d) => {
        if (!alive) return
        setData(d)
        setError(null)
        setLoading(false)
      },
      (e) => {
        if (!alive) return
        setError(e)
        setLoading(false)
      },
    )
    return () => {
      alive = false
    }
  }, [...deps, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, loading, reload: () => setTick((t) => t + 1) }
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${Math.round(s)}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export function duration(a: string | null | undefined, b: string | null | undefined): string {
  if (!a) return ''
  const end = b ? new Date(b).getTime() : Date.now()
  const s = Math.max(0, (end - new Date(a).getTime()) / 1000)
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}
