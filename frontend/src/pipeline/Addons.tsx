// Fine-tune add-ons (concept, Advanced): one row per add-on that works with the chosen painter, with an on/off
// switch, Subtle / Normal / Strong, and the one-line meaning. Required add-ons stay on and show a lock.
// The turnaround's own helpers are listed read-only.
import { type AddonChoice, type AddonEntry, type Recipe, type StrengthName, type Version } from '../api'
import { Info } from '../components/Info'
import { joinAnd, STRENGTH_LABELS } from '../help'
import { addonLocked, strengthsEqual } from '../recipe'

const STEPS: StrengthName[] = ['subtle', 'normal', 'strong']

export type AddonRow = { entry: AddonEntry; locked: boolean; on: boolean; strength: StrengthName }

// The rows for a painter: every catalog entry that works with it, with the state the recipe gives it (its
// addon_entries on and strength, and its default add-ons list, which also holds for another painter the add-on
// works with), then what this version chose.
export function addonRows(catalog: AddonEntry[], recipe: Pick<Recipe, 'addons' | 'addon_entries'> | null | undefined, painter: string, chosen: Record<string, AddonChoice>): AddonRow[] {
  const fromRecipe = new Map((recipe?.addon_entries ?? []).map((e) => [e.id, e]))
  const defaults = new Map((recipe?.addons ?? []).map((d) => [d.id, d.strength]))
  return catalog
    .filter((a) => (a.stage ?? 'concept') === 'concept' && (a.base_models ?? []).includes(painter))
    .map((a) => {
      const r = fromRecipe.get(a.id)
      const recipeOn = (r?.on ?? false) || defaults.has(a.id)
      const locked = addonLocked(a, painter)
      const base: StrengthName = r?.strength ?? defaults.get(a.id) ?? a.default_strength ?? 'normal'
      const pick = chosen[a.id]
      const on = locked ? true : pick ? pick !== 'off' : recipeOn
      const strength: StrengthName = pick && pick !== 'off' ? pick : base
      return { entry: { ...a, help: a.help ?? {} }, locked, on, strength }
    })
}

function Lock() {
  return (
    <svg className="lock-icon" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <rect x="2" y="5.5" width="8" height="5.5" rx="1" fill="currentColor" />
      <path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  )
}

export function AddonsList({
  rows,
  disabled,
  onChange,
  notUsed,
}: {
  rows: AddonRow[]
  disabled: boolean
  onChange: (id: string, choice: AddonChoice) => void
  notUsed?: string | null
}) {
  if (!rows.length) return null
  return (
    <div className="addons">
      <div className="field-label">
        Add-ons
        <Info term="term.addons" />
      </div>
      <ul className="addon-list">
        {rows.map((r) => (
          <AddonRowView key={r.entry.id} row={r} disabled={disabled} onChange={(c) => onChange(r.entry.id, c)} />
        ))}
      </ul>
      {notUsed && <div className="muted small">{notUsed}</div>}
    </div>
  )
}

function AddonRowView({ row, disabled, onChange, readOnly = false }: { row: AddonRow; disabled: boolean; onChange?: (c: AddonChoice) => void; readOnly?: boolean }) {
  const { entry: a, locked, on, strength } = row
  const fixedStrength = strengthsEqual(a)
  const reason = a.stage === 'turnaround' ? 'Part of the turnaround, cannot be switched off' : 'Part of this painter, cannot be switched off'
  const label = a.label || a.id
  return (
    <li className={'addon-row' + (on ? '' : ' addon-off')}>
      <div className="addon-head">
        {readOnly ? (
          <span className="addon-fixed small">
            {on ? 'On' : 'Off'}
            {on && !fixedStrength ? `, ${STRENGTH_LABELS[strength].toLowerCase()}` : ''}
          </span>
        ) : (
          <button
            type="button"
            role="switch"
            aria-checked={on}
            aria-label={`${label}: ${on ? 'on' : 'off'}`}
            className={'switch' + (on ? ' switch-on' : '')}
            disabled={disabled || locked}
            title={locked ? reason : on ? 'Switch this add-on off' : 'Switch this add-on on'}
            onClick={() => onChange?.(on ? 'off' : strength)}
          >
            <span className="switch-knob" />
            <span className="switch-label">{on ? 'On' : 'Off'}</span>
          </button>
        )}
        <span className="addon-label">
          {label}
          <Info entry={{ plain: label, meaning: a.help?.meaning ?? '', why: a.help?.why ?? '', technical: a.help?.technical ?? a.id }} />
        </span>
        {!readOnly && !fixedStrength && (
          <span className="segmented" role="radiogroup" aria-label={`${label}: strength`}>
            {STEPS.map((s) => (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={on && strength === s}
                className={'seg' + (on && strength === s ? ' seg-on' : '')}
                disabled={disabled || !on}
                title={on ? undefined : 'Switch the add-on on to choose a strength'}
                onClick={() => onChange?.(s)}
              >
                {STRENGTH_LABELS[s]}
              </button>
            ))}
            <Info term={'term.strength_' + (on ? strength : 'normal')} />
          </span>
        )}
      </div>
      {a.help?.meaning && <div className="muted small addon-meaning">{a.help.meaning}</div>}
      {locked && (
        <div className="muted small addon-lock">
          <Lock /> {reason}
        </div>
      )}
    </li>
  )
}

// The turnaround's helpers, read-only (turnaround Fine-tune, Advanced).
export function TurnaroundAddons({ entries }: { entries: AddonEntry[] }) {
  const rows = entries.filter((e) => e.stage === 'turnaround')
  if (!rows.length) return null
  return (
    <div className="addons">
      <div className="field-label">
        Add-ons
        <Info term="term.addons" />
      </div>
      <ul className="addon-list">
        {rows.map((e) => (
          <AddonRowView key={e.id} readOnly disabled row={{ entry: e, locked: e.required, on: e.on ?? e.required, strength: e.strength ?? e.default_strength }} />
        ))}
      </ul>
    </div>
  )
}

// "Not used: ..." after a run: add-ons the version asked for that the painter could not use or the app does not know.
export function addonsNotUsed(v: Version | undefined | null, catalog: AddonEntry[] | undefined): string | null {
  if (!v) return null
  const unknown = Array.isArray(v.metrics.addons_unknown) ? (v.metrics.addons_unknown as unknown[]).map(String) : []
  const ignored = Array.isArray(v.metrics.addons_ignored)
    ? (v.metrics.addons_ignored as unknown[]).map((x) => (x && typeof x === 'object' ? String((x as { id?: unknown }).id ?? '') : String(x))).filter(Boolean)
    : []
  const ids = [...new Set([...ignored, ...unknown])]
  if (!ids.length) return null
  const label = (id: string) => catalog?.find((a) => a.id === id)?.label ?? id
  return `Not used: ${joinAnd(ids.map(label))}.`
}
