// "Set model location": point the app at a model or LoRA file the person already has, for one entry of the download list. The
// same control sits on the models panel and on the System page's model rows (one mechanism, backend/vgac/model_choices.py).
// The window's file chooser when there is one, else a typed path. Any model file may be chosen; a file that is not the one the
// app was tested with comes back with a warning and is used only after "Use it anyway" (the panel row has that button).
import { useState } from 'react'
import { api } from '../api'
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
  const choose = (path: string) => act(async () => (await api.chooseModel(specId, path)).warning)
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
      {note && <span className={note.ok ? 'setup-ok small' : 'warn small'}>{note.text}</span>}
    </span>
  )
}
