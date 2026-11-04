// Three.js GLB viewer: GLTFLoader with textures, OrbitControls, shading modes
// (textured, wireframe, normals), turntable, grid, SkeletonHelper when the GLB
// has a skeleton, an AnimationMixer with a clip dropdown when it has animations,
// and an optional bones overlay drawn from rig_info bone segments.
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export type BoneSegment = { name?: string; head: number[]; tail: number[] }
export type Shading = 'textured' | 'wireframe' | 'normals'

type Props = {
  url?: string
  bones?: BoneSegment[] | null
  height?: number | string
  showBonesDefault?: boolean
  className?: string
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

export default function Viewer({ url, bones, height = 420, showBonesDefault = true, className = '' }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const modelRef = useRef<THREE.Object3D | null>(null)
  const originalsRef = useRef(new Map<THREE.Mesh, THREE.Material | THREE.Material[]>())
  const skeletonRef = useRef<THREE.SkeletonHelper | null>(null)
  const overlayRef = useRef<THREE.Group | null>(null)
  const clipsRef = useRef<THREE.AnimationClip[]>([])
  const actionRef = useRef<THREE.AnimationAction | null>(null)
  const turntableRef = useRef(false)

  const [shading, setShadingState] = useState<Shading>('textured')
  const [turntable, setTurntable] = useState(false)
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
      modelRef.current = null
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
        if (skinned) {
          const helper = new THREE.SkeletonHelper(model)
          ;(helper.material as THREE.LineBasicMaterial).depthTest = false
          helper.visible = showSkeleton
          hh.frame.add(helper)
          skeletonRef.current = helper
        }

        clipsRef.current = gltf.animations ?? []
        const names = clipsRef.current.map((c) => c.name || 'clip')
        setClips(names)
        if (clipsRef.current.length) {
          hh.mixer = new THREE.AnimationMixer(model)
          const first = clipsRef.current[0]
          const action = hh.mixer.clipAction(first)
          action.play()
          actionRef.current = action
          setClip(first.name || 'clip')
          setPlaying(true)
        } else {
          setClip('')
        }
        if (bones && bones.length) {
          const overlay = buildBoneOverlay(bones)
          overlay.visible = showBones
          hh.frame.add(overlay)
          overlayRef.current = overlay
        }
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

  // Bones overlay may arrive after the model (rig_info is fetched separately).
  useEffect(() => {
    const h = handlesRef.current
    if (!h) return
    if (overlayRef.current) {
      h.frame.remove(overlayRef.current)
      overlayRef.current = null
    }
    if (bones && bones.length && modelRef.current) {
      const overlay = buildBoneOverlay(bones)
      overlay.visible = showBones
      h.frame.add(overlay)
      overlayRef.current = overlay
    }
  }, [bones, showBones])

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
