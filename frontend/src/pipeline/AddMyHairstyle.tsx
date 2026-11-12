// "Add your own hairstyle" (backend my_hair.py, /api/my-hair): the person chooses a hair mesh from another source (a GLB,
// glTF or FBX file with only the hair in it), says what kind of hairstyle it is and where it came from, and the app keeps
// a copy, reads it in Blender, fits it to the library's reference head and draws its pictures. It then sits in the hair
// picker with a Yours mark, next to the ready-made styles. The same form edits a hairstyle already added (no file).
import { useEffect, useRef, useState } from 'react'
import { hairApi, type MyHairForm, type MyHairListing, type MyHairStyle } from '../api'
import { fmtBytes } from '../components/ImageDrop'
import { ErrorText } from '../components/ui'

const KINDS = ['.glb', '.gltf', '.fbx']
const FALLBACK: MyHairListing = {
  styles: [],
  responsibility: 'Only add hair you made or have the right to use. The file stays on this computer, and the name, source and licence you give here are written into the credits of every character that uses it.',
  max_mb: 80,
  kinds: KINDS,
  families: [
    { id: 'short', label: 'Short' },
    { id: 'bob', label: 'Bob' },
    { id: 'long', label: 'Long' },
    { id: 'ponytail', label: 'Ponytail' },
    { id: 'bun', label: 'Bun' },
    { id: 'braid', label: 'Braid' },
    { id: 'afro', label: 'Afro' },
  ],
  licences: ['CC0', 'CC-BY', 'My own work', 'Paid or licensed asset', 'Not sure'],
}

const LICENCE_WORDS: Record<string, string> = {
  CC0: 'CC0 (free for any use)',
  'CC-BY': 'CC-BY (free, with credit to the author)',
  'My own work': 'I made it',
  'Paid or licensed asset': 'I bought or licensed it',
  'Not sure': 'I am not sure',
}

export function AddMyHairstyle({ editing, onDone, onCancel }: { editing?: MyHairStyle | null; onDone: (s: MyHairStyle) => void; onCancel: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [meta, setMeta] = useState<MyHairListing>(FALLBACK)
  const [file, setFile] = useState<File | null>(null)
  const [label, setLabel] = useState(editing?.name ?? '')
  const [family, setFamily] = useState(editing?.family ?? 'short')
  const [fringe, setFringe] = useState(editing?.fringe ?? false)
  const [curly, setCurly] = useState(editing?.curly ?? false)
  const [source, setSource] = useState(editing?.source ?? '')
  const [licence, setLicence] = useState(editing?.licence && editing.licence !== 'Not stated' ? editing.licence : 'Not sure')
  const [phase, setPhase] = useState<'idle' | 'sending' | 'reading'>('idle')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    hairApi.mine().then(setMeta, () => undefined)
  }, [])

  const busy = phase !== 'idle'
  const chooseFile = (f: File | undefined) => {
    if (!f) return
    setError(null)
    const lower = f.name.toLowerCase()
    if (!KINDS.some((k) => lower.endsWith(k))) {
      setFile(null)
      setError(new Error('Choose a GLB, glTF or FBX file. Other kinds of file cannot hold a hair mesh the app can read.'))
      return
    }
    if (meta.max_mb > 0 && f.size > meta.max_mb * 1024 * 1024) {
      setFile(null)
      setError(new Error(`That file is ${fmtBytes(f.size)}, larger than ${meta.max_mb} MB. Hair meshes are usually much smaller.`))
      return
    }
    setFile(f)
    if (!label.trim()) setLabel(f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim().slice(0, 60))
  }

  const form = (): MyHairForm => ({ label: label.trim(), family, fringe, curly, source: source.trim(), licence: licence === 'Not sure' ? '' : licence })
  const submit = async () => {
    setError(null)
    if (!label.trim()) {
      setError(new Error('Give the hairstyle a name.'))
      return
    }
    try {
      if (editing) {
        setPhase('reading')
        const f = form()
        onDone(await hairApi.updateMine(editing.id, { label: f.label, family: f.family, fringe: f.fringe, curly: f.curly, source: f.source, licence: f.licence }))
        return
      }
      if (!file) {
        setError(new Error('Choose the hair file first.'))
        return
      }
      setPhase('sending')
      onDone(
        await hairApi.uploadMine(file, form(), (p) => {
          setProgress(p)
          setPhase(p >= 1 ? 'reading' : 'sending')
        }),
      )
    } catch (e) {
      setError(e)
    } finally {
      setPhase('idle')
      setProgress(0)
      if (input.current) input.current.value = ''
    }
  }

  return (
    <div className="mymove-add myhair-form" role="group" aria-label={editing ? 'Change your hairstyle' : 'Add your own hairstyle'}>
      <div className="row-between">
        <strong>{editing ? `Change "${editing.name}"` : 'Add your own hairstyle'}</strong>
        <button type="button" className="link" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
      {!editing && (
        <>
          <p className="muted small">
            Choose a hairstyle from another source: a GLB, glTF or FBX file with only the hair in it, not a whole character. The app keeps a copy, fits it to the same head the ready-made styles use, and shows it here next to them. Very detailed hair
            is simplified to about 4,500 triangles so it fits a game's budget. The file stays on this computer.
          </p>
          <label className="field">
            <span className="field-label">Hair file</span>
            <input ref={input} type="file" accept={KINDS.join(',')} disabled={busy} aria-label="Choose a hair file" onChange={(e) => chooseFile(e.target.files?.[0])} />
          </label>
        </>
      )}
      <label className="field">
        <span className="field-label">Name</span>
        <input value={label} maxLength={60} disabled={busy} placeholder="For example: Wavy bob" onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Kind of hairstyle</span>
        <select value={family} disabled={busy} onChange={(e) => setFamily(e.target.value)}>
          {meta.families.map((f) => (
            <option key={f.id} value={f.id}>
              {f.label}
            </option>
          ))}
        </select>
        <span className="muted small">Your description's hair words (for example "ponytail") pick which styles are tried first.</span>
      </label>
      <div className="row-gap">
        <label className="check-item">
          <input type="checkbox" checked={fringe} disabled={busy} onChange={(e) => setFringe(e.target.checked)} />
          Has a fringe (bangs)
        </label>
        <label className="check-item">
          <input type="checkbox" checked={curly} disabled={busy} onChange={(e) => setCurly(e.target.checked)} />
          Curly or wavy
        </label>
      </div>
      <label className="field">
        <span className="field-label">Where it came from (optional)</span>
        <input value={source} maxLength={300} disabled={busy} placeholder="For example: made by me, or the web page you downloaded it from" onChange={(e) => setSource(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Licence</span>
        <select value={licence} disabled={busy} onChange={(e) => setLicence(e.target.value)}>
          {meta.licences.map((l) => (
            <option key={l} value={l}>
              {LICENCE_WORDS[l] ?? l}
            </option>
          ))}
        </select>
      </label>
      <p className="small mymove-honest">{meta.responsibility}</p>
      {phase === 'sending' && (
        <div className="muted small" role="status">
          Copying the file into the app... {Math.round(progress * 100)}%
        </div>
      )}
      {phase === 'reading' && (
        <div className="muted small" role="status">
          {editing ? 'Saving...' : 'Reading the hair and drawing its pictures... This takes up to a minute.'}
        </div>
      )}
      <div className="row-gap">
        <button type="button" className="primary" onClick={submit} disabled={busy || (!editing && !file)}>
          {editing ? 'Save' : busy ? 'Adding...' : 'Add hairstyle'}
        </button>
      </div>
      <ErrorText error={error} />
    </div>
  )
}
