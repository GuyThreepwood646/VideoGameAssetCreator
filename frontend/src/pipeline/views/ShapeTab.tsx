import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { api, libraryUrl, type ShapeLog, type ShapePreview, type Version } from '../../api'
import { useEditHistory } from '../../editHistory'
import Viewer from '../../Viewer'
import type { ViewerModel, ViewerTool } from '../../viewerTool'
import {
  DEFAULT_SHAPE,
  GAP_MM,
  SIDE_CHOICES,
  START_PERCENT,
  armWords,
  clampShape,
  cutRing,
  focusCentre,
  gapWords,
  nearestArm,
  shapeBody,
  startWords,
  type ShapeSettings,
} from './shapeModel'

// The Shape tab (WP-26): on a finished rig, separate a fused arm from the body. The page sends parameters only. The
// preview computes where each cut starts; the tab draws a ring in the cut's plane on each chosen upper arm, on top of
// the model, and looks at the arm when it opens. Apply runs the job, which makes a new clean-up version. The source
// version is never changed. Undo and redo cover the settings (the WP-71 shared history); applying is undone by
// choosing the earlier version.

const DEBOUNCE_MS = 350
// The app's accent colour (styles.css --accent): a light blue that reads on the skin, the cloth and the leather.
const CUT_COLOUR = 0x6ea4e8

// The rings for the chosen arms, on the model's first mesh (model space, which the glTF file's own mesh uses). They
// draw over the model (no depth test), so a cut start is never hidden by the body. Returns the clean-up.
function addCutRings(model: ViewerModel, preview: ShapePreview, side: ShapeSettings['side']): () => void {
  const host = model.meshes[0]
  if (!host) return () => {}
  const sides = side === 'both' ? (['L', 'R'] as const) : ([side] as const)
  const added: THREE.Mesh[] = []
  for (const s of sides) {
    const arm = preview.arms[s]
    if (!arm) continue
    const r = cutRing(arm)
    const geo = new THREE.RingGeometry(r.inner, r.outer, 48)
    const mat = new THREE.MeshBasicMaterial({ color: CUT_COLOUR, side: THREE.DoubleSide, depthTest: false, depthWrite: false })
    const ring = new THREE.Mesh(geo, mat)
    ring.position.set(...r.center)
    ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(...r.normal).normalize())
    ring.renderOrder = 999
    host.add(ring)
    added.push(ring)
  }
  return () => {
    for (const ring of added) {
      host.remove(ring)
      ring.geometry.dispose()
      ;(ring.material as THREE.Material).dispose()
    }
  }
}

