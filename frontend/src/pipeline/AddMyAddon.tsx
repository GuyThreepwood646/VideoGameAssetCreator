// Your own add-ons (backend user_addons.py). A LoRA is chosen from the LoRA folders the person linked on the setup page
// (backend lora_folders.py): the file stays where it is, ComfyUI reads it there, and nothing is copied. The list shows every LoRA
// file in those folders that is not set up yet; "Set it up" opens the short form. The form is prefilled from the file's header;
// the painter is left empty when the file does not say.
// This sits inside the Fine-tune <form>, so it uses no <form> of its own, every button is type="button", and Enter in
// its text boxes never submits Fine-tune.
import { useEffect, useState, type KeyboardEvent } from 'react'
import { api, STYLE_MODELS, type AddonEntry, type AddonPrefill, type AvailableAddon, type LoraFolderState, type MadeFor, type MyAddonsListing, type StrengthName } from '../api'
import { fmtBytes } from '../components/ImageDrop'
import { Info } from '../components/Info'
import { ErrorText } from '../components/ui'
import { modelLabel, STRENGTH_LABELS } from '../help'
import { EDIT_MODEL, EDIT_MODEL_LABEL, MADE_FOR_EDIT, MADE_FOR_PLAIN, madeForOf, PLAIN_WARNING } from '../editLoras'

const STEPS: StrengthName[] = ['subtle', 'normal', 'strong']

// --- The list of your own add-ons, shared by every open Fine-tune --------------------------------------------------

let cache: MyAddonsListing | null = null
let inflight: Promise<MyAddonsListing> | null = null
const listeners = new Set<(l: MyAddonsListing) => void>()

export function reloadMyAddons(): Promise<MyAddonsListing> {
  const p = api.myAddons().then((l) => {
    cache = l
    for (const f of listeners) f(l)
    return l
  })
  inflight = p
  p.finally(() => {
    if (inflight === p) inflight = null
  }).catch(() => undefined)
  return p
}

// Your own add-ons (GET /api/my-addons), or null until loaded (or when the backend has no such list).
export function useMyAddons(enabled: boolean): MyAddonsListing | null {
  const [list, setList] = useState<MyAddonsListing | null>(cache)
  useEffect(() => {
    if (!enabled) return
    listeners.add(setList)
    if (cache) setList(cache)
    else if (!inflight) reloadMyAddons().catch(() => undefined)
    return () => {
      listeners.delete(setList)
    }
  }, [enabled])
  return list
}

// The catalog the concept step shows: the tested add-ons from the stage schema, then your own from the live list
// (the schema copy can be out of date right after adding or removing one).
export function withMyAddons(catalog: AddonEntry[], mine: MyAddonsListing | null): AddonEntry[] {
  const builtIn = catalog.filter((a) => !a.mine)
  return [...builtIn, ...(mine ? mine.addons : catalog.filter((a) => a.mine))]
}

function painterChoices(mine: MyAddonsListing | null): { id: string; label: string }[] {
  return mine?.painters?.length ? mine.painters : STYLE_MODELS.map((id) => ({ id, label: modelLabel(id) }))
}

const noEnter = (e: KeyboardEvent) => {
  if (e.key === 'Enter') e.preventDefault()
}

// --- Adding one ------------------------------------------------------------------------------------------------

// `how` is the plain sentence of where the file is kept (in its LoRA folder).
type Picked = { fileName: string; folder: string; bytes: number; prefill: AddonPrefill | null; how?: string }

export function AddMyAddon({ painter, onAdded, onClose }: { painter: string; onAdded: (a: AddonEntry) => void; onClose: () => void }) {
  const mine = useMyAddons(true)
  const [picked, setPicked] = useState<Picked | null>(null)
  if (picked) return <AddonForm picked={picked} painters={painterChoices(mine)} currentPainter={painter} onSaved={onAdded} onCancel={onClose} />
  return (
    <div className="addon-add">
      <div className="row-between">
        <strong>Add your own LoRA</strong>
        <button type="button" className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
      <p className="muted small">
        Choose a LoRA file from one of your LoRA folders (set them up on the setup page). The file stays where it is: ComfyUI reads it there,
        and nothing is copied.
      </p>
      <FromFolders onPicked={setPicked} />
    </div>
  )
}

// Change one of your own add-ons (the System page, Your library): the same short form, starting from its values.
export function ChangeMyAddon({ entry, onSaved, onCancel }: { entry: AddonEntry; onSaved: (a: AddonEntry) => void; onCancel: () => void }) {
  const mine = useMyAddons(true)
  return (
    <AddonForm editing={entry} picked={{ fileName: entry.file_name ?? '', folder: '', bytes: 0, prefill: null }} painters={painterChoices(mine)} currentPainter="" onSaved={onSaved} onCancel={onCancel} />
  )
}

