// "Use my own animation file" (backend moves.py, /api/my-moves). The person chooses an FBX, GLB or BVH file with a
// human-shaped skeleton; the app keeps a copy in its own folder, reads its moves and bones in Blender and says how
// well it recognised the skeleton. Then the person names each move, says whether it loops and, if they like, where
// the file came from (that goes into the export's manifest).
import { useRef, useState } from 'react'
import { movesApi, type MyMoveFile } from '../api'
import { fmtBytes } from '../components/ImageDrop'
import { Info } from '../components/Info'
import { Disclosure, ErrorText } from '../components/ui'

const KINDS = ['.fbx', '.glb', '.bvh']
export const RESPONSIBILITY = 'You are responsible for being allowed to use this file in your game.'

export function MyMoveUpload({ maxMb, onDone, onCancel }: { maxMb: number; onDone: (f: MyMoveFile) => void; onCancel: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [state, setState] = useState<{ phase: 'idle' | 'sending' | 'reading'; progress: number }>({ phase: 'idle', progress: 0 })
  const [error, setError] = useState<unknown>(null)
  const [file, setFile] = useState<MyMoveFile | null>(null)

  const choose = async (f: File | undefined) => {
    if (!f) return
    setError(null)
    const lower = f.name.toLowerCase()
    if (!KINDS.some((k) => lower.endsWith(k))) {
      setError(new Error('Choose an FBX, GLB or BVH file. Other kinds of file cannot hold moves the app can read.'))
      return
    }
    if (maxMb > 0 && f.size > maxMb * 1024 * 1024) {
      setError(new Error(`That file is ${fmtBytes(f.size)}, larger than ${maxMb} MB. Animation files are usually much smaller.`))
      return
    }
    setState({ phase: 'sending', progress: 0 })
    try {
      const up = await movesApi.uploadMine(f, (p) => setState({ phase: p >= 1 ? 'reading' : 'sending', progress: p }))
      setFile(up)
    } catch (e) {
      setError(e)
    } finally {
      setState({ phase: 'idle', progress: 0 })
      if (input.current) input.current.value = ''
    }
  }

  if (file) return <MyMoveForm file={file} onSaved={onDone} onCancel={onCancel} fresh />
  const busy = state.phase !== 'idle'
  return (
    <div className="mymove-add">
      <div className="row-between">
        <strong>Use my own animation file</strong>
        <button type="button" className="link" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
      <p className="muted small">
        Choose an animation file of a human-shaped skeleton: FBX, GLB or BVH. For example a Mixamo download (choose Without skin), or a BVH motion capture file. The app keeps a copy in its own
        folder and copies the moves onto your character's skeleton.
      </p>
      <input ref={input} type="file" accept={KINDS.join(',')} disabled={busy} aria-label="Choose an animation file" onChange={(e) => choose(e.target.files?.[0])} />
      {state.phase === 'sending' && (
        <div className="muted small" role="status">
          Copying the file into the app... {Math.round(state.progress * 100)}%
        </div>
      )}
      {state.phase === 'reading' && (
        <div className="muted small" role="status">
          Reading the file... This takes up to half a minute.
        </div>
      )}
      {!busy && <div className="muted small">The file stays on this computer. Nothing is sent to the internet.</div>}
      <ErrorText error={error} />
    </div>
  )
}

// Name each move, say whether it loops, and where the file came from.
export function MyMoveForm({ file, onSaved, onCancel, fresh = false }: { file: MyMoveFile; onSaved: (f: MyMoveFile) => void; onCancel: () => void; fresh?: boolean }) {
  const [label, setLabel] = useState(file.label)
  const [source, setSource] = useState(file.source ?? '')
  const [clips, setClips] = useState(file.clips.map((c) => ({ action: c.action, label: c.label, loop: c.loop, seconds: c.seconds })))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const good = file.recognised >= file.total
  const save = async () => {
    setError(null)
    if (clips.some((c) => !c.label.trim())) {
      setError(new Error('Give every move a name.'))
      return
    }
    setBusy(true)
    try {
      onSaved(await movesApi.updateMine(file.id, { label: label.trim() || file.label, source: source.trim(), clips: clips.map((c) => ({ action: c.action, label: c.label.trim(), loop: c.loop })) }))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="mymove-form">
      <div className="row-between">
        <strong>{fresh ? 'Your file is ready' : 'Your animation file'}</strong>
        <button type="button" className="link" onClick={onCancel} disabled={busy}>
          {fresh ? 'Not now' : 'Cancel'}
        </button>
      </div>
      <div className={'small ' + (good ? '' : 'mymove-partial')}>
        {file.sentence}
        <Info term="term.my_moves" />
        {file.missing.length > 0 && <span className="muted"> Not found: {file.missing.join(', ')}. Those parts follow the bone above them.</span>}
      </div>
      <label className="field">
        <span className="field-label">Name of the file in your list</span>
        <input value={label} maxLength={80} disabled={busy} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <div className="field-label">Its moves ({clips.length})</div>
      <ul className="mymove-clips">
        {clips.map((c, i) => (
          <li key={c.action}>
            <input
              aria-label={`Name of the move ${c.action}`}
              value={c.label}
              maxLength={60}
              disabled={busy}
              onChange={(e) => setClips((list) => list.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
            />
            <label className="check-item">
              <input type="checkbox" checked={c.loop} disabled={busy} onChange={(e) => setClips((list) => list.map((x, j) => (j === i ? { ...x, loop: e.target.checked } : x)))} />
              Loops
            </label>
            <span className="muted small">{c.seconds ? `${c.seconds.toFixed(1)} s` : ''}</span>
          </li>
        ))}
      </ul>
      <label className="field">
        <span className="field-label">Where it came from (optional)</span>
        <input value={source} maxLength={500} placeholder="For example: made by me, or the web page you downloaded it from" disabled={busy} onChange={(e) => setSource(e.target.value)} />
      </label>
      <p className="small mymove-honest">
        {RESPONSIBILITY} What you write in Where it came from goes into the export's manifest.
      </p>
      <Disclosure label="Details">
        <div className="small muted mono">
          {file.file_name} ({file.kind.toUpperCase()}), {file.recognised} of {file.total} main bones recognised
          {clips.map((c) => `; ${c.action}`).join('')}
        </div>
      </Disclosure>
      <div className="row-gap">
        <button type="button" className="primary" onClick={save} disabled={busy}>
          {busy ? 'Saving...' : 'Save'}
        </button>
      </div>
      <ErrorText error={error} />
    </div>
  )
}
