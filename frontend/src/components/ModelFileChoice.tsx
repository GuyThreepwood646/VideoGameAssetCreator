// "Set model location": point the app at a model or LoRA file the person already has, for one entry of the download list. The
// same control sits on the models panel and on the System page's model rows (one mechanism, backend/vgac/model_choices.py).
// The window's file chooser when there is one, else a typed path. Any model file may be chosen; a file that is not the one the
// app was tested with comes back with a warning and is used only after "Use it anyway" (the panel row has that button).
import { useState } from 'react'
import { api, type PlaceOptions } from '../api'
import { hostPick } from '../desktop'

const MODEL_FILES = 'Model files|*.safetensors;*.gguf;*.ckpt;*.sft'

export default function ModelFileChoice({
  specId,
  name,
  chosen,
  onChanged,
}: {
  specId: string
  name: string
  chosen: boolean // the entry already uses a file of the person's own
  onChanged: () => void | Promise<void>
}) {
  const [typing, setTyping] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [place, setPlace] = useState<{ path: string; opts: PlaceOptions } | null>(null) // a file in a folder ComfyUI does not read

  const act = async (fn: () => Promise<string>) => {
    setBusy(true)
    setNote(null)
    try {
      const text = await fn()
      setNote(text ? { ok: false, text } : null)
      setTyping(null)
      await onChanged()
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }
  const choose = (path: string) =>
    act(async () => {
      const r = await api.chooseModel(specId, path)
      if (r.needs_place) {
        setPlace({ path, opts: r.needs_place })
        return ''
      }
      setPlace(null)
      return r.warning
    })
  const doPlace = (mode: 'link' | 'copy') =>
    act(async () => {
      const r = await api.placeModel(specId, place!.path, mode)
      setPlace(null)
      return mode === 'copy' ? 'Copying your file in the background; the models panel shows how far it is.' : (r.warning ?? '')
    })
  const pick = () => {
    const host = hostPick()
    if (host) void host.pickFile(`Choose your file for ${name}`, MODEL_FILES).then((p) => p && void choose(p))
    else setTyping(typing ?? '')
  }

  return (
    <span className="row-gap">
      {chosen ? (
        <button type="button" className="link small" disabled={busy} onClick={() => void act(async () => (await api.unchooseModel(specId), ''))}>
          Use the app&apos;s own file again
        </button>
      ) : (
        <button type="button" className="link small" disabled={busy} onClick={pick}>
          Set model location
        </button>
      )}
      {typing !== null && !chosen && (
        <>
          <input type="text" className="setup-path" value={typing} placeholder="The full path of your file" onChange={(e) => setTyping(e.target.value)} />
          <button type="button" className="small-btn" disabled={busy || !typing.trim()} onClick={() => void choose(typing.trim())}>
            Use this file
          </button>
        </>
      )}
      {place && (
        <div className="small">
          <div>{place.opts.message}</div>
          <div className="row-gap">
            {place.opts.can_link && (
              <button type="button" className="small-btn" disabled={busy} onClick={() => void doPlace('link')}>
                Link it (nothing is copied)
              </button>
            )}
            {place.opts.can_copy && (
              <button type="button" className="small-btn" disabled={busy} onClick={() => void doPlace('copy')}>
                Copy it in ({(place.opts.size / 1e9).toFixed(1)} GB)
              </button>
            )}
            <button type="button" className="link small" onClick={() => setPlace(null)}>
              Cancel
            </button>
          </div>
          {!place.opts.can_link && <div className="muted">It is on another drive than your ComfyUI&apos;s models, so it cannot be linked.</div>}
          <div className="muted">
            Or add the folder {place.opts.add_folder} to your ComfyUI&apos;s extra model paths yourself, restart ComfyUI and press Look again. The app never changes your ComfyUI settings.
          </div>
        </div>
      )}
      {note && <span className={note.ok ? 'setup-ok small' : 'warn small'}>{note.text}</span>}
    </span>
  )
}
