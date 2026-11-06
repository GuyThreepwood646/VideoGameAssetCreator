import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { aboutSize, gridReadout, measuredSize, sizeAxisOf } from '../../help'
import { versionLook } from '../../recipe'
import Viewer from '../../Viewer'

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

export function MeshView({ version, category }: { version: Version; category?: string }) {
  const glb = libraryUrl(version.files.glb)
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
  return (
    <div className="mesh-view">
      {glb ? <Viewer url={glb} height={440} /> : <div className="placeholder-box">No 3D model in this version.</div>}
      <div className="mesh-side">
        <div>
          <div className="field-label">
            Model
            <Info term="term.triangles" />
          </div>
          <div className="small">
            {tris !== null ? `${tris.toLocaleString('en-US')} triangles` : 'triangle count unknown'}
            {typeof version.metrics.texture_size === 'number' ? `, ${version.metrics.texture_size} px texture` : ''}
            {version.stage === 'mesh' && version.params?.head_room ? ' (over the budget on purpose: the clean-up step brings it back and gives the head more of them)' : ''}
          </div>
        </div>
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
            <div className="small">{aboutSize(size.value, size.word)}</div>
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
