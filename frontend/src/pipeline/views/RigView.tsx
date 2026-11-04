import { useEffect, useState } from 'react'
import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { Disclosure } from '../../components/ui'
import Viewer, { type BoneSegment, type BonesSpace } from '../../Viewer'

type RigInfo = { template?: string; bone_segments?: BoneSegment[]; bone_segments_gltf?: BoneSegment[]; bones?: BoneSegment[] }

export function RigView({ version }: { version: Version }) {
  const animGlb = libraryUrl(version.files.anim_glb)
  const glb = animGlb ?? libraryUrl(version.files.glb)
  const rigInfoUrl = libraryUrl(version.files.rig_info)
  const strip = libraryUrl(version.files.bend_strip ?? version.files.bend_test)
  const animStrip = libraryUrl(version.files.anim_strip)
  const [bones, setBones] = useState<BoneSegment[] | null>(null)
  const [bonesSpace, setBonesSpace] = useState<BonesSpace>('gltf')
  const [rigError, setRigError] = useState<string | null>(null)

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

  const boneCount = typeof version.metrics.bones === 'number' ? version.metrics.bones : bones?.length
  const template = version.params.template_label ?? version.metrics.template ?? version.params.template
  return (
    <div className="rig-view">
      <div className="view-intro muted small">
        Does the movement look natural? Press play in the viewer, or look at the bent poses below. {animGlb ? 'The viewer shows the animated model.' : ''}
        <Info term="term.skeleton" />
      </div>
      {glb ? <Viewer url={glb} bones={bones} bonesSpace={bonesSpace} height={440} /> : <div className="placeholder-box">No 3D model in this version.</div>}
      {rigError && <div className="muted small">Bones overlay unavailable: {rigError}</div>}
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
