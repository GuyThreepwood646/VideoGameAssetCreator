// The whole-asset checks in three tiers (WP-95, UX-01, UX-05, UX-10): "Likely to show in the game", "Worth a look" and
// "Advice". The backend decides each check's tier (check_tiers.py); this only groups what it was sent. One line per
// check with its fix button; the full explanation is folded under "Why". A check never says the asset failed.
import { useState } from 'react'
import { Link } from 'react-router'
import { STAGES, type Asset, type CheckTier } from '../api'
import { TrafficDot } from '../components/ui'
import { checkFix, checkFixNote, fixTooltip, stagePlain } from '../help'
import { useRefData } from '../refdata'
import { itemsByTier, oneLine, TIER_ORDER, type AttentionItem } from './finish'
import type { FixRequest } from '../components/ui'

const FALLBACK_LABELS: Record<CheckTier, string> = { likely: 'Likely to show in the game', look: 'Worth a look', advice: 'Advice' }

function Row({ asset, item, stage, onFix, busy }: { asset: Asset; item: AttentionItem; stage?: string; onFix?: (fix: FixRequest) => void; busy?: boolean }) {
  const [open, setOpen] = useState(false)
  const fix = onFix ? checkFix(item.check) : null
  const line = oneLine(item.text)
  const note = checkFixNote(item.check)
  const more = line !== item.text || !!note || item.check.value !== undefined
  const near = item.check.near_miss
  return (
    <li className={'tier-check' + (open ? ' open' : '')}>
      <div className="tier-check-row">
        <TrafficDot state="warn" title={near ? 'a near miss' : 'worth knowing'} />
        <span className="tier-check-line">
          <span className="muted">Step {STAGES.indexOf(item.stage) + 1}, {stagePlain(item.stage, null)}: </span>
          {line}
          {near && <span className="muted"> (a near miss)</span>}
        </span>
        {fix && (
          <button type="button" className="small-btn" disabled={busy} onClick={() => onFix!(fix)} title={fixTooltip(fix)}>
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
          {line !== item.text && <p>{item.text}</p>}
          {note && <p className="muted">{note}</p>}
          <p className="muted">
            Measured {JSON.stringify(item.check.value)}; limit {JSON.stringify(item.check.limit)}. {item.stage !== stage && <Link to={`/assets/${asset.id}/${item.stage}`}>Go to this step</Link>}
          </p>
        </div>
      )}
    </li>
  )
}

export function TieredChecks({ asset, items, stage, onFix, busy }: { asset: Asset; items: AttentionItem[]; stage?: string; onFix?: (fix: FixRequest) => void; busy?: boolean }) {
  const { ref } = useRefData()
  const label = (t: CheckTier) => ref?.checkTiers?.tiers.find((x) => x.id === t)?.label ?? FALLBACK_LABELS[t]
  const by = itemsByTier(items)
  return (
    <div className="tiered-checks">
      {TIER_ORDER.filter((t) => by[t].length > 0).map((t) => (
        <section key={t} className={'tier tier-' + t} aria-label={label(t)}>
          <div className="field-label">
            {label(t)} <span className="muted">({by[t].length})</span>
          </div>
          <ul className="tier-list">
            {by[t].map((a, i) => (
              <Row key={a.stage + a.id + i} asset={asset} item={a} stage={stage} onFix={onFix} busy={busy} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
