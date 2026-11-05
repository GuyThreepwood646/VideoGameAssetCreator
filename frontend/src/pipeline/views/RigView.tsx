import { useEffect, useMemo, useState } from 'react'
import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { Disclosure } from '../../components/ui'
import { joinAnd, movesLine, movesPlain, movesSourceLine } from '../../help'
import { MovesPanel } from '../MovesPanel'
import Viewer, { hitShapesOf, type BoneSegment, type BonesSpace, type ClipInfo, type HitboxDoc } from '../../Viewer'

type RigInfo = { template?: string; bone_segments?: BoneSegment[]; bone_segments_gltf?: BoneSegment[]; bones?: BoneSegment[] }
// metrics.hitboxes on the rig version (4.2).
type HitboxSummary = { count?: number; mode?: string; fit?: string; fallback?: boolean; failed?: boolean; regions_found?: ({ id?: string; label?: string } | string)[] }

// A JSON file of this version, fetched once per url; null until it arrives or when it is missing.
function useJsonFile<T>(url: string | undefined): { data: T | null; error: string | null } {
  const [state, setState] = useState<{ data: T | null; error: string | null }>({ data: null, error: null })
  useEffect(() => {
    let alive = true
    setState({ data: null, error: null })
    if (!url) return
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<T>
      })
      .then((d) => alive && setState({ data: d, error: null }))
      .catch((e) => alive && setState({ data: null, error: e instanceof Error ? e.message : String(e) }))
    return () => {
      alive = false
    }
  }, [url])
  return state
}

// The hit box line under the intro (4.2 states): null when there is nothing to say (no skeleton, older versions).
function hitboxLine(summary: HitboxSummary | null, doc: HitboxDoc | null): { text: string; warn: boolean } | null {
  const mode = summary?.mode ?? doc?.mode
  const count = typeof summary?.count === 'number' ? summary.count : (doc?.count ?? (doc ? hitShapesOf(doc).length : undefined))
  if (summary?.failed) return { text: 'We could not fit hit boxes on this skeleton; it still has its body shape for moving.', warn: true }
  if (mode === 'none') return { text: 'No hit boxes. The body shape still keeps it out of walls.', warn: false }
  if (count === undefined || count === 0) return null
  if (summary?.fallback ?? doc?.fallback) return { text: 'Hit boxes: one for the body and one for the head. We could not tell the limbs apart on this skeleton.', warn: false }
  return { text: `Hit boxes: ${count}, fitted to the body. They move with the skeleton so hits land where they look.`, warn: false }
}

