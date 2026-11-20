// Which kinds of assets, and for a picture-edit LoRA which steps, one of your LoRAs starts on in (System, Your library;
// backend lora_defaults.py). A step's own settings can still switch it on or off for one asset.
import { useState } from 'react'
import { api, type AddonEntry } from '../api'
import { ErrorText } from '../components/ui'
import { EDIT_KNOBS, EDIT_KNOB_LABELS, isEditLora } from '../editLoras'
import { useRefData } from '../refdata'

// The kinds of assets a LoRA starts on for, in the words the app uses for them.
function kinds(ids: string[], labels: Map<string, string>): string {
  return ids.map((c) => labels.get(c) ?? c).join(', ')
}

// The one plain line under a LoRA in Your library.
export function startOnSentence(a: Pick<AddonEntry, 'stage' | 'start_on'>, labels: Map<string, string>): string {
  const so = a.start_on
  if (!so || so.categories.length === 0) return 'Starts off everywhere: switch it on in a step when you want it.'
  const where = kinds(so.categories, labels)
  if (!isEditLora(a)) return `Starts on for the first picture of: ${where}.`
  const steps = EDIT_KNOBS.filter((k) => so.uses.includes(k)).map((k) => EDIT_KNOB_LABELS[k])
  return `Starts on for: ${where}; in ${steps.join(', ')}.`
}

export function useCategoryLabels(): Map<string, string> {
  const { ref } = useRefData()
  return new Map((ref?.categories ?? []).map((c) => [c.id, c.label]))
}

export function StartOnForm({ entry, onSaved, onCancel }: { entry: AddonEntry; onSaved: () => void; onCancel: () => void }) {
  const { ref } = useRefData()
  const edit = isEditLora(entry)
  const [cats, setCats] = useState<string[]>(entry.start_on?.categories ?? [])
  const [uses, setUses] = useState<string[]>(entry.start_on?.uses ?? (edit ? ['view_addons'] : []))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const toggle = (list: string[], id: string, on: boolean) => (on ? [...list, id] : list.filter((x) => x !== id))
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.setStartOn(entry.id, { categories: cats, uses: edit ? uses : [] })
      onSaved()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <fieldset className="start-on-form" data-start-on={entry.id}>
      <legend>Where {entry.label} starts on</legend>
      <p className="muted small">
        New steps of these kinds of assets start with it on. You can still switch it off, or on, in any step of any asset.
      </p>
      <div className="field-label small">Kinds of assets</div>
      <div className="start-on-choices">
        {(ref?.categories ?? []).map((c) => (
          <label key={c.id} className="small">
            <input type="checkbox" checked={cats.includes(c.id)} disabled={busy} onChange={(e) => setCats(toggle(cats, c.id, e.target.checked))} /> {c.label}
          </label>
        ))}
      </div>
      {edit && (
        <>
          <div className="field-label small">In these steps</div>
          <div className="start-on-choices">
            {EDIT_KNOBS.map((k) => (
              <label key={k} className="small">
                <input type="checkbox" checked={uses.includes(k)} disabled={busy} onChange={(e) => setUses(toggle(uses, k, e.target.checked))} /> {EDIT_KNOB_LABELS[k]}
              </label>
            ))}
          </div>
          <p className="muted small">The pose redraw and the face close-up are made for characters only.</p>
        </>
      )}
      <ErrorText error={error} />
      <div className="row-gap">
        <button type="button" disabled={busy} onClick={() => void save()}>
          Save
        </button>
        <button type="button" className="link" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </fieldset>
  )
}
