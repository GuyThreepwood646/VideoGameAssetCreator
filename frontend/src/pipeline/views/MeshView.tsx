import { useEffect, useId, useRef, useState } from 'react'
import { useBlocker } from 'react-router'
import { api, creditText, libraryUrl, type Asset, type LibraryHairMetrics, type ShapeLog, type Version } from '../../api'
import { Info } from '../../components/Info'
import { aboutSize, gridReadout, measuredSize, sizeAxisOf } from '../../help'
import { versionLook } from '../../recipe'
import Viewer from '../../Viewer'
import { usePaint } from '../../paint/usePaint'
import { clearActiveEditor, setActiveEditor, useEditKeys, type KeyTarget } from '../../editHistory'
import { confirmLeaveUnsavedPaint, setUnsavedPaint } from '../../unsavedPaint'
import { paintSaveBody } from '../../paint/savePaint'
import type { ViewerModel } from '../../viewerTool'
import { HairPicker, kindWords } from '../HairPicker'
import { SmoothPaint, TouchUp } from './TouchUp'
import { FaceSheet } from '../../components/FaceSheet'
import { ShapeLogNote } from './ShapeTab'
import type { FixAction } from '../../help'

const TEXTURE_KEYS = ['base_color', 'normal', 'orm', 'metallic_roughness', 'occlusion', 'ao', 'roughness', 'metallic', 'emissive']
const TEXTURE_NAMES: Record<string, string> = {
  base_color: 'colour',
  normal: 'bumps',
  orm: 'surface',
  metallic_roughness: 'metal and shine',
  occlusion: 'shadowing',
  ao: 'shadowing',
  roughness: 'shine',
  metallic: 'metal',
  emissive: 'glow',
}

// The industry term shown muted after the plain name (UX-04).
const TEXTURE_TECH: Record<string, string> = {
  base_color: 'base colour',
  normal: 'normal map',
  orm: 'ORM pack',
  metallic_roughness: 'metallic-roughness',
  occlusion: 'ambient occlusion',
  ao: 'ambient occlusion',
  roughness: 'roughness, inverse of shine',
  metallic: 'metallic',
  emissive: 'emissive',
}

// Hair from the library at Clean up (metrics.hair_library on the prep version).

