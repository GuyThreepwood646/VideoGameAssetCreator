// Three.js GLB viewer: GLTFLoader with textures, OrbitControls, shading modes
// (textured, wireframe, normals), turntable, grid, SkeletonHelper when the GLB
// has a skeleton, an AnimationMixer with a clip dropdown when it has animations,
// and an optional bones overlay drawn from rig_info bone segments.
//
// Bone alignment: the overlay segments come from rig_info (Blender's view of the
// skeleton, converted to glTF axes), but the GLB the browser draws can carry its own
// transform on the armature or skinned mesh node, and some riggers write inverse bind
// matrices that cancel an armature offset. So the overlay is not trusted to line up
// as-is: at load we sample the rest-pose surface the way three.js actually draws it
// (skinning applied), try a few candidate corrections built from the GLB's own node
// matrices, and keep the one that puts the bones inside the surface. The same is done
// for the SkeletonHelper, which draws the GLB's live bones.
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export type BoneSegment = { name?: string; head: number[]; tail: number[] }
export type Shading = 'textured' | 'wireframe' | 'normals'
// 'gltf': segments already in glTF axes (rig_info bone_segments_gltf).
// 'unknown': older rig_info without them; may be Blender Z-up, so that is tried too.
export type BonesSpace = 'gltf' | 'unknown'

type Props = {
  url?: string
  bones?: BoneSegment[] | null
  bonesSpace?: BonesSpace
  height?: number | string
  showBonesDefault?: boolean
  className?: string
  // Start with the turntable on (the finishing moment).
  turntableDefault?: boolean
  // Prefer the clip whose name contains this text (case-insensitive), e.g. "walk".
  defaultClip?: string
}

type SceneHandles = {
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  controls: OrbitControls
  modelRoot: THREE.Group // rotates for the turntable
  frame: THREE.Group // offset so the model sits on the grid
  grid: THREE.GridHelper
  mixer: THREE.AnimationMixer | null
  clock: THREE.Clock
}

const normalMaterial = new THREE.MeshNormalMaterial()

function setShading(root: THREE.Object3D, shading: Shading, originals: Map<THREE.Mesh, THREE.Material | THREE.Material[]>) {
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh
    if (!mesh.isMesh) return
    if (!originals.has(mesh)) originals.set(mesh, mesh.material)
    const orig = originals.get(mesh)!
    if (shading === 'normals') {
      mesh.material = normalMaterial
      return
    }
    mesh.material = orig
    const mats = Array.isArray(orig) ? orig : [orig]
    for (const m of mats) if ('wireframe' in m) (m as THREE.MeshStandardMaterial).wireframe = shading === 'wireframe'
  })
}

function frameModel(h: SceneHandles, model: THREE.Object3D) {
  const box = new THREE.Box3().setFromObject(model)
  if (box.isEmpty()) return
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  // Sit the model on the grid, centred on the origin, by offsetting the frame group.
  h.frame.position.set(-center.x, -box.min.y, -center.z)
  const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1
  const dist = (radius / Math.sin((h.camera.fov * Math.PI) / 360)) * 1.3
  h.camera.position.set(dist * 0.8, dist * 0.6, dist * 0.8)
  h.camera.near = Math.max(0.01, dist / 1000)
  h.camera.far = dist * 100
  h.camera.updateProjectionMatrix()
  h.controls.target.set(0, size.y / 2, 0)
  h.controls.update()
  const gridSize = Math.max(2, Math.ceil(radius * 4))
  h.grid.scale.setScalar(gridSize / 10)
}

// ---------------------------------------------------------------------------
// Bone alignment (see the note at the top)
// ---------------------------------------------------------------------------

type Candidate = { name: string; m: THREE.Matrix4 }
type RestData = {
  verts: Float32Array // rest-pose surface sample, model-local, as three.js draws it
  candidates: Candidate[] // corrections to try, model-local; the first is identity
  skeletonPoints: THREE.Vector3[] | null // the GLB's own bones at rest, model-local
}

