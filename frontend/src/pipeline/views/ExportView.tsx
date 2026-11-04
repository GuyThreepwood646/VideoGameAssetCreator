import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { Chip, Disclosure } from '../../components/ui'
import Viewer from '../../Viewer'

type Lod = { level?: number; triangles?: number; screen_height?: number; ratio?: number }
type UnityReport = { ok?: boolean; prefab?: string; avatar?: string; warnings?: string[]; errors?: string[] }

const FILE_NAMES: Record<string, string> = {
  fbx: 'FBX (most engines)',
  glb: 'GLB (web and Godot)',
  prefab: 'Unity prefab',
  unitypackage: 'Unity package',
  base_color: 'colour texture',
  normal: 'bump texture',
  orm: 'surface texture',
  ao: 'shadow texture',
  metallic: 'metal texture',
  roughness: 'shine texture',
  sprite_atlas: 'sprite atlas',
  sprite_sheet: 'sprite sheet',
  manifest: 'manifest',
}

export function ExportView({ version }: { version: Version }) {
  const glb = libraryUrl(version.files.glb)
  const lods = Array.isArray(version.metrics.lods) ? (version.metrics.lods as Lod[]) : []
  const unity = (version.metrics.unity_report ?? null) as UnityReport | null
  const sheet = libraryUrl(version.files.sprite_sheet ?? version.files.sheet)
  const clips = Array.isArray(version.metrics.clips) ? (version.metrics.clips as string[]) : []
  const lod0 = lods[0]?.triangles
  const fileKeys = ['fbx', 'glb', 'prefab', 'unitypackage', 'manifest', 'base_color', 'normal', 'orm', 'ao', 'metallic', 'roughness', 'sprite_atlas', 'sprite_sheet'].filter((k) => version.files[k])

  return (
    <div className="export-view">
      <div className="ready-box">
        <div className="ready-title">Your asset is ready</div>
        <div className="muted small">Download the files for your game engine. FBX works in Unity, Unreal and Godot; GLB works on the web.</div>
        <div className="deliverables">
          {fileKeys.map((k) => (
            <a key={k} className="btn" href={libraryUrl(version.files[k])} download={version.files[k].split('/').pop()} title={version.files[k]}>
              {FILE_NAMES[k] ?? k}
            </a>
          ))}
          {fileKeys.length === 0 && <span className="muted small">No files in this version.</span>}
        </div>
      </div>
      {glb ? <Viewer url={glb} height={400} /> : <div className="placeholder-box">No 3D model in this version.</div>}
      <Disclosure label="What is inside">
        <div className="export-grid">
          <div>
            <div className="field-label">
              Detail levels
              <Info term="term.lod" />
            </div>
            {lods.length ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>Level</th>
                    <th>Triangles</th>
                    <th>of full</th>
                    <th>Used when taller than</th>
                  </tr>
                </thead>
                <tbody>
                  {lods.map((l, i) => (
                    <tr key={i}>
                      <td>{l.level ?? i}</td>
                      <td className="mono">{l.triangles?.toLocaleString('en-US') ?? '?'}</td>
                      <td className="mono">{lod0 && l.triangles ? Math.round((l.triangles / lod0) * 100) + '%' : ''}</td>
                      <td className="mono">{l.screen_height !== undefined ? `${Math.round(l.screen_height * 100)}% of screen` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="muted small">No detail-level data.</div>
            )}
          </div>
          <div>
            <div className="field-label">
              Unity check
              <Info term="term.unity_report" />
            </div>
            {unity ? (
              <div className="unity-report">
                <div className="row-gap">
                  <Chip tone={unity.ok ? 'ok' : 'bad'}>{unity.ok ? 'works in Unity' : 'failed in Unity'}</Chip>
                  {unity.avatar && <Chip>skeleton: {unity.avatar}</Chip>}
                  {clips.length > 0 && <Chip>{clips.length} animations</Chip>}
                </div>
                {unity.prefab && <div className="mono small">{unity.prefab}</div>}
                {unity.errors && unity.errors.length > 0 && (
                  <ul className="checks">
                    {unity.errors.map((w, i) => (
                      <li key={i} className="fail">
                        <span className="fail-mark">ERROR</span> {w}
                      </li>
                    ))}
                  </ul>
                )}
                {unity.warnings && unity.warnings.length > 0 && (
                  <ul className="checks">
                    {unity.warnings.map((w, i) => (
                      <li key={i}>
                        <span className="warn-mark">NOTE</span> {w}
                      </li>
                    ))}
                  </ul>
                )}
                {clips.length > 0 && (
                  <div className="small muted">
                    animations: {clips.join(', ')}
                    <Info term="term.clips" />
                  </div>
                )}
              </div>
            ) : (
              <div className="muted small">No Unity report.</div>
            )}
          </div>
        </div>
      </Disclosure>
      {sheet && (
        <div className="sprite-sheet">
          <div className="field-label">
            Sprite sheet
            <Info term="term.sprites" />
          </div>
          <div className="strip-scroll checker">
            <img src={sheet} alt="sprite sheet" />
          </div>
        </div>
      )}
    </div>
  )
}
