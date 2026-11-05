import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { Chip, Disclosure, TrafficDot } from '../../components/ui'
import { joinAnd } from '../../help'
import Viewer from '../../Viewer'
import { collisionPhrase, controllerParams, CYCLIC_CLIP, unityClips, unityReport, type UnityReport } from '../finish'

type Lod = { level?: number; triangles?: number; screen_height?: number; ratio?: number }

const FILE_NAMES: Record<string, string> = {
  fbx: 'FBX (most engines)',
  fbx_unreal: 'Unreal FBX with collision shapes',
  glb: 'GLB (web, Blender)',
  glb_godot: 'GLB for Godot (with collision)',
  glb_lod1: 'detail level 1 (GLB)',
  glb_lod2: 'detail level 2 (GLB)',
  glb_lod3: 'detail level 3 (GLB)',
  prefab: 'Unity prefab',
  unitypackage: 'Unity package',
  base_color: 'colour texture',
  normal: 'bump texture',
  metallic_smoothness: 'metal and shine texture (Unity)',
  orm: 'surface texture (Unreal, Godot, web)',
  ao: 'shadow texture',
  metallic: 'metal texture',
  roughness: 'roughness texture',
  sprite_atlas: 'sprite atlas',
  sprite_sheet: 'sprite sheet',
  manifest: 'manifest',
}

const DELIVERABLE_KEYS = ['fbx', 'fbx_unreal', 'glb', 'glb_godot', 'glb_lod1', 'glb_lod2', 'glb_lod3', 'prefab', 'unitypackage', 'manifest', 'base_color', 'normal', 'metallic_smoothness', 'orm', 'ao', 'metallic', 'roughness', 'sprite_atlas', 'sprite_sheet']
// Once the packed maps exist (3.1) the separate metal and roughness maps leave the download row; they stay in
// Details, Files.
const PACKED_REPLACES = new Set(['metallic', 'roughness'])

// The download buttons for an export version (also used by the finishing card).
export function Deliverables({ version }: { version: Version }) {
  const packed = !!(version.files.metallic_smoothness || version.files.orm)
  const fileKeys = DELIVERABLE_KEYS.filter((k) => version.files[k] && !(packed && PACKED_REPLACES.has(k)))
  return (
    <div className="deliverables">
      {fileKeys.map((k) => (
        <a key={k} className="btn" href={libraryUrl(version.files[k])} download={version.files[k].split('/').pop()} title={version.files[k]}>
          {FILE_NAMES[k] ?? k}
        </a>
      ))}
      {fileKeys.length === 0 && <span className="muted small">No files in this version.</span>}
    </div>
  )
}