// Blender (x, y, z) -> glTF (x, z, -y)
const Z_UP_TO_Y_UP = new THREE.Matrix4().makeRotationX(-Math.PI / 2)
const MAX_SAMPLE = 1500

function sameMatrix(a: THREE.Matrix4, b: THREE.Matrix4): boolean {
  const ea = a.elements
  const eb = b.elements
  for (let i = 0; i < 16; i++) if (Math.abs(ea[i] - eb[i]) > 1e-5) return false
  return true
}

function captureRest(model: THREE.Object3D): RestData {
  model.updateMatrixWorld(true)
  const inv = model.matrixWorld.clone().invert()
  const rel = (o: THREE.Object3D) => inv.clone().multiply(o.matrixWorld)
  const meshes: THREE.Mesh[] = []
  model.traverse((o) => {
    const m = o as THREE.Mesh
    if (m.isMesh && m.geometry?.attributes.position) meshes.push(m)
  })
  const total = meshes.reduce((n, m) => n + m.geometry.attributes.position.count, 0)
  const stride = Math.max(1, Math.ceil(total / MAX_SAMPLE))
  const out: number[] = []
  const v = new THREE.Vector3()
  for (const m of meshes) {
    const r = rel(m)
    const pos = m.geometry.attributes.position
    for (let i = 0; i < pos.count; i += stride) {
      try {
        // For a SkinnedMesh this applies the bone transforms: the point as drawn.
        m.getVertexPosition(i, v)
      } catch {
        v.fromBufferAttribute(pos, i)
      }
      v.applyMatrix4(r)
      if (Number.isFinite(v.x) && Number.isFinite(v.y) && Number.isFinite(v.z)) out.push(v.x, v.y, v.z)
    }
  }

  const candidates: Candidate[] = [{ name: 'as exported', m: new THREE.Matrix4() }]
  const add = (name: string, m: THREE.Matrix4 | null) => {
    if (!m || Math.abs(m.determinant()) < 1e-12) return
    for (const [n, x] of [
      [name, m],
      [name + ' (inverse)', m.clone().invert()],
    ] as const) {
      if (!candidates.some((c) => sameMatrix(c.m, x))) candidates.push({ name: n, m: x })
    }
  }
  let skeletonPoints: THREE.Vector3[] | null = null
  const skinned = meshes.find((m) => (m as THREE.SkinnedMesh).isSkinnedMesh) as THREE.SkinnedMesh | undefined
  if (skinned) {
    add('skinned mesh', rel(skinned))
    if (skinned.parent && skinned.parent !== model) add('skinned mesh parent', rel(skinned.parent))
    const sk = skinned.skeleton
    if (sk && sk.bones.length) {
      let root: THREE.Object3D = sk.bones[0]
      while (root.parent && (root.parent as THREE.Bone).isBone) root = root.parent
      if (root.parent && root.parent !== model) add('armature node', rel(root.parent))
      // Where the skin puts the mesh data relative to the skeleton (identity when the bind matrices agree).
      if (sk.boneInverses[0]) add('bind offset', inv.clone().multiply(sk.bones[0].matrixWorld).multiply(sk.boneInverses[0]).multiply(skinned.bindMatrix))
      skeletonPoints = []
      const p = new THREE.Vector3()
      const q = new THREE.Vector3()
      for (const b of sk.bones) {
        p.setFromMatrixPosition(b.matrixWorld).applyMatrix4(inv)
        skeletonPoints.push(p.clone())
        if (b.parent && (b.parent as THREE.Bone).isBone) {
          q.setFromMatrixPosition(b.parent.matrixWorld).applyMatrix4(inv)
          skeletonPoints.push(p.clone().add(q).multiplyScalar(0.5))
        }
      }
    }
  } else if (meshes[0] && meshes[0] !== model) {
    add('mesh', rel(meshes[0]))
  }
  return { verts: new Float32Array(out), candidates, skeletonPoints }
}

