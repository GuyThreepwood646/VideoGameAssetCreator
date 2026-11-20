import { useEffect, useMemo, useState } from 'react'
import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { Disclosure } from '../../components/ui'
import { joinAnd, movesLine, movesPlain, movesSourceLine } from '../../help'
import { MovesPanel } from '../MovesPanel'
import Viewer, { hitShapesOf, type BoneSegment, type BonesSpace, type ClipInfo, type HitboxDoc } from '../../Viewer'
import { pointTool } from '../../declaredPoints'
import type { ViewerModel } from '../../viewerTool'
import { DeclaredPointsPanel, useDeclaredPoints } from './DeclaredPoints'
import { growStiffRegion, regionTriangles } from '../../stiffRegion'
import type { RegionHighlight } from '../../regionOverlay'
import * as THREE from 'three'

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
  // A humanoid whose shared clip library is not on this PC (the check library_missing says the same in the check list).
  const libMissing = Array.isArray(m.library_missing) && m.library_missing.length > 0
  const clipData = clipFile.data
  const [clipReq, setClipReq] = useState<{ name: string; n: number } | null>(null)
  // Hanging parts (WP-20 step 5): while a part is being placed, the viewer's pointer tool takes the left button.
  const dp = useDeclaredPoints(version)
  const { setPoint, addStiffPoint, pending } = dp
  const [viewerModel, setViewerModel] = useState<ViewerModel | null>(null)
  const [markName, setMarkName] = useState<string | null>(null)
  // A stiff part takes one point per click and stays in placing mode; a sway part takes its one point and leaves it.
  const markKind = dp.parts?.find((p) => p.name === markName)?.kind ?? null
  const markTool = useMemo(
    () =>
      markName && viewerModel
        ? pointTool((p) => {
            if (markKind === 'stiff') addStiffPoint(markName, p)
            else {
              setPoint(markName, p)
              setMarkName(null)
            }
          })
        : null,
    [markName, markKind, viewerModel, setPoint, addStiffPoint],
  )
  // The stiff regions (WP-69) grown from every stiff point, saved or still pending, drawn over the model before keeping.
  const stiffPoints = useMemo(
    () => [...(dp.parts ?? []).filter((p) => p.kind === 'stiff').flatMap((p) => p.locations ?? []), ...Object.values(pending).flat()],
    [dp.parts, pending],
  )
  const regionHighlight = useMemo(() => growHighlight(viewerModel, stiffPoints), [viewerModel, stiffPoints])
  const clipInfo = useMemo(() => (Array.isArray(clipData?.clips) ? clipData.clips.filter((c) => c && typeof c.name === 'string') : null), [clipData])

  return (
    <div className="rig-view">
      <div className="view-intro muted small">
        Does the movement look natural? Press play in the viewer, or look at the bent poses below. {animGlb ? 'The viewer shows the animated model.' : ''}
        <Info term="term.skeleton" />
      </div>
      {(hbLine || moves || missing.length > 0 || namesFixed || libMissing) && (
        <div className="rig-lines small">
          {libMissing && (
            <div>
              <span className="warn-mark">CHECK</span>
              The shared animation library is not on this PC, so this character has only the built-in moves: no library walk, run or jump.
              To get it back, run setup\Setup-Extras.ps1 -Packs -Install, or unzip it into data\animations by hand. Then make the moves again.
            </div>
          )}
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
      {glb ? <Viewer url={glb} bones={bones} bonesSpace={bonesSpace} height={440} hitboxes={hitDoc} clipInfo={clipInfo} defaultClip={animGlb ? 'walk' : undefined} clipRequest={clipReq} tool={markTool} onToolExit={() => setMarkName(null)} onModelLoaded={setViewerModel} highlight={regionHighlight} /> : <div className="placeholder-box">No 3D model in this version.</div>}
      <DeclaredPointsPanel dp={dp} done={version.status === 'done'} markName={markName} onMark={setMarkName} />
      <MovesPanel version={version} previewClips={clipInfo ? clipInfo.map((c) => c.name) : null} onPlay={(name) => setClipReq((r) => ({ name, n: (r?.n ?? 0) + 1 }))} />
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
            {template ? `, ${/^mixamo$/i.test(String(template)) ? 'standard human skeleton (Mixamo)' : `${String(template)} skeleton`}` : ''}
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

// The region the stiff points cover on the model: the union of each point's grown piece (stiffRegion.ts), as the
// triangles to draw. Positions are the mesh's own rest coordinates, the same frame the points are stored in.
function growHighlight(model: ViewerModel | null, points: number[][]): RegionHighlight | null {
  if (!model || points.length === 0) return null
  let mesh: THREE.Mesh | null = null
  for (const m of model.meshes) {
    const n = m.geometry?.getAttribute('position')?.count ?? 0
    if (n && (!mesh || n > (mesh.geometry.getAttribute('position')?.count ?? 0))) mesh = m
  }
  if (!mesh) return null
  const geo = mesh.geometry
  const pos = geo.getAttribute('position') as THREE.BufferAttribute
  const flat = pos.array as Float32Array
  const index: ArrayLike<number> = geo.index ? (geo.index.array as ArrayLike<number>) : Array.from({ length: pos.count }, (_, i) => i)
  const mask = new Uint8Array(pos.count)
  for (const p of points) {
    if (p.length !== 3) continue
    const m = growStiffRegion(flat, index, [p[0], p[1], p[2]])
    for (let i = 0; i < mask.length; i++) if (m[i]) mask[i] = 1
  }
  const triangles = regionTriangles(index, mask)
  return triangles.length ? { mesh, triangles } : null
}
