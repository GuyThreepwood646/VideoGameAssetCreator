// Fine-tune add-ons (concept, Advanced): one row per add-on that works with the chosen painter, with an on/off
// switch, Subtle / Normal / Strong, and the one-line meaning. Required add-ons stay on and show a lock.
// For the chosen painter the list shows the tested add-ons, then "Yours" (the person's own, each with Remove), then
// "Add your own add-on" (AddMyAddon.tsx). The turnaround's own helpers are listed read-only.
import { useState } from 'react'
import { api, type AddonChoice, type AddonEntry, type Recipe, type StrengthName, type TurnMethod, type Version } from '../api'
import { Info } from '../components/Info'
import { ErrorText } from '../components/ui'
import { joinAnd, modelLabel, STRENGTH_LABELS } from '../help'
import { addonLocked, strengthsEqual } from '../recipe'
import { AddMyAddon, reloadMyAddons } from './AddMyAddon'

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
  painter,
}: {
  rows: AddonRow[]
  disabled: boolean
  onChange: (id: string, choice: AddonChoice) => void
  notUsed?: string | null
  // The chosen painter. With one, the list also offers your own add-ons and "Add your own add-on".
  painter?: string
}) {
  const [adding, setAdding] = useState(false)
  // The line after adding one: "switched on" holds only while that add-on is still listed (Remove clears it).
  const [added, setAdded] = useState<{ id: string; text: string; listed: boolean } | null>(null)
  if (!rows.length && !painter) return null
  const tested = rows.filter((r) => !r.entry.mine)
  const yours = rows.filter((r) => r.entry.mine)
  const painterName = painter ? modelLabel(painter) : ''
  const onAdded = (a: AddonEntry) => {
    setAdding(false)
    if (painter && a.base_models.includes(painter)) {
      onChange(a.id, a.default_strength)
      setAdded({ id: a.id, text: `${a.label} is added and switched on. Press Apply and re-run to paint with it.`, listed: true })
    } else setAdded({ id: a.id, text: `${a.label} is added. It shows up when you choose a model it was made for.`, listed: false })
  }
  return (
    <div className="addons">
      <div className="field-label">
        LoRAs (style add-ons){painterName ? ` for ${painterName}` : ''}
        <Info term="term.addons" />
      </div>
      {tested.length > 0 ? (
        <ul className="addon-list">
          {tested.map((r) => (
            <AddonRowView key={r.entry.id} row={r} disabled={disabled} onChange={(c) => onChange(r.entry.id, c)} />
          ))}
        </ul>
      ) : (
        painter && <div className="muted small">No tested LoRAs for this model yet. You can add your own.</div>
      )}
      {yours.length > 0 && (
        <>
          <div className="addon-section-title small">Yours</div>
          <ul className="addon-list">
            {yours.map((r) => (
              <AddonRowView key={r.entry.id} row={r} disabled={disabled} onChange={(c) => onChange(r.entry.id, c)} removable />
            ))}
          </ul>
        </>
      )}
      {painter &&
        (adding ? (
          <AddMyAddon painter={painter} onAdded={onAdded} onClose={() => setAdding(false)} />
        ) : (
          <div className="row-gap addon-add-row">
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                setAdded(null)
                setAdding(true)
              }}
            >
              Add your own LoRA
            </button>
            {added && (!added.listed || yours.some((r) => r.entry.id === added.id)) && (
              <span className="muted small" role="status">
                {added.text}
              </span>
            )}
          </div>
        ))}
      {notUsed && <div className="muted small">{notUsed}</div>}
    </div>
  )
}

// One quiet line for the simple view, so people know add-ons exist without opening Advanced.
export function AddonsHint({ rows, painter, onShow }: { rows: AddonRow[]; painter: string; onShow: () => void }) {
  const on = rows.filter((r) => r.on).length
  const name = modelLabel(painter)
  const text =
    rows.length === 0
      ? `LoRAs (style add-ons): none tested for ${name} yet; you can add your own.`
      : on > 0
        ? `LoRAs (style add-ons): ${on} on, ${rows.length - on} more you can switch on for ${name}.`
        : `LoRAs (style add-ons): ${rows.length} you can switch on for ${name}.`
  return (
    <div className="muted small addon-hint">
      {text}{' '}
      <button type="button" className="link" onClick={onShow}>
        Show LoRAs
      </button>
    </div>
  )
}

function AddonRowView({
  row,
  disabled,
  onChange,
  readOnly = false,
  removable = false,
}: {
  row: AddonRow
  disabled: boolean
  onChange?: (c: AddonChoice) => void
  readOnly?: boolean
  removable?: boolean
}) {
  const { entry: a, locked, on, strength } = row
  const fixedStrength = strengthsEqual(a)
  const reason = a.stage === 'turnaround' ? 'Set by How to turn the views' : 'Part of this model, cannot be switched off'
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
            title={locked ? reason : on ? 'Switch this LoRA off' : 'Switch this LoRA on'}
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
                title={on ? undefined : 'Switch the LoRA on to choose a strength'}
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
      {removable && <RemoveMine entry={a} disabled={disabled} />}
      {locked && (
        <div className="muted small addon-lock">
          <Lock /> {reason}
        </div>
      )}
    </li>
  )
}

// Remove one of your own add-ons from the list. The file stays in the add-on folder.
function RemoveMine({ entry, disabled }: { entry: AddonEntry; disabled: boolean }) {
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const remove = async () => {
    setError(null)
    try {
      await api.removeMyAddon(entry.id)
      await reloadMyAddons()
    } catch (e) {
      setError(e)
    }
  }
  return (
    <div className="addon-mine-actions small">
      {asking ? (
        <>
          <span>Take it off your list? The file stays in the LoRA folder, so you can set it up again later.</span>
          <button type="button" onClick={remove}>
            Remove
          </button>
          <button type="button" onClick={() => setAsking(false)}>
            Keep
          </button>
        </>
      ) : (
        <button type="button" className="link" disabled={disabled} onClick={() => setAsking(true)}>
          Remove
        </button>
      )}
      <ErrorText error={error} />
    </div>
  )
}

// The turnaround's helpers, read-only (turnaround Fine-tune, Advanced): the LoRAs the chosen way of turning the views
// loads. They change with "How to turn the views", not one by one: two view LoRAs are never loaded together.
export function TurnaroundAddons({ entries, method = 'standard' }: { entries: AddonEntry[]; method?: TurnMethod }) {
  const rows = entries.filter((e) => e.stage === 'turnaround' && (!e.turn_method || e.turn_method === method))
  if (!rows.length) return null
  return (
    <div className="addons">
      <div className="field-label">
        LoRAs (style add-ons)
        <Info term="term.addons" />
      </div>
      <ul className="addon-list">
        {rows.map((e) => (
          <AddonRowView key={e.id} readOnly disabled row={{ entry: e, locked: true, on: true, strength: e.strength ?? e.default_strength }} />
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
  return `${ids.length === 1 ? '1 style add-on was' : `${ids.length} style add-ons were`} skipped because ${ids.length === 1 ? 'it does' : 'they do'} not fit this painter (${joinAnd(ids.map(label))}).`
}
