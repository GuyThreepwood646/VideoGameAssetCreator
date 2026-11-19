// Your own add-ons (backend user_addons.py). Two ways to bring a file in, both ending in the same short form:
//   1. "Choose a file". In the desktop app the native picker gives the real path and the backend (addon_links.py) uses the file where it
//      is (already in a folder ComfyUI reads), links it (same drive) or, only after "Copy", copies it. In a browser there is no real path:
//      the .safetensors file is sent to this app's own backend on this computer, which keeps a copy in the app's add-on folder
//      (VGAC/mine/ inside ComfyUI's loras folder).
//   2. "It is already on this computer": open that folder, put the file in it, then "Look again".
// The form is prefilled from the file's header; the painter is left empty when the file does not say.
// This sits inside the Fine-tune <form>, so it uses no <form> of its own, every button is type="button", and Enter in
// its text boxes never submits Fine-tune.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { api, STYLE_MODELS, type AddonEntry, type AddonPlaced, type AddonPrefill, type MadeFor, type MyAddonsListing, type StrengthName, type UnregisteredAddon } from '../api'
import { fmtBytes } from '../components/ImageDrop'
import { hostPick, pickLoraPath } from '../desktop'
import { Info } from '../components/Info'
import { ErrorText } from '../components/ui'
import { ADDON_BROWSER_COPY, ADDON_CHOOSE_NOTE, ADDON_CHOOSE_TEXT, modelLabel, STRENGTH_LABELS } from '../help'
import { EDIT_MODEL, EDIT_MODEL_LABEL, MADE_FOR_EDIT, MADE_FOR_PLAIN, madeForOf, PLAIN_WARNING } from '../editLoras'

const MAX_ADDON_BYTES = 4 * 1024 * 1024 * 1024
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

// `how` is the plain sentence of what was done with the file (used where it is, linked, copied); `inPlace` is true for a file used where it is.
type Picked = { fileName: string; bytes: number; prefill: AddonPrefill | null; inPlace?: boolean; how?: string }

export function AddMyAddon({ painter, onAdded, onClose }: { painter: string; onAdded: (a: AddonEntry) => void; onClose: () => void }) {
  const mine = useMyAddons(true)
  const [picked, setPicked] = useState<Picked | null>(null)
  const [way, setWay] = useState<'file' | 'folder' | null>(null)
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
        {ADDON_CHOOSE_TEXT[hostPick() ? 'desktop' : 'browser']}
      </p>
      <div className="addon-add-ways">
        <button type="button" className={way === 'file' ? 'primary' : ''} onClick={() => setWay('file')}>
          Choose a file
        </button>
        <button type="button" className={way === 'folder' ? 'primary' : ''} onClick={() => setWay('folder')}>
          It is already on this computer
        </button>
      </div>
      {way === 'file' && (hostPick() ? <ChoosePath onPicked={setPicked} /> : <ChooseFile onPicked={setPicked} />)}
      {way === 'folder' && <FromFolder folder={mine?.folder ?? ''} onPicked={setPicked} />}
    </div>
  )
}

// Change one of your own add-ons (the System page, Your library): the same short form, starting from its values.
export function ChangeMyAddon({ entry, onSaved, onCancel }: { entry: AddonEntry; onSaved: (a: AddonEntry) => void; onCancel: () => void }) {
  const mine = useMyAddons(true)
  return (
    <AddonForm editing={entry} picked={{ fileName: entry.file_name ?? '', bytes: 0, prefill: null }} painters={painterChoices(mine)} currentPainter="" onSaved={onSaved} onCancel={onCancel} />
  )
}

