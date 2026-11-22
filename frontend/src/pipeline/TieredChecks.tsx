// The whole-asset checks in three tiers (WP-95, UX-01, UX-05, UX-10): "Likely to show in the game", "Worth a look" and
// "Advice". The backend decides each check's tier (check_tiers.py); this only groups what it was sent. A check never says
// the asset failed.
import { Link } from 'react-router'
import { STAGES, type Asset } from '../api'
import { CheckLine, TierHeading } from '../components/CheckLine'
import type { FixRequest } from '../components/ui'
import { stagePlain } from '../help'
import { itemsByTier, TIER_ORDER, type AttentionItem } from './finish'

export function TieredChecks({ asset, items, stage, onFix, busy }: { asset: Asset; items: AttentionItem[]; stage?: string; onFix?: (fix: FixRequest) => void; busy?: boolean }) {
  const by = itemsByTier(items)
  return (
    <div className="tiered-checks">
      {TIER_ORDER.filter((t) => by[t].length > 0).map((t) => (
        <section key={t} className={'tier tier-' + t} aria-label={t}>
          <TierHeading tier={t} count={by[t].length} />
          <ul className="tier-list">
            {by[t].map((a, i) => (
              <CheckLine
                key={a.stage + a.id + i}
                check={a.check}
                ctx={{ platform: asset.platform_target, category: asset.category }}
                prefix={<span className="muted">Step {STAGES.indexOf(a.stage) + 1}: </span>}
                onFix={onFix ? (f) => onFix(f) : undefined}
                busy={busy}
                why={
                  <>
                    {stagePlain(a.stage, null)}. {a.stage !== stage && <Link to={`/assets/${asset.id}/${a.stage}`}>Go to this step</Link>}
                  </>
                }
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