function segmentPoints(bones: BoneSegment[]): THREE.Vector3[] {
  const pts: THREE.Vector3[] = []
  for (const b of bones) {
    if (!b.head || !b.tail || b.head.length < 3 || b.tail.length < 3) continue
    const h = new THREE.Vector3(b.head[0], b.head[1], b.head[2])
    const t = new THREE.Vector3(b.tail[0], b.tail[1], b.tail[2])
    pts.push(h, t, h.clone().add(t).multiplyScalar(0.5))
  }
  return pts.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.z))
}

// Two-way mean nearest distance between the (corrected) bone points and the surface
// sample: low when the bones sit inside the surface and spread across it.
function alignScore(points: THREE.Vector3[], verts: Float32Array, m: THREE.Matrix4): number {
  const n = points.length
  const vn = verts.length / 3
  const P = new Float32Array(n * 3)
  const t = new THREE.Vector3()
  for (let i = 0; i < n; i++) {
    t.copy(points[i]).applyMatrix4(m)
    P[i * 3] = t.x
    P[i * 3 + 1] = t.y
    P[i * 3 + 2] = t.z
  }
  const nearestV = new Float64Array(vn).fill(Infinity)
  let toSurface = 0
  for (let i = 0; i < n; i++) {
    const px = P[i * 3]
    const py = P[i * 3 + 1]
    const pz = P[i * 3 + 2]
    let best = Infinity
    for (let j = 0; j < vn; j++) {
      const dx = verts[j * 3] - px
      const dy = verts[j * 3 + 1] - py
      const dz = verts[j * 3 + 2] - pz
      const d = dx * dx + dy * dy + dz * dz
      if (d < best) best = d
      if (d < nearestV[j]) nearestV[j] = d
    }
    toSurface += Math.sqrt(best)
  }
  let toBones = 0
  for (let j = 0; j < vn; j++) toBones += Math.sqrt(nearestV[j])
  return toSurface / n + toBones / vn
}

// The correction (model-local) that best puts these points on the drawn surface.
// Identity wins unless another candidate is clearly better.
function pickAlignment(points: THREE.Vector3[], rest: RestData | null, tryZUp: boolean, label: string): THREE.Matrix4 {
  const identity = new THREE.Matrix4()
  if (!rest || !points.length || rest.verts.length < 9) return identity
  const cands = tryZUp ? [...rest.candidates, ...rest.candidates.map((c) => ({ name: c.name + ', from Z up', m: c.m.clone().multiply(Z_UP_TO_Y_UP) }))] : rest.candidates
  const scores = cands.map((c) => alignScore(points, rest.verts, c.m))
  let best = 0
  for (let i = 1; i < scores.length; i++) if (scores[i] < scores[best]) best = i
  const chosen = best !== 0 && scores[best] < scores[0] * 0.8 ? best : 0
  if (import.meta.env.DEV) console.debug(`[viewer] ${label} alignment: ${cands[chosen].name}`, Object.fromEntries(cands.map((c, i) => [c.name, Number(scores[i].toFixed(4))])))
  return chosen === 0 ? identity : cands[chosen].m.clone()
}

function buildBoneOverlay(bones: BoneSegment[]): THREE.Group {
  const g = new THREE.Group()
  g.name = 'bones-overlay'
  const positions: number[] = []
  const joints: number[] = []
  for (const b of bones) {
    if (!b.head || !b.tail || b.head.length < 3 || b.tail.length < 3) continue
    positions.push(b.head[0], b.head[1], b.head[2], b.tail[0], b.tail[1], b.tail[2])
    joints.push(b.head[0], b.head[1], b.head[2])
  }
  const lineGeo = new THREE.BufferGeometry()
  lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  const lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ color: 0x7fd4ff, depthTest: false, transparent: true, opacity: 0.95 }))
  lines.renderOrder = 999
  g.add(lines)
  const pointGeo = new THREE.BufferGeometry()
  pointGeo.setAttribute('position', new THREE.Float32BufferAttribute(joints, 3))
  const points = new THREE.Points(pointGeo, new THREE.PointsMaterial({ color: 0xffc44d, size: 6, sizeAttenuation: false, depthTest: false, transparent: true }))
  points.renderOrder = 1000
  g.add(points)
  return g
}

