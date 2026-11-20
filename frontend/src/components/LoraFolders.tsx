// The person's LoRA folders (backend lora_folders.py): the folders their LoRA files are in. Add one (the Choose a folder button in
// the desktop app, or type its full path), remove one, and see each folder's state. ComfyUI reads these folders only when it starts,
// so after a change the note says which card's ComfyUI to stop and start again (Graphics cards panel). The app never restarts it.
// Used on the setup page and in the ComfyUI install step.
import { useEffect, useState } from 'react'
import { api, type LoraFolders as LoraFoldersState, type LoraRestartNote } from '../api'
import { hostPick } from '../desktop'
import { ErrorText } from './ui'

// The per-card lines, shown only while a card is running (stop and start that one). When none is running, the top note says
// ComfyUI reads these folders when it starts, and there are no per-card lines.
function cardNotes(notes: LoraRestartNote[]) {
  if (!notes.some((n) => n.state === 'restart')) return null
  return (
    <>
      {notes.map((n) => (
        <div key={n.card + n.state} className="small">
          {n.text}
        </div>
      ))}
    </>
  )
}

export default function LoraFolders({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<LoraFoldersState | null>(null)
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [message, setMessage] = useState('')
  const [notes, setNotes] = useState<LoraRestartNote[]>([])

  useEffect(() => {
    api
      .loraFolders()
      .then((d) => {
        setData(d)
        setNotes(d.restart)
      })
      .catch((e: unknown) => setError(e))
  }, [])

  // One change (add or remove): the new list, the plain answer, and the restart notes for it. False when it was refused.
  const change = async (fn: () => Promise<LoraFoldersState>): Promise<boolean> => {
    setBusy(true)
    setError(null)
    setMessage('')
    try {
      const r = await fn()
      setData(r)
      setMessage(r.message ?? '')
      setNotes(r.restart)
      onChanged?.()
      return true
    } catch (e) {
      setError(e)
      return false
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    const p = path.trim()
    if (!p) return
    if (await change(() => api.addLoraFolder(p))) setPath('')
  }

  const browse = async () => {
    const host = hostPick()
    if (!host) return
    const picked = await host.pickFolder('Choose the folder your LoRA files are in')
    if (picked) setPath(picked)
  }

  return (
    <div className="lora-folders">
      <div className="muted small">Your LoRA files stay in these folders. ComfyUI reads them where they are; nothing is copied or moved.</div>
      {data && data.folders.length === 0 && <div className="muted small">No LoRA folders yet.</div>}
      {data && data.folders.length > 0 && (
        <ul className="lora-folder-list">
          {data.folders.map((f) => (
            <li key={f.path} className="row-between">
              <span>
                <span className="mono small">{f.path}</span>
                {f.exists && f.readable && (
                  <span className="muted small">
                    {' '}
                    ({f.lora_files} LoRA file{f.lora_files === 1 ? '' : 's'})
                  </span>
                )}
                {f.problem && <div className="error small">{f.problem}</div>}
              </span>
              <button type="button" className="small-btn" disabled={busy} onClick={() => void change(() => api.removeLoraFolder(f.path))}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="row-gap">
        <input
          value={path}
          placeholder="D:\LoRAs"
          aria-label="Full path of a LoRA folder"
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void add()
            }
          }}
        />
        {hostPick() && (
          <button type="button" disabled={busy} onClick={() => void browse()}>
            Choose a folder
          </button>
        )}
        <button type="button" className="primary" disabled={busy || !path.trim()} onClick={() => void add()}>
          {busy ? 'Working...' : 'Add folder'}
        </button>
      </div>
      {message ? (
        <div className="setup-ok small" role="status">
          {message}
        </div>
      ) : (
        data?.note && <div className="small">{data.note}</div>
      )}
      {cardNotes(notes)}
      <ErrorText error={error} />
    </div>
  )
}
