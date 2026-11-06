import { creditText, libraryUrl, type Asset, type LibraryHairMetrics, type Version } from '../../api'
import { Info } from '../../components/Info'
import { aboutSize, gridReadout, measuredSize, sizeAxisOf } from '../../help'
import { versionLook } from '../../recipe'
import Viewer from '../../Viewer'
import { HairPicker, kindWords } from '../HairPicker'
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
  const faceMetrics = (version.metrics?.face_detail ?? null) as { face_texels_after?: number; density?: { level?: string; note?: string | null } } | null
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
  return (
    <div className="mesh-view">
      {glb ? <Viewer url={glb} height={440} /> : <div className="placeholder-box">No 3D model in this version.</div>}
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
            {tris !== null ? `${tris.toLocaleString('en-US')} triangles` : 'triangle count unknown'}
            {libHairOn && tris !== null && typeof libHair?.triangles === 'number' ? ` in the body and ${libHair.triangles.toLocaleString('en-US')} in the hair` : ''}
            {typeof version.metrics.texture_size === 'number' ? `, ${version.metrics.texture_size} px texture` : ''}
            {version.stage === 'mesh' && version.params?.head_room ? ' (over the budget on purpose: the clean-up step brings it back and gives the head more of them)' : ''}
          </div>
        </div>
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
              {look.crisp && <Info term="term.texture_crisp" />}
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
              <img src={faceSheet} alt="The face from the picture, the 3D face before, and the 3D face after" />
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
                  The close-up of the face it was painted from
                </a>
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
                  <span className="small">{TEXTURE_NAMES[k] ?? k.replace(/_/g, ' ')}</span>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