export function MeshView({ version, category, asset, hairFocus, onFix, onChanged }: { version: Version; category?: string; asset?: Asset; hairFocus?: number | null; onFix?: (fix: FixAction) => void; onChanged?: () => void }) {
  // With a library hairstyle the viewer shows the body with its hair (with_hair_glb), else the model as it is.
  const glb = libraryUrl(version.files.with_hair_glb ?? version.files.glb)
  const libHair = version.stage === 'prep' ? ((version.metrics?.hair_library ?? null) as LibraryHairMetrics | null) : null
  const libHairOn = !!libHair?.attached
  const hairFit = version.stage === 'prep' ? libraryUrl(version.files.hair_fit_sheet) : null
  const showPicker = version.stage === 'mesh' && !!asset && (version.params?.hair_source === 'library' || !!version.metrics?.hair_library)
  // Prep sets the size (3.2): "About 1.8 m tall", measured on the category's axis.
  const size = version.stage === 'prep' && category ? measuredSize(version.metrics, category) : null
  // Scenery on a grid: "On the grid: 3 x 1 squares, 3 m tall".
  const onGrid = version.stage === 'prep' && category && sizeAxisOf(category) === 'grid' ? gridReadout(version.metrics) : null
  const tiling = version.stage === 'prep' ? libraryUrl(version.files.tiling) : null
  // The face detail pass's before and after picture (clean-up step, characters with a face).
  const faceSheet = version.stage === 'prep' ? libraryUrl(version.files.face_before_after) : null
  // The close-up of the painted face the pass drew and checked, when it was used; and the face's texels across.
  const faceCloseup = version.stage === 'prep' ? libraryUrl(version.files.face_closeup) : null
  const faceMetrics = (version.metrics?.face_detail ?? null) as { face_texels_after?: number; density?: { level?: string; note?: string | null }; closeup?: { own?: boolean; used?: boolean; notes?: string[] } } | null
  const ownCloseup = !!faceMetrics?.closeup?.own && !!faceMetrics.closeup.used
  // The hair detail pass's pictures: before and after, and what was taken as hair; or why it stood back.
  const hairSheet = version.stage === 'prep' ? libraryUrl(version.files.hair_before_after) : null
  const hairMask = version.stage === 'prep' ? libraryUrl(version.files.hair_mask) : null
  const hairMetrics = (version.metrics?.hair_detail ?? null) as { ran?: boolean; texels_per_cm_before?: number; texels_per_cm_after?: number } | null
  const hairMaterial = (version.metrics?.hair_material ?? null) as { use?: boolean; tex?: number; texels_per_cm?: number } | null
  const textures = TEXTURE_KEYS.filter((k) => k in version.files)
  const iou = version.metrics.silhouette_iou
  const tris = typeof version.metrics.triangles === 'number' ? version.metrics.triangles : null
  // Where the chosen look changed the result (fewer triangles, small or crisp textures, flat faces, no bumps).
  const look = versionLook(version)
  // A change described at this step edits the picture first; the model is built from the edited picture.
  const editedPicture = version.stage === 'mesh' && version.params?.picture_edit ? libraryUrl(version.files.edited_picture ?? version.files.edited_cutout) : null
  // The model drawn grey with the texture off (stages/mesh.py measure_geometry): a texture can hide torn or shattered surfaces.
  const greyViews = version.stage === 'mesh' ? libraryUrl(version.files.grey_sheet) : null
  // Touch up (WP-22): step 4 only. The viewer reports its model; painting is on while the user has started it.
  const [model, setModel] = useState<ViewerModel | null>(null)
  const [painting, setPainting] = useState(false)
  const paint = usePaint(model, look?.look ?? null)
  const canPaint = version.stage === 'prep' && !!glb
  const paintTool = painting && model ? paint.toolFor(model) : null
  // Save (WP-23): the painted picture goes to the backend as a new clean-up version made from this one.
  const [saving, setSaving] = useState(false)
  const [saveNote, setSaveNote] = useState<string | null>(null)
  const savePaint = async () => {
    const maps = paint.saveMaps()
    if (!maps) return
    const body = paintSaveBody(maps)
    if (!body) {
      setSaveNote('Nothing is painted yet.')
      return
    }
    setSaving(true)
    setSaveNote(null)
    try {
      await api.paint(version.id, body)
      paint.markSaved()
      setSaveNote('Saved. The new version appears here when it is made. Keep it to carry the paint on to the rig and the export.')
      onChanged?.()
    } catch (e) {
      setSaveNote(e instanceof Error ? e.message : 'Could not save the paint. Try again.')
    } finally {
      setSaving(false)
    }
  }
  // Undo and redo keys (WP-71): the paint is the editor in use once the person has used this panel.
  const paintRef = useRef(paint)
  useEffect(() => {
    paintRef.current = paint
  })
  const keys = useRef<KeyTarget>({ undo: () => paintRef.current.undo(), redo: () => paintRef.current.redo() })
  const activate = () => setActiveEditor(keys.current)
  useEditKeys()
  useEffect(() => () => clearActiveEditor(keys.current), [])
  // Unsaved paint: a plain question before leaving this page, and the browser warns when the tab closes.
  const owner = useId()
  const unsaved = paint.dirty
  useEffect(() => {
    setUnsavedPaint(owner, unsaved)
    return () => setUnsavedPaint(owner, false)
  }, [owner, unsaved])
  useEffect(() => {
    if (!unsaved) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])
  const blocker = useBlocker(({ currentLocation, nextLocation }) => unsaved && currentLocation.pathname !== nextLocation.pathname)
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (confirmLeaveUnsavedPaint()) blocker.proceed()
    else blocker.reset()
  }, [blocker])
  return (
    <div className="mesh-view" onPointerDownCapture={activate} onFocusCapture={activate}>
      {glb ? (
        <Viewer url={glb} height={440} tool={paintTool} onToolExit={() => setPainting(false)} onModelLoaded={setModel} />
      ) : (
        <div className="placeholder-box">No 3D model in this version.</div>
      )}
      {canPaint && <TouchUp paint={paint} painting={painting} onStart={() => setPainting(true)} onStop={() => setPainting(false)} onSave={savePaint} saving={saving} saveNote={saveNote} />}
      {version.stage === 'prep' && version.metrics?.shape_log != null && <ShapeLogNote log={version.metrics.shape_log as ShapeLog} />}
      {canPaint && version.metrics?.painted === true && <SmoothPaint versionId={version.id} smoothed={typeof version.metrics?.polished === 'string' ? version.metrics.polished : null} onDone={onChanged} />}
      {showPicker && asset && <HairPicker asset={asset} version={version} focus={hairFocus} onFix={onFix} onChanged={onChanged} />}
      <div className="mesh-side">
        {libHairOn && libHair && (
          <div>
            <div className="field-label">
              Hairstyle (Hair from the library)
              <Info term="term.hair_library" />
            </div>
            <div className="small">
              "{libHair.name ?? (libHair.style ?? '').replace(/_/g, ' ')}": {kindWords(libHair.kind ?? 'shell').toLowerCase()}
              {typeof libHair.triangles === 'number' ? `, ${libHair.triangles.toLocaleString('en-US')} triangles` : ''}
              {typeof libHair.cap_triangles === 'number' ? ` including a ${libHair.cap_triangles.toLocaleString('en-US')}-triangle scalp cap (a hair-coloured layer under the hair)` : ''}, in its own material.
            </div>
            {creditText(libHair.credit) && <div className="small muted">Credit: {creditText(libHair.credit)}</div>}
            {libHair.reused_body_of && <div className="small muted">Only the hair was fitted again; the body is the same as before.</div>}
            {hairFit && (
              <div className="small">
                <a href={hairFit} target="_blank" rel="noreferrer">
                  How the hairstyle was fitted to the head
                </a>
              </div>
            )}
          </div>
        )}
        <div>
          <div className="field-label">
            Model
            <Info term="term.triangles" />
          </div>
          <div className="small">
            {tris !== null ? `Your model: ${tris.toLocaleString('en-US')} triangles` : 'triangle count unknown'}
            {libHairOn && tris !== null && typeof libHair?.triangles === 'number' ? ` in the body and ${libHair.triangles.toLocaleString('en-US')} in the hair` : ''}
            {typeof version.metrics.texture_size === 'number' ? `, ${version.metrics.texture_size} px texture` : ''}
            {version.stage === 'mesh' && version.params?.head_room ? ' (over the budget on purpose: the clean-up step brings it back down and gives the head a bigger share of the triangles)' : ''}
          </div>
        </div>
        {greyViews && (
          <div>
            <div className="field-label">The shape without the texture</div>
            <a href={greyViews} target="_blank" rel="noreferrer" className="tiling-thumb" title="Four views of the model in plain grey">
              <img src={greyViews} alt="Four views of the model in plain grey with the texture off" />
            </a>
            <div className="small muted">A texture can hide a torn or broken surface, so look here for holes, shards and loose pieces.</div>
          </div>
        )}
        {editedPicture && (
          <div>
            <div className="field-label">
              Built from the edited picture
              <Info term="term.instruction" />
            </div>
            <a href={editedPicture} target="_blank" rel="noreferrer" className="tiling-thumb" title="The picture as edited for this version">
              <img src={editedPicture} alt="The picture edited as described, which this model was built from" />
            </a>
            <div className="small muted">
              {typeof version.params?.instruction === 'string' && version.params.instruction ? `"${version.params.instruction}" was drawn into the picture first, then the model was built from it.` : 'The picture was edited first, then the model was built from it.'}
            </div>
          </div>
        )}
        {look && (
          <div>
            <div className="field-label">
              Look
              <Info term="term.recipe" />
            </div>
            <div className={'small' + (look.ok ? '' : ' warn-text')}>
              {look.sentence}
            </div>
          </div>
        )}
        {size && (
          <div>
            <div className="field-label">
              Size in the game
              <Info term="term.size_m" />
            </div>
            <div className="small">
              {aboutSize(size.value, size.word)}
              {libHairOn && typeof libHair?.extra_height_m === 'number' && libHair.extra_height_m > 0.005 ? `, about ${Math.round(libHair.extra_height_m * 100)} cm more with the hair` : ''}
            </div>
          </div>
        )}
        {onGrid && (
          <div>
            <div className="field-label">
              Size in the game
              <Info term="term.grid_footprint" />
            </div>
            <div className="small">{onGrid}</div>
          </div>
        )}
        {tiling && (
          <div>
            <div className="field-label">
              How it tiles
              <Info term="term.grid" />
            </div>
            <a href={tiling} target="_blank" rel="noreferrer" className="tiling-thumb" title="Three copies side by side">
              <img src={tiling} alt="Three copies of the piece side by side" />
            </a>
            <div className="small muted">Three copies side by side, to show the edges meet.</div>
          </div>
        )}
        {faceSheet && (
          <div>
            <div className="field-label">
              Face: before and after
              <Info term="term.face_detail" />
            </div>
            <a href={faceSheet} target="_blank" rel="noreferrer" className="tiling-thumb" title="Open the full picture">
              <FaceSheet src={faceSheet} alt="The face from the picture, the 3D face before, and the 3D face after" />
            </a>
            <div className="small muted">The picture, the 3D face before, and after it was painted from the picture.</div>
            {faceMetrics?.face_texels_after ? (
              <div className="small muted">
                {faceMetrics.face_texels_after} texels (texture pixels) across the face ({faceMetrics.density?.level === 'high' ? 'High' : 'Standard'} face sharpness).
                {faceMetrics.density?.note ? ' ' + faceMetrics.density.note : ''}
              </div>
            ) : null}
            {faceCloseup && (
              <div className="small">
                <a href={faceCloseup} target="_blank" rel="noreferrer">
                  {ownCloseup ? 'Your own close-up of the face, which it was painted from' : 'The close-up of the face it was painted from'}
                </a>
                {ownCloseup && faceMetrics?.closeup?.notes?.length ? <span className="muted"> (it differs from the painting: {faceMetrics.closeup.notes.join('; ')})</span> : null}
              </div>
            )}
          </div>
        )}
        {hairSheet && (
          <div>
            <div className="field-label">
              Hair: before and after
              <Info term="term.hair_detail" />
            </div>
            <a href={hairSheet} target="_blank" rel="noreferrer" className="tiling-thumb" title="Open the full picture">
              <img src={hairSheet} alt="The hair before and after its strands and shading were painted, plain and lit" />
            </a>
            <div className="small muted">The hair from five sides before and after, then lit, before and after.</div>
            {hairMetrics?.texels_per_cm_after ? (
              <div className="small muted">
                {hairMetrics.texels_per_cm_before} texels (texture pixels) per cm on the hair before,{' '}
                {hairMaterial?.use ? hairMaterial.texels_per_cm : hairMetrics.texels_per_cm_after} now.
              </div>
            ) : null}
            {hairMaterial?.use ? (
              <div className="small muted">
                Hair material (its own texture and shader): a {hairMaterial.tex} px texture for the hair alone, one more draw call per character.
              </div>
            ) : null}
            {hairMask && (
              <div className="small">
                <a href={hairMask} target="_blank" rel="noreferrer">
                  {hairMaterial?.use ? 'What was taken as hair, and what has its own material' : 'What was taken as hair'}
                </a>
              </div>
            )}
          </div>
        )}
        {iou !== null && typeof iou === 'object' && (
          <div>
            <div className="field-label">
              Outline match per view
              <Info term="term.silhouette_iou" />
            </div>
            <table className="table">
              <tbody>
                {Object.entries(iou as Record<string, unknown>).map(([k, v]) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td className={'mono ' + (typeof v === 'number' && v < 0.8 ? 'bad-text' : '')}>{typeof v === 'number' ? `${Math.round(v * 100)}%` : String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {version.stage === 'prep' && version.files.base_color && version.files.base_color_original && (
          <div>
            <div className="field-label">
              Painted-on shadows
              <Info term="term.baked_light" />
            </div>
            <div className="texture-row before-after">
              {[
                ['base_color_original', 'Before: shadows painted in'],
                ['base_color', 'After: shadows removed'],
              ].map(([k, label]) => (
                <a key={k} href={libraryUrl(version.files[k])} target="_blank" rel="noreferrer" className="texture-thumb" title={label}>
                  <img src={libraryUrl(version.files[k])} alt={label} />
                  <span className="small">{label}</span>
                </a>
              ))}
            </div>
          </div>
        )}
        {textures.length > 0 && (
          <div>
            <div className="field-label">
              Textures
              <Info term="term.texture" />
            </div>
            <div className="texture-row">
              {textures.map((k) => (
                <a key={k} href={libraryUrl(version.files[k])} target="_blank" rel="noreferrer" className="texture-thumb" title={k}>
                  <img src={libraryUrl(version.files[k])} alt={k} />
                  <span className="small">
                    {TEXTURE_NAMES[k] ?? k.replace(/_/g, ' ')}
                    {TEXTURE_TECH[k] && <span className="tech-term"> {TEXTURE_TECH[k]}</span>}
                  </span>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
