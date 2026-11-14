// "I already have these": point the app at a folder that holds some of a set's files (backend/vgac/models_setup.py scan_folder).
// The person first sees what was found in it, then takes the matches; each is taken like a single "Set model location" choice
// (hashed in the background, a different file is warned about). The window's folder chooser, else a typed path.
import { useState } from 'react'
import { api, type FolderScan } from '../api'
import { hostPick } from '../desktop'

const HOW: Record<string, string> = { name: 'same name', size: 'same size, another name' }

export default function ModelFolderChoice({ setId, onChanged }: { setId: string; onChanged: () => void | Promise<void> }) {
  const [typing, setTyping] = useState<string | null>(null)
  const [scan, setScan] = useState<FolderScan | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const look = async (path: string) => {
    setBusy(true)
    setNote(null)
    try {
      setScan(await api.scanModelFolder(setId, path))
      setTyping(null)
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }
  const pick = () => {
    const host = hostPick()
    if (host) void host.pickFolder('Choose the folder your model files are in').then((p) => p && void look(p))
    else setTyping(typing ?? '')
  }
  const take = async () => {
    if (!scan) return
    setBusy(true)
    try {
      const r = await api.useModelFolder(setId, scan.folder, scan.found.filter((f) => f.ok).map((f) => f.id))
      setNote({ ok: r.left.length === 0, text: `Using ${r.taken.length} of your files.${r.left.map((l) => ` ${l.name}: ${l.reason}`).join('')}` })
      setScan(null)
      await onChanged()
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="setup-field">
      <button type="button" className="link small" disabled={busy} onClick={pick}>
        I already have these
      </button>
      {typing !== null && (
        <span className="row-gap">
          <input type="text" className="setup-path" value={typing} placeholder="The full path of the folder" onChange={(e) => setTyping(e.target.value)} />
          <button type="button" className="small-btn" disabled={busy || !typing.trim()} onClick={() => void look(typing.trim())}>
            Look in it
          </button>
        </span>
      )}
      {scan && (
        <div className="small">
          <div>
            Found {scan.found.length} of {scan.found.length + scan.missing.length} in {scan.folder}:
          </div>
          <ul className="setup-rows">
            {scan.found.map((f) => (
              <li key={f.id} className="setup-row">
                <span className={'dot ' + (f.ok ? 'ok' : 'warn')} />
                <span className="setup-row-label">{f.name}</span>
                <span className="muted small setup-row-detail">
                  {f.path} ({HOW[f.how] ?? f.how}
                  {f.size_matches ? '' : '; not the size the app was tested with: results may differ or the step may fail'})
                  {f.problem ? ` - cannot be used: ${f.problem}` : ''}
                  {!f.problem && !f.readable && f.can_link ? ' - your ComfyUI does not read that folder; it will be linked in (nothing is copied)' : ''}
                  {!f.problem && !f.readable && !f.can_link ? ' - your ComfyUI does not read that folder and it is on another drive, so it cannot be linked: choose this file on its own to copy it in' : ''}
                </span>
              </li>
            ))}
          </ul>
          {scan.missing.length > 0 && <div className="muted">Not in that folder: {scan.missing.map((m) => m.name).join(', ')}.</div>}
          <div className="row-gap">
            <button type="button" className="small-btn" disabled={busy || !scan.found.some((f) => f.ok)} onClick={() => void take()}>
              Use these {scan.found.filter((f) => f.ok).length} files
            </button>
            <button type="button" className="link small" onClick={() => setScan(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {note && <div className={note.ok ? 'setup-ok small' : 'warn small'}>{note.text}</div>}
    </div>
  )
}