export function RigView({ version }: { version: Version }) {
  const animGlb = libraryUrl(version.files.anim_glb)
  const glb = animGlb ?? libraryUrl(version.files.glb)
  const rigInfoUrl = libraryUrl(version.files.rig_info)
  const strip = libraryUrl(version.files.bend_strip ?? version.files.bend_test)
  const animStrip = libraryUrl(version.files.anim_strip)
  const [bones, setBones] = useState<BoneSegment[] | null>(null)
  const [bonesSpace, setBonesSpace] = useState<BonesSpace>('gltf')
  const [rigError, setRigError] = useState<string | null>(null)
  const hitFile = useJsonFile<HitboxDoc>(libraryUrl(version.files.hitboxes))
  const clipFile = useJsonFile<{ clips?: ClipInfo[] }>(animGlb ? libraryUrl(version.files.anim_clips) : undefined)

  useEffect(() => {
    let alive = true
    setBones(null)
    setRigError(null)
    if (!rigInfoUrl) return
    fetch(rigInfoUrl)
      .then((r) => {
        if (!r.ok) throw new Error(`rig_info HTTP ${r.status}`)
        return r.json() as Promise<RigInfo>
      })
      .then((info) => {
        if (!alive) return
        // Prefer the glTF-axis segments; older rig_info only has Blender's (the viewer then also tries Z up).
        const gltf = Array.isArray(info.bone_segments_gltf) && info.bone_segments_gltf.length > 0
        const segs = (gltf ? info.bone_segments_gltf : (info.bone_segments ?? info.bones)) ?? []
        setBonesSpace(gltf ? 'gltf' : 'unknown')
        setBones(segs.filter((b) => Array.isArray(b.head) && Array.isArray(b.tail)))
      })
      .catch((e) => alive && setRigError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [rigInfoUrl])

  const m = version.metrics
  const boneCount = typeof m.bones === 'number' ? m.bones : bones?.length
  const template = version.params.template_label ?? m.template ?? version.params.template
  const summary = m.hitboxes && typeof m.hitboxes === 'object' ? (m.hitboxes as HitboxSummary) : null
  const hitDoc = hitFile.data && hitFile.data.mode !== 'none' ? hitFile.data : null
  const hbLine = hitboxLine(summary, hitFile.data)
  const hitNames = hitShapesOf(hitDoc).map((h) => h.label || h.region || h.name || '?')
  // The rig stage renames humanoid bones by geometry (metrics.rig_restore.bone_names).
  const restore = m.rig_restore && typeof m.rig_restore === 'object' ? (m.rig_restore as { bone_names?: { corrected?: boolean } }) : null
  const namesFixed = restore?.bone_names?.corrected === true
  const moves = movesLine(m.clips)
  const movesFrom = movesSourceLine(m)
  const missing = movesPlain(m.missing_clips)
  const clipData = clipFile.data
  const clipInfo = useMemo(() => (Array.isArray(clipData?.clips) ? clipData.clips.filter((c) => c && typeof c.name === 'string') : null), [clipData])

  return (
    <div className="rig-view">
      <div className="view-intro muted small">
        Does the movement look natural? Press play in the viewer, or look at the bent poses below. {animGlb ? 'The viewer shows the animated model.' : ''}
        <Info term="term.skeleton" />
      </div>
      {(hbLine || moves || missing.length > 0 || namesFixed) && (
        <div className="rig-lines small">
          {namesFixed && (
            <div>
              Bone names: corrected to the standard names, so other tools recognise the skeleton.
              <Info term="term.bone_names" />
            </div>
          )}
          {hbLine && (
            <div>
              {hbLine.warn && <span className="warn-mark">CHECK</span>}
              {hbLine.text}
              <Info term="term.hitboxes" />
            </div>
          )}
          {moves && (
            <div>
              {moves}
              <Info term="term.moves" />
            </div>
          )}
          {moves && movesFrom && (
            <div>
              {movesFrom}
              <Info term="term.clips_source" />
            </div>
          )}
          {missing.length > 0 && (
            <div>
              <span className="warn-mark">CHECK</span>
              Some moves could not be made: {joinAnd(missing)}.
            </div>
          )}
        </div>
      )}
      <MovesPanel version={version} />
      {glb ? <Viewer url={glb} bones={bones} bonesSpace={bonesSpace} height={440} hitboxes={hitDoc} clipInfo={clipInfo} defaultClip={animGlb ? 'walk' : undefined} /> : <div className="placeholder-box">No 3D model in this version.</div>}
      {rigError && <div className="muted small">Bones overlay unavailable: {rigError}</div>}
      {hitFile.error && version.files.hitboxes && <div className="muted small">Hit boxes overlay unavailable: {hitFile.error}</div>}
      <div className="rig-side">
        <div>
          <div className="field-label">
            Skeleton
            <Info term="term.bones" />
          </div>
          <div className="small">
            {boneCount !== undefined ? `${boneCount} bones` : 'bone count unknown'}
            {template ? `, ${String(template)} skeleton` : ''}
          </div>
          {bones && bones.length > 0 && (
            <Disclosure label="Bone names">
              <div className="bone-list small mono">{bones.map((b) => b.name ?? '?').join(', ')}</div>
            </Disclosure>
          )}
          {hitNames.length > 0 && (
            <Disclosure label="Hit box list">
              <ul className="small">
                {hitNames.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </Disclosure>
          )}
        </div>
      </div>
      {animStrip && (
        <div className="bend-strip">
          <div className="field-label">
            Animation frames
            <Info term="term.clips" />
          </div>
          <div className="strip-scroll">
            <img src={animStrip} alt="animation strip" />
          </div>
        </div>
      )}
      {strip && (
        <div className="bend-strip">
          <div className="field-label">
            Bent poses (35 and 90 degrees per limb)
            <Info term="term.bend_test" />
          </div>
          <div className="strip-scroll">
            <img src={strip} alt="bend test strip" />
          </div>
        </div>
      )}
    </div>
  )
}