export default function Viewer({ url, bones, bonesSpace = 'gltf', height = 420, showBonesDefault = true, className = '', turntableDefault = false, defaultClip }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const modelRef = useRef<THREE.Object3D | null>(null)
  const originalsRef = useRef(new Map<THREE.Mesh, THREE.Material | THREE.Material[]>())
  const skeletonRef = useRef<THREE.SkeletonHelper | null>(null)
  const overlayRef = useRef<THREE.Group | null>(null)
  const clipsRef = useRef<THREE.AnimationClip[]>([])
  const actionRef = useRef<THREE.AnimationAction | null>(null)
  const turntableRef = useRef(turntableDefault)
  const restRef = useRef<RestData | null>(null)
  // The SkeletonHelper lives in this group (a child of the scene, not of the model frame,
  // because the helper already draws in world space) with its alignment correction.
  const skelGroupRef = useRef<THREE.Group | null>(null)
  const skelCorrRef = useRef<THREE.Matrix4 | null>(null)
  // Latest props for the async load callback.
  const bonesRef = useRef<{ bones: BoneSegment[] | null | undefined; space: BonesSpace; show: boolean }>({ bones, space: bonesSpace, show: showBonesDefault })

  const [shading, setShadingState] = useState<Shading>('textured')
  const [turntable, setTurntable] = useState(turntableDefault)
  const [grid, setGrid] = useState(true)
  const [showSkeleton, setShowSkeleton] = useState(true)
  const [showBones, setShowBones] = useState(showBonesDefault)
  const [hasSkeleton, setHasSkeleton] = useState(false)
  const [clips, setClips] = useState<string[]>([])
  const [clip, setClip] = useState<string>('')
  const [playing, setPlaying] = useState(true)
  const [status, setStatus] = useState('')

  // Scene setup once.
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x24272d)
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000)
    camera.position.set(3, 2, 3)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.target.set(0, 0.5, 0)

    scene.add(new THREE.HemisphereLight(0xffffff, 0x50545c, 1.2))
    const key = new THREE.DirectionalLight(0xffffff, 1.6)
    key.position.set(4, 8, 5)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0xffffff, 0.5)
    fill.position.set(-5, 3, -4)
    scene.add(fill)

    const gridHelper = new THREE.GridHelper(10, 20, 0x5a5f69, 0x3a3e46)
    scene.add(gridHelper)
    const modelRoot = new THREE.Group()
    const frame = new THREE.Group()
    modelRoot.add(frame)
    scene.add(modelRoot)

    const handles: SceneHandles = { renderer, scene, camera, controls, modelRoot, frame, grid: gridHelper, mixer: null, clock: new THREE.Clock() }
    const _inv = new THREE.Matrix4()
    handlesRef.current = handles

    const resize = () => {
      const w = host.clientWidth || 1
      const h = host.clientHeight || 1
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(host)

    let raf = 0
    const tick = () => {
      raf = requestAnimationFrame(tick)
      const dt = handles.clock.getDelta()
      if (turntableRef.current) modelRoot.rotation.y += dt * 0.6
      handles.mixer?.update(dt)
      // Keep the corrected skeleton helper following the model (turntable, framing).
      const sg = skelGroupRef.current
      const corr = skelCorrRef.current
      const model = modelRef.current
      if (sg && corr && model) {
        modelRoot.updateMatrixWorld(true)
        sg.matrix.copy(model.matrixWorld).multiply(corr).multiply(_inv.copy(model.matrixWorld).invert())
        sg.matrixWorldNeedsUpdate = true
      }
      controls.update()
      renderer.render(scene, camera)
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      controls.dispose()
      renderer.dispose()
      if (renderer.domElement.parentNode === host) host.removeChild(renderer.domElement)
      handlesRef.current = null
    }
  }, [])

  // Load the model whenever the url changes.
  useEffect(() => {
    const h = handlesRef.current
    if (!h) return
    if (!url) {
      h.frame.clear()
      removeSkeletonGroup(h)
      modelRef.current = null
      restRef.current = null
      setStatus('No file')
      return
    }
    let cancelled = false
    setStatus('Loading...')
    const loader = new GLTFLoader()
    loader.load(
      url,
      (gltf) => {
        if (cancelled || !handlesRef.current) return
        const hh = handlesRef.current
        hh.frame.clear()
        removeSkeletonGroup(hh)
        hh.modelRoot.rotation.set(0, 0, 0)
        originalsRef.current = new Map()
        skeletonRef.current = null
        overlayRef.current = null
        hh.mixer?.stopAllAction()
        hh.mixer = null
        actionRef.current = null

        const model = gltf.scene
        modelRef.current = model
        hh.frame.add(model)
        frameModel(hh, model)
        setShading(model, shading, originalsRef.current)

        let tris = 0
        let skinned = false
        model.traverse((o) => {
          const m = o as THREE.Mesh
          if (m.isMesh && m.geometry) {
            const idx = m.geometry.index
            tris += (idx ? idx.count : m.geometry.attributes.position.count) / 3
          }
          if ((o as THREE.SkinnedMesh).isSkinnedMesh) skinned = true
        })
        setHasSkeleton(skinned)
        // Rest pose as drawn, sampled before any animation moves it.
        const rest = captureRest(model)
        restRef.current = rest
        if (skinned) {
          const helper = new THREE.SkeletonHelper(model)
          ;(helper.material as THREE.LineBasicMaterial).depthTest = false
          helper.visible = showSkeleton
          const group = new THREE.Group()
          group.name = 'skeleton-helper'
          group.matrixAutoUpdate = false
          group.add(helper)
          hh.scene.add(group)
          skelGroupRef.current = group
          const corr = pickAlignment(rest.skeletonPoints ?? [], rest, false, 'skeleton')
          skelCorrRef.current = sameMatrix(corr, new THREE.Matrix4()) ? null : corr
          skeletonRef.current = helper
        }

        clipsRef.current = gltf.animations ?? []
        const names = clipsRef.current.map((c) => c.name || 'clip')
        setClips(names)
        if (clipsRef.current.length) {
          hh.mixer = new THREE.AnimationMixer(model)
          const wanted = defaultClip ? defaultClip.toLowerCase() : ''
          const first = (wanted && clipsRef.current.find((c) => (c.name || '').toLowerCase().includes(wanted))) || clipsRef.current[0]
          const action = hh.mixer.clipAction(first)
          action.play()
          actionRef.current = action
          setClip(first.name || 'clip')
          setPlaying(true)
        } else {
          setClip('')
        }
        attachOverlay(hh)
        setStatus(`${Math.round(tris).toLocaleString('en-US')} tris${skinned ? ', skinned' : ''}${names.length ? `, ${names.length} clips` : ''}`)
      },
      undefined,
      (err) => {
        if (!cancelled) setStatus('Load failed: ' + (err instanceof Error ? err.message : String(err)))
      },
    )
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  function removeSkeletonGroup(h: SceneHandles) {
    if (skelGroupRef.current) h.scene.remove(skelGroupRef.current)
    skelGroupRef.current = null
    skelCorrRef.current = null
  }

  // (Re)build the bones overlay for the current model, aligned to the drawn surface.
  function attachOverlay(h: SceneHandles) {
    if (overlayRef.current) {
      h.frame.remove(overlayRef.current)
      overlayRef.current = null
    }
    const { bones: segs, space, show } = bonesRef.current
    const model = modelRef.current
    if (!segs || !segs.length || !model) return
    const overlay = buildBoneOverlay(segs)
    const corr = pickAlignment(segmentPoints(segs), restRef.current, space !== 'gltf', 'bones overlay')
    // The overlay sits next to the model in the frame group; the model's own local transform plus the correction.
    overlay.matrixAutoUpdate = false
    overlay.matrix.copy(model.matrix).multiply(corr)
    overlay.matrixWorldNeedsUpdate = true
    overlay.visible = show
    h.frame.add(overlay)
    overlayRef.current = overlay
  }

  // Bones overlay may arrive after the model (rig_info is fetched separately).
  useEffect(() => {
    bonesRef.current = { bones, space: bonesSpace, show: showBones }
    const h = handlesRef.current
    if (h) attachOverlay(h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bones, bonesSpace])

  useEffect(() => {
    bonesRef.current = { ...bonesRef.current, show: showBones }
    if (overlayRef.current) overlayRef.current.visible = showBones
  }, [showBones])

  useEffect(() => {
    if (skeletonRef.current) skeletonRef.current.visible = showSkeleton
  }, [showSkeleton])

  useEffect(() => {
    const h = handlesRef.current
    if (h) h.grid.visible = grid
  }, [grid])

  useEffect(() => {
    turntableRef.current = turntable
  }, [turntable])

  const changeShading = (s: Shading) => {
    setShadingState(s)
    if (modelRef.current) setShading(modelRef.current, s, originalsRef.current)
  }

  const changeClip = (name: string) => {
    const h = handlesRef.current
    if (!h?.mixer) return
    const c = clipsRef.current.find((x) => (x.name || 'clip') === name)
    if (!c) return
    actionRef.current?.stop()
    const action = h.mixer.clipAction(c)
    action.reset().play()
    action.paused = !playing
    actionRef.current = action
    setClip(name)
  }

  const togglePlay = () => {
    const next = !playing
    setPlaying(next)
    if (actionRef.current) actionRef.current.paused = !next
  }

  const resetView = () => {
    const h = handlesRef.current
    if (h && modelRef.current) {
      h.modelRoot.rotation.set(0, 0, 0)
      frameModel(h, modelRef.current)
    }
  }

  return (
    <section className={'viewer ' + className} style={{ height }}>
      <div className="viewer-toolbar">
        <div className="seg">
          {(['textured', 'wireframe', 'normals'] as Shading[]).map((s) => (
            <button key={s} type="button" aria-pressed={shading === s} onClick={() => changeShading(s)}>
              {s}
            </button>
          ))}
        </div>
        <button type="button" aria-pressed={turntable} onClick={() => setTurntable((t) => !t)}>
          Turntable
        </button>
        <button type="button" aria-pressed={grid} onClick={() => setGrid((g) => !g)}>
          Grid
        </button>
        {hasSkeleton && (
          <button type="button" aria-pressed={showSkeleton} onClick={() => setShowSkeleton((s) => !s)}>
            Skeleton
          </button>
        )}
        {bones && bones.length > 0 && (
          <button type="button" aria-pressed={showBones} onClick={() => setShowBones((s) => !s)}>
            Bones ({bones.length})
          </button>
        )}
        {clips.length > 0 && (
          <>
            <select value={clip} onChange={(e) => changeClip(e.target.value)} aria-label="Animation clip">
              {clips.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <button type="button" onClick={togglePlay}>
              {playing ? 'Pause' : 'Play'}
            </button>
          </>
        )}
        <button type="button" onClick={resetView}>
          Reset view
        </button>
        <span className="viewer-status">{status}</span>
      </div>
      <div className="canvas-host" ref={hostRef} />
    </section>
  )
}