// The LoRA files in the person's LoRA folders that are not set up yet, and the state of each folder.
function FromFolders({ onPicked }: { onPicked: (p: Picked) => void }) {
  const [files, setFiles] = useState<AvailableAddon[] | null>(null)
  const [folders, setFolders] = useState<LoraFolderState[]>([])
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const look = async () => {
    setBusy(true)
    setError(null)
    try {
      const [found, listing] = await Promise.all([api.availableAddons(), api.loraFolders()])
      setFiles(found)
      setFolders(listing.folders)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    void look()
    // Looked at once when the form opens; "Look again" does the rest.
  }, [])
  return (
    <div className="addon-add-step">
      {folders.length === 0 && files !== null && (
        <div className="muted small">You have no LoRA folders yet. Add the folder your LoRA files are in on the setup page, then press Look again.</div>
      )}
      {folders.filter((f) => !f.exists || !f.readable).map((f) => (
        <div key={f.path} className="error small">
          {f.path}: {f.problem}
        </div>
      ))}
      <div className="row-gap">
        <button type="button" onClick={() => void look()} disabled={busy}>
          {busy ? 'Looking...' : 'Look again'}
        </button>
      </div>
      {files && folders.length > 0 && files.length === 0 && (
        <div className="muted small">
          No new LoRA files in your LoRA folders. If you have just added a folder, ComfyUI must be restarted to see its LoRAs (see the note on the setup page).
        </div>
      )}
      {files && files.length > 0 && (
        <ul className="addon-files">
          {files.map((f) => (
            <li key={f.path} className="row-between">
              <span>
                {f.file_name} <span className="muted small">({fmtBytes(f.bytes)}, in {f.folder})</span>
                {f.error && <div className="error small">{f.error}</div>}
              </span>
              {!f.error && (
                <button
                  type="button"
                  onClick={() => onPicked({ fileName: f.file_name, folder: f.folder, bytes: f.bytes, prefill: f.prefill ?? null, how: `Used where it is, in ${f.folder}. Nothing was copied.` })}
                >
                  Set it up
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <ErrorText error={error} />
    </div>
  )
}


// --- The short form ------------------------------------------------------------------------------------------------

function AddonForm({
  picked,
  painters,
  currentPainter,
  onSaved,
  onCancel,
  editing,
}: {
  picked: Picked
  painters: { id: string; label: string }[]
  currentPainter: string
  onSaved: (a: AddonEntry) => void
  onCancel: () => void
  // Changing one of your own add-ons: the form starts from its values and saving replaces the entry (same file).
  editing?: AddonEntry
}) {
  const pre = picked.prefill
  const [label, setLabel] = useState(editing?.label ?? pre?.label ?? picked.fileName.replace(/\.safetensors$/i, ''))
  const [models, setModels] = useState<string[]>(editing?.base_models ?? pre?.base_models ?? [])
  const [trigger, setTrigger] = useState(editing?.trigger ?? pre?.trigger ?? '')
  const [strength, setStrength] = useState<StrengthName>(editing?.default_strength ?? 'normal')
  const [source, setSource] = useState(editing?.source ?? '')
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)
  // Picture edits: the file settles what it was made for when it can; otherwise the person must choose (both options
  // are locked when the file is sure, so a sure plain file can never be saved as an Edit one).
  const forEdits = models.includes(EDIT_MODEL)
  const detectedMadeFor = madeForOf(pre)
  const madeForLocked = !!detectedMadeFor
  const [madeFor, setMadeFor] = useState<MadeFor | ''>(detectedMadeFor || madeForOf(editing))
  const missing = !label.trim()
    ? 'Give the LoRA a name.'
    : models.length === 0
      ? 'Choose at least one model.'
      : forEdits && !madeFor
        ? 'Choose what it was made for.'
        : null
  const save = async () => {
    if (missing) {
      setError(new Error(missing))
      return
    }
    setSaving(true)
    setError(null)
    try {
      // The picture-edit answer (made_for) goes with the change too, so a LoRA moved onto picture edits is asked the question.
      const change = { label: label.trim(), base_models: models, trigger: trigger.trim(), default_strength: strength, source: source.trim(), made_for: forEdits ? madeFor : '' }
      // Changing keeps the file as it is.
      const entry = editing
        ? await api.updateMyAddon(editing.id, change)
        : await api.addMyAddon({ ...change, file_name: picked.fileName, folder: picked.folder })
      await reloadMyAddons().catch(() => undefined)
      onSaved(entry)
    } catch (e) {
      setError(e)
    } finally {
      setSaving(false)
    }
  }
  // A painter and picture edits are never chosen together: choosing one clears the other.
  const toggle = (id: string, on: boolean) => setModels((cur) => (on ? [...cur.filter((m) => m !== EDIT_MODEL), id] : cur.filter((m) => m !== id)))
  const toggleEdits = (on: boolean) => setModels(on ? [EDIT_MODEL] : [])
  // The warning follows what the LoRA was made for: a plain Qwen-Image file, or one the person calls plain or not sure.
  const warning = forEdits && madeFor === MADE_FOR_PLAIN ? PLAIN_WARNING : ''
  return (
    <div className="addon-add addon-form">
      <div className="row-between">
        <strong>{editing ? 'Change your LoRA' : 'Set up your LoRA'}</strong>
        <span className="muted small">{editing ? picked.fileName : `${picked.fileName} (${fmtBytes(picked.bytes)})`}</span>
      </div>
      {(editing?.placement?.note || picked.how) && <div className="addon-note small">{editing?.placement?.note || picked.how}</div>}

      <label className="field">
        <span className="field-label">Name</span>
        <input value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} onKeyDown={noEnter} />
      </label>

      <fieldset className="field addon-painters">
        <legend className="field-label">
          Which model was it made for? (its base model)
          <Info term="term.addon_painter" />
        </legend>
        <span className="field-hint">A LoRA only works with the model family it was trained on. Its download page says which one.</span>
        {pre?.painter_note && <div className="addon-note small">{pre.painter_note}</div>}
        <div className="check-row">
          {painters.map((p) => (
            <label key={p.id} className="check-item">
              <input type="checkbox" checked={models.includes(p.id)} onChange={(e) => toggle(p.id, e.target.checked)} />
              {p.label}
              {p.id === currentPainter ? ' (chosen now)' : ''}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="field addon-edits">
        <legend className="field-label">Or use it for picture edits</legend>
        <span className="field-hint">The picture-edit model of this app (Refine, side views, pose redraw, face close-up). Your LoRA is only offered there, never in the concept painters.</span>
        <div className="check-row">
          <label className="check-item">
            <input type="checkbox" checked={forEdits} onChange={(e) => toggleEdits(e.target.checked)} />
            {EDIT_MODEL_LABEL}
          </label>
        </div>
        {forEdits && warning && <div className="addon-note small">{warning}</div>}
      </fieldset>

      {forEdits && (
        <fieldset className="field addon-made-for">
          <legend className="field-label">
            What was it made for?
            <Info term="term.made_for" />
          </legend>
          {madeForLocked ? (
            <span className="field-hint">The file says what it was made for, so this is set and cannot be changed here.</span>
          ) : (
            <span className="field-hint">The file does not say. Choose one before you add it.</span>
          )}
          <div className="check-row">
            <label className="check-item">
              <input type="radio" name="made-for" checked={madeFor === MADE_FOR_EDIT} disabled={madeForLocked} onChange={() => setMadeFor(MADE_FOR_EDIT)} />
              Qwen-Image-Edit
            </label>
            <label className="check-item">
              <input type="radio" name="made-for" checked={madeFor === MADE_FOR_PLAIN} disabled={madeForLocked} onChange={() => setMadeFor(MADE_FOR_PLAIN)} />
              Plain Qwen-Image, or not sure
            </label>
          </div>
        </fieldset>
      )}

      <label className="field">
        <span className="field-label">
          Trigger words (words the LoRA needs in the description)
          <Info term="term.addon_trigger" />
        </span>
        <input value={trigger} maxLength={300} placeholder="for example: ink wash style" onChange={(e) => setTrigger(e.target.value)} onKeyDown={noEnter} />
        <span className="field-hint">Optional. Some LoRAs only work when certain words are in the description. Its download page lists them.</span>
      </label>

      <div className="field">
        <span className="field-label">Starting strength</span>
        <span className="segmented" role="radiogroup" aria-label="Starting strength">
          {STEPS.map((s) => (
            <button key={s} type="button" role="radio" aria-checked={strength === s} className={'seg' + (strength === s ? ' seg-on' : '')} onClick={() => setStrength(s)}>
              {STRENGTH_LABELS[s]}
            </button>
          ))}
          <Info term={'term.strength_' + strength} />
        </span>
      </div>

      <label className="field">
        <span className="field-label">Where it came from (optional)</span>
        <input value={source} maxLength={500} placeholder="a link or a short note" onChange={(e) => setSource(e.target.value)} onKeyDown={noEnter} />
        <span className="field-hint">Kept with every asset that uses it, next to the licence of the tested LoRAs.</span>
      </label>

      <p className="muted small">You are responsible for being allowed to use this file in your game. Check its licence on the page you downloaded it from.</p>
      <ErrorText error={error} />
      <div className="row-gap">
        <button type="button" className="primary" disabled={saving} onClick={save}>
          {saving ? 'Saving...' : editing ? 'Save changes' : 'Add it'}
        </button>
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  )
}
