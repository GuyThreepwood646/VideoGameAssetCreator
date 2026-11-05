import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { aboutSize, measuredSize } from '../../help'
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
  const textures = TEXTURE_KEYS.filter((k) => k in version.files)
  const iou = version.metrics.silhouette_iou
  const tris = typeof version.metrics.triangles === 'number' ? version.metrics.triangles : null
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
          </div>
        </div>
        {size && (
          <div>
            <div className="field-label">
              Size in the game
              <Info term="term.size_m" />
            </div>
            <div className="small">{aboutSize(size.value, size.word)}</div>
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