// hideReady: the finishing card above already shows the title and the downloads.
export function ExportView({ version, hideReady = false }: { version: Version; hideReady?: boolean }) {
  const glb = libraryUrl(version.files.glb)
  const lods = Array.isArray(version.metrics.lods) ? (version.metrics.lods as Lod[]) : []
  const unity = unityReport(version)
  const sheet = libraryUrl(version.files.sprite_sheet ?? version.files.sheet)
  const clips = Array.isArray(version.metrics.clips) ? (version.metrics.clips as string[]) : []
  const lod0 = lods[0]?.triangles

  return (
    <div className="export-view">
      {!hideReady && (
        <div className="ready-box">
          <div className="ready-title">Your asset is ready</div>
          <div className="muted small">Download the files for your game engine. FBX works in Unity, Unreal and Godot; GLB works on the web and in Godot.</div>
          <Deliverables version={version} />
        </div>
      )}
      {!hideReady && (glb ? <Viewer url={glb} height={400} /> : <div className="placeholder-box">No 3D model in this version.</div>)}
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
                  {clips.length > 0 && <Chip>{clips.length} animations</Chip>}
                </div>
                <UnityLines report={unity} version={version} />
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

type UnityLine = { id: string; state: 'ok' | 'warn'; text: string; term: string; title?: string }

const listNames = (names: string[], max = 6): string => (names.length > max ? `${names.slice(0, max).join(', ')} and ${names.length - max} more` : joinAnd(names))

// What the Unity import says about metal and shine (3.1), looping clips and the controller (3.3), and whether the
// skeleton reads as a person (3.6). Each line shows only when the report has that field.
function unityLines(rep: UnityReport, version: Version): UnityLine[] {
  const out: UnityLine[] = []
  // Collision (3.4), from the shapes Unity put on the prefab.
  if (typeof rep.colliderCount === 'number' && Array.isArray(rep.colliderKinds)) {
    const phrase = rep.colliderCount > 0 ? collisionPhrase({ ...version, metrics: { collider_count: rep.colliderCount, collider_kinds: rep.colliderKinds, collider_shape: version.metrics.collider_shape } }, rep) : null
    if (phrase) out.push({ id: 'collision', state: 'ok', text: `Collision: ${phrase}.`, term: 'collider', title: `Technical: ${rep.colliderCount} collider(s): ${rep.colliderKinds.join(', ')}` })
  }
  if (rep.secondaryUV === true) out.push({ id: 'lightmap', state: 'ok', text: 'Ready for baked lighting.', term: 'lightmap_uv' })
  if (rep.lodFadeMode === 'CrossFade') out.push({ id: 'lodfade', state: 'ok', text: 'Detail levels fade smoothly.', term: 'lod_fade' })
  if (Array.isArray(rep.texturesAssigned)) {
    out.push(
      rep.texturesAssigned.includes('metallic_smoothness')
        ? { id: 'metal', state: 'ok', text: 'Metal and shine: connected, so metal parts look like metal.', term: 'metal_shine' }
        : { id: 'metal', state: 'warn', text: 'Metal and shine: not connected, so metal may look like plastic.', term: 'metal_shine' },
    )
  }
  const clips = unityClips(rep).filter((c) => typeof c.loop === 'boolean')
  if (clips.length) {
    const cyclic = clips.filter((c) => CYCLIC_CLIP.test(c.name))
    const stuck = cyclic.filter((c) => !c.loop).map((c) => c.name)
    const loops = clips.filter((c) => c.loop).map((c) => c.name)
    if (stuck.length) out.push({ id: 'loops', state: 'warn', text: `Does not loop: ${listNames(stuck)}, so ${stuck.length > 1 ? 'they' : 'it'} would stop after one cycle.`, term: 'loop' })
    else if (loops.length) out.push({ id: 'loops', state: 'ok', text: `Loops smoothly: ${listNames(loops)}.`, term: 'loop' })
  }
  if (rep.controller === null) {
    out.push({ id: 'controller', state: 'warn', text: 'No animation controller was built, so you would set one up in Unity.', term: 'animator_controller' })
  } else if (typeof rep.controller === 'string') {
    // An older importer reported only where it saved the controller.
    out.push({ id: 'controller', state: 'ok', text: 'Animation controller: built and attached to the prefab.', term: 'animator_controller', title: `Technical: ${rep.controller}` })
  } else if (rep.controller) {
    const params = controllerParams(rep)
    const triggers = params.filter((p) => p !== 'Speed')
    out.push(
      params.includes('Speed')
        ? { id: 'controller', state: 'ok', text: `Animation controller: Speed switches between standing, walking and running${triggers.length ? `; ${joinAnd(triggers)} ${triggers.length > 1 ? 'are triggers' : 'is a trigger'}` : ''}.`, term: 'animator_controller' }
        : { id: 'controller', state: 'ok', text: 'Animation controller: one state per animation, with no Speed switch.', term: 'animator_controller' },
    )
  }
  if (rep.avatar) {
    const human = /human/i.test(rep.avatar)
    // avatar_mapping is recorded for humanoids; generic there means the person mapping failed and it fell back.
    const fellBack = rep.avatar_mapping === 'generic' || rep.avatar_mapping === 'none'
    out.push({
      id: 'avatar',
      state: !human && fellBack ? 'warn' : 'ok',
      text: human ? 'Skeleton in Unity: Humanoid, so the shared human animations work.' : 'Skeleton in Unity: Generic, so it plays its own animations only.',
      term: 'avatar',
      title: rep.avatar_mapping ? `Technical: ${rep.avatar}, mapping ${rep.avatar_mapping}` : `Technical: ${rep.avatar}`,
    })
  }
  return out
}

function UnityLines({ report, version }: { report: UnityReport; version: Version }) {
  const lines = unityLines(report, version)
  if (!lines.length) return null
  return (
    <ul className="unity-lines">
      {lines.map((l) => (
        <li key={l.id} className={'unity-line ' + l.state} title={l.title}>
          <TrafficDot state={l.state} title={l.state === 'ok' ? 'checked' : 'needs a look'} />
          <span>
            {l.text}
            <Info term={'term.' + l.term} />
          </span>
        </li>
      ))}
    </ul>
  )
}
