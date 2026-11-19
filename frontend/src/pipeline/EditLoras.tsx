// Your LoRAs for one picture-edit use (refine, side views, pose redraw, face close-up): one row per LoRA with Off, Subtle,
// Normal or Strong, the LoRA's warning, and the notes the version records for LoRAs it could not use.
import { Info } from '../components/Info'
import type { EditChoice, EditLora } from '../editLoras'

const CHOICES: { id: EditChoice; label: string }[] = [
  { id: 'off', label: 'Off' },
  { id: 'subtle', label: 'Subtle' },
  { id: 'normal', label: 'Normal' },
  { id: 'strong', label: 'Strong' },
]

export function EditLoras({
  purpose,
  entries,
  choices,
  disabled,
  notes,
  onChange,
}: {
  purpose: string
  entries: EditLora[]
  choices: Record<string, EditChoice>
  disabled?: boolean
  notes?: string[]
  onChange: (id: string, choice: EditChoice) => void
}) {
  return (
    <div className="edit-loras" data-edit-loras>
      <div className="field-label">
        {purpose}
        <Info term="term.edit_lora" />
      </div>
      {entries.length === 0 && (
        <div className="muted small">You have no LoRAs for picture edits yet. Add one from the LoRA list on the Concept step, and choose Picture edits.</div>
      )}
      {entries.map((e) => {
        const current = choices[e.id] ?? 'off'
        return (
          <div key={e.id} className="edit-lora-row">
            <div className="row-between">
              <span>{e.label}</span>
              <span className="segmented" role="radiogroup" aria-label={`Strength of ${e.label} for ${purpose}`}>
                {CHOICES.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    role="radio"
                    aria-checked={current === c.id}
                    disabled={disabled}
                    className={'seg' + (current === c.id ? ' seg-on' : '')}
                    onClick={() => onChange(e.id, c.id)}
                  >
                    {c.label}
                  </button>
                ))}
              </span>
            </div>
            {e.warning && <div className="addon-note small">{e.warning}</div>}
          </div>
        )
      })}
      {(notes ?? []).map((n) => (
        <div key={n} className="muted small">
          {n}
        </div>
      ))}
    </div>
  )
}
