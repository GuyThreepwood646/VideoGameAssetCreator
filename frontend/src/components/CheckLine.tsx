// One check as one short line (WP-95): a dot (red only for "likely to show in the game"), the line, its fix button and a
// "Why" that unfolds the full sentence, the fix note and the measured value. Used by the step pages' check lists and the
// finished page's tiers, so a check reads the same everywhere.
import { useState, type ReactNode } from 'react'
import type { Check, CheckTier } from '../api'
import { checkFix, checkFixNote, checkSentence, fixTooltip, type CheckContext, type FixAction } from '../help'
import { useRefData } from '../refdata'
import { oneLine } from '../pipeline/finish'
import { TrafficDot } from './TrafficDot'

const FALLBACK_LABELS: Record<CheckTier, string> = { likely: 'Likely to show in the game', look: 'Worth a look', advice: 'Advice' }

export function tierLabel(ref: { checkTiers: { tiers: { id: CheckTier; label: string }[] } | null } | null, t: CheckTier): string {
  return ref?.checkTiers?.tiers.find((x) => x.id === t)?.label ?? FALLBACK_LABELS[t]
}

export function TierHeading({ tier, count }: { tier: CheckTier; count: number }) {
  const { ref } = useRefData()
  return (
    <div className="field-label">
      {tierLabel(ref, tier)} <span className="muted">({count})</span>
    </div>
  )
}

export function CheckLine({ check, ctx, prefix, onFix, busy, why }: { check: Check; ctx?: CheckContext; prefix?: ReactNode; onFix?: (fix: FixAction, check: Check) => void; busy?: boolean; why?: ReactNode }) {
  const [open, setOpen] = useState(false)
  const { ref } = useRefData()
  const text = checkSentence(check, ctx)
  const line = check.headline ?? oneLine(text)
  const fix = onFix ? checkFix(check) : null
  const note = checkFixNote(check)
  const tier = check.tier ?? 'look'
  const near = check.near_miss
  const more = true
  return (
    <li className={'tier-check' + (open ? ' open' : '')}>
      <div className="tier-check-row">
        <TrafficDot state={tier === 'likely' ? 'bad' : 'warn'} title={near ? 'a near miss' : tierLabel(ref, tier)} />
        <span className="tier-check-line">
          {prefix}
          {line}
          {near && <span className="muted"> (a near miss)</span>}
        </span>
        {fix && onFix && (
          <button type="button" className="small-btn" disabled={busy} onClick={() => onFix(fix, check)} title={fixTooltip(fix, ref?.stages)}>
            {fix.label}
          </button>
        )}
        {more && (
          <button type="button" className="link small" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? 'Hide why' : 'Why'}
          </button>
        )}
      </div>
      {open && (
        <div className="tier-check-why small">
          {line !== text && <p>{text}</p>}
          {note && <p className="muted">{note}</p>}
          <p className="muted">
            Measured {JSON.stringify(check.value)}; limit {JSON.stringify(check.limit)}. {why}
          </p>
        </div>
      )}
    </li>
  )
}