// The desktop app: the native picker gives the real path. The app uses the file where it is, links it, or offers a labelled copy.
function ChoosePath({ onPicked }: { onPicked: (p: Picked) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [offer, setOffer] = useState<{ path: string; placed: AddonPlaced } | null>(null)
  const take = (r: AddonPlaced) => onPicked({ fileName: r.file_name, bytes: r.bytes, prefill: r.prefill, inPlace: r.in_place, how: r.message })
  const place = async (path: string, makeCopy: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const r = await api.placeAddon(path, makeCopy)
      if (r.route === 'copy_needed') setOffer({ path, placed: r })
      else take(r)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  const choose = async () => {
    setError(null)
    setOffer(null)
    const path = await pickLoraPath()
    if (path) await place(path, false)
  }
  return (
    <div className="addon-add-step">
      <button type="button" disabled={busy} onClick={() => void choose()}>
        {busy ? 'Working...' : 'Choose a LoRA file'}
      </button>
      <div className="muted small">{ADDON_CHOOSE_NOTE}</div>
      {offer && (
        <div className="addon-note small" role="alert">
          <div>{offer.placed.message}</div>
          {offer.placed.can_copy === false && <div className="error small">There is not enough room in that folder for the copy.</div>}
          <div className="row-gap">
            <button type="button" className="primary" disabled={busy || offer.placed.can_copy === false} onClick={() => void place(offer.path, true)}>
              {busy ? 'Copying...' : `Copy ${fmtBytes(offer.placed.bytes)}`}
            </button>
            <button type="button" disabled={busy} onClick={() => setOffer(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <ErrorText error={error} />
    </div>
  )
}

function ChooseFile({ onPicked }: { onPicked: (p: Picked) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)
  const choose = async (file: File | undefined) => {
    if (!file) return
    setError(null)
    if (!file.name.toLowerCase().endsWith('.safetensors')) {
      setError(new Error('LoRAs must be .safetensors files. Other kinds of model file are not accepted.'))
      return
    }
    if (file.size > MAX_ADDON_BYTES) {
      setError(new Error(`That file is ${fmtBytes(file.size)}, larger than 4 GB. LoRAs are much smaller; this may not be a LoRA.`))
      return
    }
    setProgress(0)
    try {
      const up = await api.uploadAddon(file, setProgress)
      onPicked({ fileName: up.file_name, bytes: up.bytes, prefill: up.prefill, how: ADDON_BROWSER_COPY })
    } catch (e) {
      setError(e)
    } finally {
      setProgress(null)
      if (input.current) input.current.value = ''
    }
  }
  return (
    <div className="addon-add-step">
      <input ref={input} type="file" accept=".safetensors" disabled={progress !== null} aria-label="Choose a LoRA file" onChange={(e) => choose(e.target.files?.[0])} />
      {progress !== null ? (
        <div className="muted small" role="status">
          Making a copy in the app's LoRA folder... {Math.round(progress * 100)}%
        </div>
      ) : (
        <div className="muted small">{ADDON_BROWSER_COPY} Nothing is sent to the internet.</div>
      )}
      <ErrorText error={error} />
    </div>
  )
}

function FromFolder({ folder, onPicked }: { folder: string; onPicked: (p: Picked) => void }) {
  const [files, setFiles] = useState<UnregisteredAddon[] | null>(null)
  const [shownFolder, setShownFolder] = useState(folder)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const look = async () => {
    setBusy(true)
    setError(null)
    try {
      setFiles(await api.unregisteredAddons())
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  const open = async () => {
    setError(null)
    try {
      const r = await api.openAddonFolder()
      setShownFolder(r.folder)
    } catch (e) {
      setError(e)
    }
  }
  return (
    <div className="addon-add-step">
      <p className="muted small">
        Put the .safetensors file in the app's LoRA folder, then press Look again. The app only ever looks in this one folder.
      </p>
      {shownFolder && <div className="mono small addon-folder">{shownFolder}</div>}
      <div className="row-gap">
        <button type="button" onClick={open}>
          Open the LoRA folder
        </button>
        <button type="button" onClick={look} disabled={busy}>
          {busy ? 'Looking...' : 'Look again'}
        </button>
      </div>
      {files && files.length === 0 && <div className="muted small">No new files in the LoRA folder yet.</div>}
      {files && files.length > 0 && (
        <ul className="addon-files">
          {files.map((f) => (
            <li key={f.file_name} className="row-between">
              <span>
                {f.file_name} <span className="muted small">({fmtBytes(f.bytes)})</span>
                {f.error && <div className="error small">{f.error}</div>}
              </span>
              {!f.error && (
                <button type="button" onClick={() => onPicked({ fileName: f.file_name, bytes: f.bytes, prefill: f.prefill ?? null })}>
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
      // Changing keeps the file as it is (removing and adding again would take away a link or copy the app made).
      const entry = editing
        ? await api.updateMyAddon(editing.id, change)
        : await api.addMyAddon({ ...change, file_name: picked.fileName, in_place: picked.inPlace })
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
        {!saving && !editing && !picked.inPlace && <span className="muted small">If you cancel, the file stays in the LoRA folder; Look again finds it.</span>}
      </div>
    </div>
  )
}
