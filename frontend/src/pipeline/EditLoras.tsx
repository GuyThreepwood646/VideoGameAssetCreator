// Your LoRAs for one picture-edit use (refine, side views, pose redraw, face close-up): one row per LoRA with an On or Off
// switch and, when on, Subtle, Normal or Strong; a plain line saying which are on for this run; the LoRA's warning; a note
// on the ones that start on for this kind of asset (System, Your library); and the notes the version records for LoRAs it
// could not use.
import { Info } from '../components/Info'
import { EDIT_LORA_LIMIT, type EditChoice, type EditLora, warningOf } from '../editLoras'
import { STRENGTH_LABELS } from '../help'

const STEPS: Exclude<EditChoice, 'off'>[] = ['subtle', 'normal', 'strong']

// "On for this run: Ink look (Normal), Soft light (Subtle)." or the sentence for none.
export function onForThisRun(entries: EditLora[], choices: Record<string, EditChoice>): string {
  const on = entries.filter((e) => (choices[e.id] ?? 'off') !== 'off').map((e) => `${e.label} (${STRENGTH_LABELS[choices[e.id] as Exclude<EditChoice, 'off'>]})`)
  return on.length ? `On for this run: ${on.join(', ')}.` : 'None of your LoRAs are on for this run.'
}

export function EditLoras({
  purpose,
  entries,
  choices,
  startsOn = {},
  kindWords = 'this kind of asset',
  disabled,
  notes,
  onChange,
}: {
  purpose: string
  entries: EditLora[]
  choices: Record<string, EditChoice>
  // The LoRAs that start on for this kind of asset in this step (lora_defaults), so each can say so.
  startsOn?: Record<string, EditChoice>
  // The kind of asset in plain words ("humanoids"), for that note.
  kindWords?: string
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
        <div className="small edit-lora-summary" role="status">
          {onForThisRun(entries, choices)} <span className="muted">Two at a time is the limit for one step.{full ? ' Turn one off to choose another.' : ''}</span>
        </div>
      )}
      {entries.map((e) => {
        const current = choices[e.id] ?? 'off'
        const on = current !== 'off'
        const strength: Exclude<EditChoice, 'off'> = on ? current : ((e.default_strength as Exclude<EditChoice, 'off'> | undefined) ?? 'normal')
        // At the limit, a LoRA that is off cannot be turned on; a LoRA that is on can always be turned off.
        const blocked = full && !on
        return (
          <div key={e.id} className={'edit-lora-row' + (on ? '' : ' addon-off')}>
            <div className="addon-head">
              <button
                type="button"
                role="switch"
                aria-checked={on}
                aria-label={`${e.label} for ${purpose}: ${on ? 'on' : 'off'}`}
                className={'switch' + (on ? ' switch-on' : '')}
                disabled={disabled || blocked}
                title={blocked ? 'Two are on already: turn one off first' : on ? 'Switch this LoRA off for this run' : 'Switch this LoRA on for this run'}
                onClick={() => onChange(e.id, on ? 'off' : strength)}
              >
                <span className="switch-knob" />
                <span className="switch-label">{on ? 'On' : 'Off'}</span>
              </button>
              <span className="addon-label">{e.label}</span>
              <span className="segmented" role="radiogroup" aria-label={`Strength of ${e.label} for ${purpose}`}>
                {STEPS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="radio"
                    aria-checked={on && strength === s}
                    disabled={disabled || !on}
                    title={on ? undefined : 'Switch the LoRA on to choose a strength'}
                    className={'seg' + (on && strength === s ? ' seg-on' : '')}
                    onClick={() => onChange(e.id, s)}
                  >
                    {STRENGTH_LABELS[s]}
                  </button>
                ))}
              </span>
            </div>
            {startsOn[e.id] && <div className="muted small">Starts on for {kindWords} (set in System, Your library).</div>}
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
