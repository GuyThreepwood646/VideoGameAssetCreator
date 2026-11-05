// Your own add-ons (backend user_addons.py). Two ways to bring a file in, both ending in the same short form:
//   1. "Choose a file": the .safetensors file is sent to this app's own backend on this computer, which keeps it in
//      the app's add-on folder (VGAC/mine/ inside ComfyUI's loras folder).
//   2. "It is already on this computer": open that folder, put the file in it, then "Look again".
// The form is prefilled from the file's header; the painter is left empty when the file does not say.
// This sits inside the Fine-tune <form>, so it uses no <form> of its own, every button is type="button", and Enter in
// its text boxes never submits Fine-tune.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { api, STYLE_MODELS, type AddonEntry, type AddonPrefill, type MyAddonsListing, type StrengthName, type UnregisteredAddon } from '../api'
import { fmtBytes } from '../components/ImageDrop'
import { Info } from '../components/Info'
import { ErrorText } from '../components/ui'
import { modelLabel, STRENGTH_LABELS } from '../help'

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

type Picked = { fileName: string; bytes: number; prefill: AddonPrefill | null }

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
        A LoRA (style add-on) is a .safetensors file you downloaded, for example from a model sharing site. You do not type a path: choose the file, and the app keeps a copy in its own LoRA folder and adds
        it to the picture for you.
      </p>
      <div className="addon-add-ways">
        <button type="button" className={way === 'file' ? 'primary' : ''} onClick={() => setWay('file')}>
          Choose a file
        </button>
        <button type="button" className={way === 'folder' ? 'primary' : ''} onClick={() => setWay('folder')}>
          It is already on this computer
        </button>
      </div>
      {way === 'file' && <ChooseFile onPicked={setPicked} />}
      {way === 'folder' && <FromFolder folder={mine?.folder ?? ''} onPicked={setPicked} />}
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
      onPicked({ fileName: up.file_name, bytes: up.bytes, prefill: up.prefill })
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
          Copying the file into the app's LoRA folder... {Math.round(progress * 100)}%
        </div>
      ) : (
        <div className="muted small">The file is copied on this computer only. Nothing is sent to the internet.</div>
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
}: {
  picked: Picked
  painters: { id: string; label: string }[]
  currentPainter: string
  onSaved: (a: AddonEntry) => void
  onCancel: () => void
}) {
  const pre = picked.prefill
  const [label, setLabel] = useState(pre?.label ?? picked.fileName.replace(/\.safetensors$/i, ''))
  const [models, setModels] = useState<string[]>(pre?.base_models ?? [])
  const [trigger, setTrigger] = useState(pre?.trigger ?? '')
  const [strength, setStrength] = useState<StrengthName>('normal')
  const [source, setSource] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)
  const missing = !label.trim() ? 'Give the LoRA a name.' : models.length === 0 ? 'Choose at least one model.' : null
  const save = async () => {
    if (missing) {
      setError(new Error(missing))
      return
    }
    setSaving(true)
    setError(null)
    try {
      const entry = await api.addMyAddon({ file_name: picked.fileName, label: label.trim(), base_models: models, trigger: trigger.trim(), default_strength: strength, source: source.trim() })
      await reloadMyAddons().catch(() => undefined)
      onSaved(entry)
    } catch (e) {
      setError(e)
    } finally {
      setSaving(false)
    }
  }
  const toggle = (id: string, on: boolean) => setModels((cur) => (on ? [...cur, id] : cur.filter((m) => m !== id)))
  return (
    <div className="addon-add addon-form">
      <div className="row-between">
        <strong>Set up your LoRA</strong>
        <span className="muted small">
          {picked.fileName} ({fmtBytes(picked.bytes)})
        </span>
      </div>

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
          {saving ? 'Saving...' : 'Add it'}
        </button>
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        {!saving && <span className="muted small">If you cancel, the file stays in the LoRA folder; Look again finds it.</span>}
      </div>
    </div>
  )
}