export function ShapeTab({ version, onChanged }: { version: Version; onChanged?: () => void }) {
  const glb = libraryUrl(version.files.glb)
  const { history, activate } = useEditHistory()
  const [settings, setSettings] = useState<ShapeSettings>(DEFAULT_SHAPE)
  const [model, setModel] = useState<ViewerModel | null>(null)
  const [preview, setPreview] = useState<ShapePreview | null>(null)
  const [previewNote, setPreviewNote] = useState<string | null>(null)
  const [focus, setFocus] = useState<{ point: [number, number, number]; radius: number } | null>(null)
  const [picking, setPicking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  // The camera opens on the chosen arm (or both shoulders) once, when both the drawn model and the first preview exist.
  const focusedRef = useRef(false)
  useEffect(() => {
    if (focusedRef.current || !model || !preview) return
    const c = focusCentre(preview.arms, settings.side)
    if (c) {
      setFocus(c)
      focusedRef.current = true
    }
  }, [model, preview, settings.side])

  // Settings change through one place, so each change is one undoable step. Repeated slider moves on one setting merge.
  const change = (next: Partial<ShapeSettings>, label: string, key: string) => {
    const before = settings
    const after = clampShape({ ...settings, ...next })
    if (after.side === before.side && after.gapMm === before.gapMm && after.startPercent === before.startPercent) return
    setSettings(after)
    history.record({ label, key, undo: () => setSettings(before), redo: () => setSettings(after) })
  }

  // Preview: where each cut starts, after the settings settle. Computes only.
  const body = shapeBody(settings)
  const bodyKey = JSON.stringify(body)
  useEffect(() => {
    if (!glb) return
    const live = { current: true }
    const timer = window.setTimeout(() => {
      api
        .shapePreview(version.id, JSON.parse(bodyKey))
        .then((p) => {
          if (!live.current) return
          setPreview(p)
          setPreviewNote(null)
        })
        .catch((e: unknown) => {
          if (live.current) setPreviewNote(e instanceof Error ? e.message : 'The preview could not be made.')
        })
    }, DEBOUNCE_MS)
    return () => {
      live.current = false
      window.clearTimeout(timer)
    }
  }, [version.id, bodyKey, glb])

  // The rings follow the preview and the chosen arm (and so undo and redo).
  useEffect(() => {
    if (!model || !preview) return
    return addCutRings(model, preview, settings.side)
  }, [model, preview, settings.side])

  // Picking an arm on the model: the click chooses the side nearest the arm's shoulder-to-elbow line. The tool reads the
  // latest change through a ref, so it is not rebuilt on every settings move.
  const changeRef = useRef(change)
  useEffect(() => {
    changeRef.current = change
  })
  const tool: ViewerTool | null = useMemo(() => {
    if (!picking || !preview) return null
    return {
      id: 'shape-pick-arm',
      cursor: 'crosshair',
      down: (hit) => {
        if (!hit) return
        const side = nearestArm(hit.point, preview.arms)
        if (side) changeRef.current({ side }, `use the ${armWords(side)}`, 'side')
        setPicking(false)
      },
    }
  }, [picking, preview])

  if (!glb || version.status !== 'done') return null

  const apply = async () => {
    setSaving(true)
    setNote(null)
    try {
      await api.shape(version.id, body)
      setNote('Separating the arm. It runs in Blender and takes a few minutes. The new clean-up step appears when it is made. If the cut is refused, the job says why and the model stays as it was.')
      onChanged?.()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not start the separation. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const armsOk = !!preview && (preview.arms.L || preview.arms.R)
  return (
    <div className="shape-tab" onPointerDownCapture={activate} onFocusCapture={activate}>
      <div className="field-label">Shape</div>
      <p className="muted">
        Separate a fused arm: an arm that the 3D build grew into the body. The cut is made on a new copy of the clean-up.
      </p>
      <div className="shape-tab-operation">
        <strong>Separate a fused arm</strong>
      </div>
      <Viewer url={glb} height={320} tool={tool} onToolExit={() => setPicking(false)} onModelLoaded={setModel} focus={focus} />
      <div className="shape-tab-arm">
        <div className="field-label">Which arm</div>
        <div className="row">
          {SIDE_CHOICES.map((c) => (
            <button key={c.side} type="button" className={settings.side === c.side ? 'primary' : ''} aria-pressed={settings.side === c.side} onClick={() => change({ side: c.side }, `use the ${c.label.toLowerCase()}`, 'side')}>
              {c.label}
            </button>
          ))}
          <button type="button" aria-pressed={picking} disabled={!armsOk} onClick={() => setPicking((p) => !p)}>
            {picking ? 'Click the arm on the model' : 'Pick the arm on the model'}
          </button>
        </div>
        <p className="field-hint">The ring marks where the cut starts on each chosen upper arm.</p>
      </div>
      <label className="field shape-tab-slider">
        <span className="field-label">Gap between the two pieces: {gapWords(settings.gapMm)}</span>
        <input
          type="range"
          min={GAP_MM.min}
          max={GAP_MM.max}
          step={GAP_MM.step}
          value={settings.gapMm}
          onChange={(e) => change({ gapMm: Number(e.target.value) }, `set the gap to ${e.target.value} mm`, 'gap')}
        />
        <span className="field-hint">A wider gap leaves a visible seam between the arm and the body.</span>
      </label>
      <label className="field shape-tab-slider">
        <span className="field-label">Where the cut starts: {startWords(settings.startPercent)}</span>
        <input
          type="range"
          min={START_PERCENT.min}
          max={START_PERCENT.max}
          step={START_PERCENT.step}
          value={settings.startPercent}
          onChange={(e) => change({ startPercent: Number(e.target.value) }, `set the cut start to ${e.target.value} percent`, 'start')}
        />
        <span className="field-hint">A lower start keeps more of the shoulder whole.</span>
      </label>
      <div className="row shape-tab-history">
        <button type="button" disabled={!history.canUndo} onClick={() => void history.undo()} title={history.undoLabel ? `Undo: ${history.undoLabel}` : 'Nothing to undo'}>
          Undo setting
        </button>
        <button type="button" disabled={!history.canRedo} onClick={() => void history.redo()} title={history.redoLabel ? `Redo: ${history.redoLabel}` : 'Nothing to redo'}>
          Redo setting
        </button>
      </div>
      <div className="shape-tab-preview">
        <div className="field-label">Preview</div>
        {previewNote && <p className="warn">{previewNote}</p>}
        {preview?.messages.map((m) => (
          <p key={m}>{m}</p>
        ))}
        <p className="muted">
          The ring shows where the cut starts. The engine follows the weight line inside the fused strip, so the real cut sits near the ring, not exactly on it.
        </p>
      </div>
      <div className="row">
        <button type="button" className="primary" disabled={saving || !armsOk} onClick={() => void apply()}>
          {saving ? 'Starting' : `Separate ${armWords(settings.side)}`}
        </button>
      </div>
      {note && <p className="muted">{note}</p>}
      <p className="muted">
        After this, the next rig run keeps this rig: its skeleton, and its weights with the ones near the cut made again. Try again on
        the rig step makes the whole rig from scratch instead.
      </p>
    </div>
  )
}

// The log a separated clean-up carries (metrics.shape_log): what was done, the engine's measures and its plain lines.
export function ShapeLogNote({ log }: { log: ShapeLog }) {
  const e = log.engine
  const side = log.parameters.side === 'both' ? 'both arms' : log.parameters.side === 'L' ? 'left arm' : 'right arm'
  return (
    <div className="shape-log">
      <div className="field-label">Separated arm</div>
      <p>
        {log.operation}: {side}, gap {Math.round(log.parameters.gap * 1000)} mm, cut starts {Math.round(log.parameters.start * 100)} percent down the upper arm.
      </p>
      {log.messages.map((m) => (
        <p key={m}>{m}</p>
      ))}
      <p className="muted">
        Pieces {e.pieces ?? '-'}, open edges {e.open_edges ?? '-'}, bad edges {e.bad_edges ?? '-'}, new crossings {e.new_crossings ?? '-'}, caps placed {e.caps_placed}.
      </p>
    </div>
  )
}
