// "Use my own 3D model" on step 3 (docs/PROMPT-AND-IMPORT-SPEC.md, item 3 phase A; backend vgac/model_import.py).
// The file goes to the app as it is; Blender reads it in a job on this computer, takes pictures of it for steps 1 and 2,
// and the model, its pictures and its checks arrive as one kept result. ModelFacing then offers to use another side as
// the front, because files from other programs face different ways.
import { useRef, useState } from 'react'
import { api, MODEL_KINDS, MODEL_MAX_MB, versionsOf, type Asset, type Version } from '../api'
import { Info } from '../components/Info'

type Act = (fn: () => Promise<unknown>) => Promise<void> | void

export function isOwnModel(v: Pick<Version, 'params'> | undefined | null): boolean {
  return v?.params?.source === 'upload' && v?.params?.engine === 'upload'
}

export function OwnModel({ asset, busy, act, onCreated }: { asset: Asset; busy: boolean; act: Act; onCreated: (versionId: string) => void }) {
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const hasPictures = versionsOf(asset, 'concept').length > 0 || versionsOf(asset, 'turnaround').length > 0
  const pick = (f: File | null) => {
    setProblem(null)
    if (!f) return setFile(null)
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase()
    if (!MODEL_KINDS.includes(ext)) return setProblem('Use a GLB, glTF, FBX or OBJ file, or a zip with the model and its pictures.')
    if (f.size > MODEL_MAX_MB * 1024 * 1024) return setProblem(`This file is larger than ${MODEL_MAX_MB} MB.`)
    setFile(f)
  }
  const send = () =>
    act(async () => {
      if (!file) return
      setProgress(0)
      try {
        const r = await api.uploadModel(asset.id, file, setProgress)
        setFile(null)
        setOpen(false)
        if (input.current) input.current.value = ''
        onCreated(r.version.id)
      } finally {
        setProgress(null)
      }
    })
  return (
    <div className="own-pictures">
      <div className="row-gap">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} title="Start from a 3D model you already have instead of building one">
          Use my own 3D model
        </button>
        <Info term="term.own_model" />
      </div>
      {open && (
        <div className="own-pictures-panel">
          <label className="field">
            <span className="field-label">Your model</span>
            <input
              ref={input}
              type="file"
              accept={MODEL_KINDS.join(',')}
              disabled={busy}
              onChange={(e) => pick(e.target.files?.[0] ?? null)}
              aria-describedby="own-model-hint"
            />
          </label>
          <div id="own-model-hint" className="muted small">
            GLB, glTF, FBX or OBJ, up to {MODEL_MAX_MB} MB. For an OBJ, or an FBX or glTF whose pictures are separate files, put the model and its pictures in one zip. The parts are joined into one model and its colours go into one set of texture maps; a skeleton is set aside (step 5 rigs it again).
          </div>
          {hasPictures && (
            <div className="tool-note small">
              Steps 1 and 2 get pictures of your model, so the later steps check against it. The pictures there now stay in each step's history.
            </div>
          )}
          {problem && (
            <div className="error small" role="alert">
              {problem}
            </div>
          )}
          {progress !== null && (
            <div className="small" role="status">
              Sending {Math.round(progress * 100)}%
              <progress value={progress} max={1} aria-label="Sending your model" />
            </div>
          )}
          <div className="row-gap">
            <button type="button" className="primary" disabled={busy || !file} onClick={send}>
              Use this model
            </button>
            <button type="button" className="link small" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// Which side to use as the front, in the words of step 2's pictures, and the turn that makes that side the front
// (counter-clockwise seen from above): the side shown as "Left side" faces +X, so it needs 270.
const FRONT_FROM: { label: string; turn: 90 | 180 | 270 }[] = [
  { label: 'Left side', turn: 270 },
  { label: 'Back', turn: 180 },
  { label: 'Right side', turn: 90 },
]

export function ModelFacing({ version, busy, act, onCreated }: { version: Version; busy: boolean; act: Act; onCreated: (versionId: string) => void }) {
  const notes = Array.isArray(version.metrics?.import_notes) ? (version.metrics.import_notes as string[]) : []
  const imported = (version.metrics?.imported ?? {}) as { triangles?: number; texture_mode?: string; materials?: number }
  const turn = (fromSide: 90 | 180 | 270) =>
    act(async () => {
      const r = await api.turnModel(version.id, fromSide)
      onCreated(r.version.id)
    })
  const how =
    imported.texture_mode === 'kept'
      ? 'its own texture was kept'
      : imported.texture_mode === 'baked'
        ? `its colours${imported.materials && imported.materials > 1 ? ` from ${imported.materials} materials` : ''} were baked into one texture`
        : 'it has no colours'
  return (
    <div className="own-pictures-panel own-model-facing">
      <div className="small">
        Your model{typeof imported.triangles === 'number' ? `, ${imported.triangles.toLocaleString()} triangles` : ''}; {how}.
      </div>
      {notes.map((n) => (
        <div key={n} className="tool-note small">
          {n}
        </div>
      ))}
      <div className="small">
        Is the front right? The step 1 picture shows the side the app treats as the front, and step 4 turns the model to face that way. If another side of step 2 is the real front, use it:
      </div>
      <div className="row-gap" role="group" aria-label="Use another side as the front">
        {FRONT_FROM.map((f) => (
          <button key={f.label} type="button" className="small-btn" disabled={busy} onClick={() => turn(f.turn)}>
            {f.label} is the front
          </button>
        ))}
        <Info term="term.model_front" />
      </div>
    </div>
  )
}
