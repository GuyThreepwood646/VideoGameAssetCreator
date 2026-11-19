// Your LoRAs for one picture-edit use (refine, side views, pose redraw, face close-up): one row per LoRA with Off, Subtle,
// Normal or Strong, the LoRA's warning, and the notes the version records for LoRAs it could not use.
import { Info } from '../components/Info'
import { EDIT_LORA_LIMIT, type EditChoice, type EditLora, warningOf } from '../editLoras'

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
  // Every LoRA chosen for this use counts, shown or not, as the backend counts them.
  const active = Object.values(choices).filter((c) => c !== 'off').length
  const full = active >= EDIT_LORA_LIMIT
  return (
    <div className="edit-loras" data-edit-loras>
      <div className="field-label">
        {purpose}
        <Info term="term.edit_lora" />
      </div>
      {entries.length === 0 && (
        <div className="muted small">You have no LoRAs for picture edits yet. Add one from the LoRA list on the Concept step, and choose Picture edits.</div>
      )}
      {entries.length > 0 && (
        <div className="muted small">
          Two at a time is the limit for one step.{full ? ' Turn one off to choose another.' : ''}
        </div>
      )}
      {entries.map((e) => {
        const current = choices[e.id] ?? 'off'
        // At the limit, a LoRA that is off cannot be turned on; Off stays available on every row.
        const blocked = full && current === 'off'
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
                    disabled={disabled || (blocked && c.id !== 'off')}
                    className={'seg' + (current === c.id ? ' seg-on' : '')}
                    onClick={() => onChange(e.id, c.id)}
                  >
                    {c.label}
                  </button>
                ))}
              </span>
            </div>
            {warningOf(e) && <div className="addon-note small">{warningOf(e)}</div>}
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
