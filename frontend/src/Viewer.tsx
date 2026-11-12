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
//
// Hit boxes (plan 4.2): hitboxes.json gives each shape in the same glTF model space as the bone segments. Each
// shape is parented to its bone, placed from the bone's rest pose captured at load (before any clip moves it) and
// the bones overlay's alignment correction, so it follows the playing clip. The body shape for moving stays on
// the model. Clips (3.7): the sidecar (anim_clips.json) gives the order and which clips loop; one-shot clips play
// once and stop on their last frame.
import { useContext, useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { Info } from './components/Info'
import { LightPicker } from './components/LightPicker'
import { clipLabel, clipLoopsByName } from './help'
import { keepOverlayColours, LIGHT_EVENT, lightEventApplies, PreviewLight, rememberedLight, rememberLight, ViewerLookContext, type LightId } from './previewLight'

export type BoneSegment = { name?: string; head: number[]; tail: number[] }
// An item shown on an attachment point (items): its GLB (pivot at the grip) under the Socket_* node, at an offset in
// the socket's own frame (metres; rotation a quaternion x, y, z, w), so it moves with every clip.
export type ViewerAttachment = { id: string; url: string; socket: string; position: number[]; rotation: number[]; scale: number }
export type HitShape = {
  name?: string
  region?: string
  label?: string
  bone?: string
  kind: 'capsule' | 'sphere' | 'box' | string
  center: number[]
  axis?: number[]
  forward?: number[]
  radius?: number
  height?: number
  size?: number[]
}
export type HitboxDoc = {
  space?: string
  mode?: 'detailed' | 'simple' | 'none' | string
  fallback?: boolean
  count?: number
  hitboxes?: HitShape[]
  movement?: ({ default?: string } & Record<string, HitShape | string | undefined>) | null
}
// One row of the clip sidecar (anim_clips.json).
export type ClipInfo = { name: string; loop?: boolean; seconds?: number; hold_last?: boolean; label?: string }
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
  // Hit boxes and the body shape for moving (rig view); the toggles show only when there are some.
  hitboxes?: HitboxDoc | null
  // The clip sidecar: order, plain names and which clips loop.
  clipInfo?: ClipInfo[] | null
  // Items on attachment points, and a clip to play chosen outside the viewer (n changes on every press).
  attachments?: ViewerAttachment[] | null
  clipRequest?: { name: string; n: number } | null
  // The asset's look (style_preset), for the default preview light; the asset page provides it otherwise.
  look?: string | null
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

// Not tone mapped: the normals view shows the same colours under every preview light.
const normalMaterial = new THREE.MeshNormalMaterial({ toneMapped: false })

// Reduce Motion (plan 2.7): the system setting "reduce motion" (prefers-reduced-motion: reduce), followed live.
const REDUCE_QUERY = '(prefers-reduced-motion: reduce)'
function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(REDUCE_QUERY).matches
}
function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(prefersReducedMotion)
  useEffect(() => {
    const mq = window.matchMedia?.(REDUCE_QUERY)
    if (!mq) return
    const on = () => setReduce(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return reduce
}

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
  // Measure with the frame back at the origin: the box is in world space, so a leftover offset from the last model
  // (or the last Reset view) would be measured in and cancel the lift, sinking the model to its waist.
  h.frame.position.set(0, 0, 0)
  h.frame.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(model)
  if (box.isEmpty()) return
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  // Sit the model on the grid, centred on the origin, by offsetting the frame group.
  h.frame.position.set(-center.x, -box.min.y, -center.z)
  const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1
  // Far enough for the whole model with a little room, seen from the front quarter and slightly above. (The
  // direction is a unit vector: an unnormalised one used to stand the camera two thirds further back than meant.)
  const dist = (radius / Math.sin((h.camera.fov * Math.PI) / 360)) * 1.25
  const from = new THREE.Vector3(0.8, 0.4, 0.8).normalize().multiplyScalar(dist)
  h.camera.position.set(from.x, size.y / 2 + from.y, from.z)
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
  boneRest: Map<THREE.Object3D, THREE.Matrix4> // every bone's rest matrix, model-local, before any clip moves it
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
  const boneRest = new Map<THREE.Object3D, THREE.Matrix4>()
  model.traverse((o) => {
    if ((o as THREE.Bone).isBone) boneRest.set(o, rel(o))
  })
  return { verts: new Float32Array(out), candidates, skeletonPoints, boneRest }
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
  keepOverlayColours(g)
  return g
}

// ---------------------------------------------------------------------------
// Hit boxes and the body shape for moving
// ---------------------------------------------------------------------------

// A colour token from the stylesheet (--warn, --accent), with a fallback for when it cannot be read.
function tokenColor(name: string, fallback: string): THREE.Color {
  let v = ''
  try {
    v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  } catch {
    v = ''
  }
  const c = new THREE.Color()
  try {
    c.setStyle(v || fallback)
  } catch {
    c.setStyle(fallback)
  }
  return c
}

const vec3 = (a: number[] | undefined, d: [number, number, number]): THREE.Vector3 => (Array.isArray(a) && a.length >= 3 && a.every((x) => Number.isFinite(x)) ? new THREE.Vector3(a[0], a[1], a[2]) : new THREE.Vector3(...d))

// The shape's frame in glTF model space: origin at the centre, local +Y along `axis`, local +Z toward `forward`.
function shapeMatrix(s: HitShape): THREE.Matrix4 {
  const y = vec3(s.axis, [0, 1, 0]).normalize()
  let z = vec3(s.forward, [0, 0, 1])
  z.sub(y.clone().multiplyScalar(z.dot(y))) // make forward square to the axis
  if (z.lengthSq() < 1e-8) z = Math.abs(y.z) < 0.9 ? new THREE.Vector3(0, 0, 1).sub(y.clone().multiplyScalar(y.z)) : new THREE.Vector3(1, 0, 0).sub(y.clone().multiplyScalar(y.x))
  z.normalize()
  const x = new THREE.Vector3().crossVectors(y, z).normalize()
  const m = new THREE.Matrix4().makeBasis(x, y, z)
  m.setPosition(vec3(s.center, [0, 0, 0]))
  return m
}

// A circle of `r` around local Y at height y, as line-segment pairs.
function ringPoints(out: number[], r: number, y: number, plane: 'xz' | 'xy' | 'zy', n = 32) {
  for (let i = 0; i < n; i++) {
    const a0 = (i / n) * Math.PI * 2
    const a1 = ((i + 1) / n) * Math.PI * 2
    const p = (a: number): [number, number, number] => (plane === 'xz' ? [Math.cos(a) * r, y, Math.sin(a) * r] : plane === 'xy' ? [Math.cos(a) * r, Math.sin(a) * r + y, 0] : [0, Math.sin(a) * r + y, Math.cos(a) * r])
    out.push(...p(a0), ...p(a1))
  }
}

// A thin outline that reads at any angle: the capsule's two side profiles and its end rings, a sphere's three
// great circles, a box's edges.
function outlineGeometry(kind: string, r: number, half: number, size: THREE.Vector3): THREE.BufferGeometry {
  if (kind === 'box') return new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x, size.y, size.z))
  const pts: number[] = []
  if (kind === 'sphere') {
    ringPoints(pts, r, 0, 'xz')
    ringPoints(pts, r, 0, 'xy')
    ringPoints(pts, r, 0, 'zy')
  } else {
    ringPoints(pts, r, half, 'xz')
    ringPoints(pts, r, -half, 'xz')
    for (const plane of ['xy', 'zy'] as const) {
      // Two half circles for the caps and two straight sides.
      const n = 16
      for (const [cy, from] of [
        [half, 0],
        [-half, Math.PI],
      ] as const) {
        for (let i = 0; i < n; i++) {
          const a0 = from + (i / n) * Math.PI
          const a1 = from + ((i + 1) / n) * Math.PI
          const p = (a: number): [number, number, number] => (plane === 'xy' ? [Math.cos(a) * r, Math.sin(a) * r + cy, 0] : [0, Math.sin(a) * r + cy, Math.cos(a) * r])
          pts.push(...p(a0), ...p(a1))
        }
      }
      if (plane === 'xy') pts.push(r, -half, 0, r, half, 0, -r, -half, 0, -r, half, 0)
      else pts.push(0, -half, r, 0, half, r, 0, -half, -r, 0, half, -r)
    }
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  return g
}

const FILL_OPACITY = 0.3
const FILL_HOVER_OPACITY = 0.55

// One shape (fill plus outline) in its own local frame: Y is the axis, sizes in metres.
function buildShape(s: HitShape, color: THREE.Color, label: string): THREE.Group {
  const kind = s.kind === 'box' || s.kind === 'sphere' ? s.kind : 'capsule'
  const sz = vec3(s.size, [0, 0, 0])
  const r = Math.max(1e-4, Number.isFinite(s.radius) ? Number(s.radius) : Math.max(sz.x, sz.z) / 2 || 0.05)
  const height = Number.isFinite(s.height) ? Number(s.height) : sz.y || r * 2
  const half = Math.max(0, height / 2 - r)
  if (kind === 'box' && sz.lengthSq() === 0) sz.set(r * 2, height, r * 2)
  const geo = kind === 'box' ? new THREE.BoxGeometry(sz.x, sz.y, sz.z) : kind === 'sphere' ? new THREE.SphereGeometry(r, 24, 16) : new THREE.CapsuleGeometry(r, half * 2, 8, 20)
  const fill = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: FILL_OPACITY, depthTest: false, depthWrite: false, side: THREE.DoubleSide }))
  fill.renderOrder = 997
  fill.userData.hitLabel = label
  const line = new THREE.LineSegments(outlineGeometry(kind, r, half, sz), new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9, depthTest: false }))
  line.renderOrder = 998
  line.raycast = () => {} // hover picks the fill only
  const g = new THREE.Group()
  g.add(fill, line)
  keepOverlayColours(g)
  return g
}

// Attachment points: the export adds an empty node named Socket_* under a bone for each spot an item can be
// attached to (hand, back, head...). The marker is a child of that node, so it follows every animation.
const SOCKET_LABELS: Record<string, string> = {
  Socket_Hand_R: 'Right hand',
  Socket_Hand_L: 'Left hand',
  Socket_Forearm_R: 'Right forearm',
  Socket_Forearm_L: 'Left forearm',
  Socket_Back: 'Back',
  Socket_Hip_L: 'Left hip',
  Socket_Hip_R: 'Right hip',
  Socket_Head: 'Head',
  Socket_Chest: 'Chest',
  Socket_Mouth: 'Mouth',
}

export function socketLabel(name: string): string {
  return SOCKET_LABELS[name] ?? name.replace(/^Socket_/, '').replace(/_/g, ' ')
}

function labelSprite(text: string, height: number): THREE.Sprite {
  const pad = 12
  const font = '600 28px system-ui, sans-serif'
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')!
  ctx.font = font
  canvas.width = Math.ceil(ctx.measureText(text).width) + pad * 2
  canvas.height = 44
  ctx.font = font
  ctx.fillStyle = 'rgba(15, 18, 22, 0.82)'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.fillStyle = '#ffffff'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, pad, canvas.height / 2 + 1)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }))
  sprite.renderOrder = 1000
  sprite.scale.set((height * canvas.width) / canvas.height, height, 1)
  sprite.center.set(0, 0)
  sprite.raycast = () => {}
  return sprite
}

// A small diamond, three axis lines (red right, green up, blue forward) and the name. No Mesh inside, so the
// wireframe and normals views leave it alone.
function buildSocketMarker(node: THREE.Object3D, model: THREE.Object3D, size: number, color: THREE.Color): THREE.Group {
  const g = new THREE.Group()
  g.name = 'socket-marker'
  const axes = new THREE.AxesHelper(size)
  ;(axes.material as THREE.Material).depthTest = false
  axes.renderOrder = 999
  axes.raycast = () => {}
  const diamond = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(size * 0.3)), new THREE.LineBasicMaterial({ color, depthTest: false }))
  diamond.renderOrder = 999
  diamond.raycast = () => {}
  const label = labelSprite(socketLabel(node.name), size * 0.55)
  label.position.set(size * 0.35, size * 0.35, 0)
  g.add(axes, diamond, label)
  // Same size on screen whatever scale the bone carries.
  const ns = node.getWorldScale(new THREE.Vector3())
  const ms = model.getWorldScale(new THREE.Vector3())
  g.scale.setScalar(ms.x / (ns.x || 1))
  keepOverlayColours(g)
  return g
}

function disposeGroup(g: THREE.Object3D) {
  g.traverse((o) => {
    const m = o as THREE.Mesh
    m.geometry?.dispose?.()
    const mat = m.material as THREE.Material | THREE.Material[] | undefined
    if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
    else mat?.dispose?.()
  })
}

// The bone a hit box hangs on. three.js strips characters it cannot bind from node names ("mixamorig:Hips"
// becomes "mixamorigHips"), so the sanitised name is tried first.
function findBone(model: THREE.Object3D, name: string | undefined): THREE.Object3D | null {
  if (!name) return null
  return model.getObjectByName(THREE.PropertyBinding.sanitizeNodeName(name)) ?? model.getObjectByName(name) ?? null
}

// The movement shape the file chose (movement.default), if it has one.
function movementShapeOf(doc: HitboxDoc | null | undefined): HitShape | null {
  const mv = doc?.movement
  if (!mv || typeof mv !== 'object') return null
  const key = typeof mv.default === 'string' ? mv.default : 'upright'
  const s = mv[key]
  return s && typeof s === 'object' && Array.isArray((s as HitShape).center) ? (s as HitShape) : null
}

// The GLB's clips in the sidecar's order, then any the sidecar does not list.
function orderClips(names: string[], info: ClipInfo[] | null | undefined): string[] {
  if (!info?.length) return names
  const listed = info.map((c) => c.name).filter((n) => names.includes(n))
  return [...listed, ...names.filter((n) => !listed.includes(n))]
}

export function hitShapesOf(doc: HitboxDoc | null | undefined): HitShape[] {
  if (!doc || doc.mode === 'none' || !Array.isArray(doc.hitboxes)) return []
  return doc.hitboxes.filter((h) => h && Array.isArray(h.center) && h.center.length >= 3)
}

export default function Viewer({ url, bones, bonesSpace = 'gltf', height = 420, showBonesDefault = true, className = '', turntableDefault = false, defaultClip, hitboxes, clipInfo, look: lookProp, attachments, clipRequest }: Props) {
  // Preview light (previewLight.ts): Outdoors for Photoreal, Studio for other looks, then the person's last choice.
  const lookCtx = useContext(ViewerLookContext)
  const look = lookProp ?? lookCtx
  const [light, setLight] = useState<LightId>(() => rememberedLight(look))
  const [lightNote, setLightNote] = useState<string | null>(null)
  const lightRigRef = useRef<PreviewLight | null>(null)
  const hostRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const modelRef = useRef<THREE.Object3D | null>(null)
  const originalsRef = useRef(new Map<THREE.Mesh, THREE.Material | THREE.Material[]>())
  const skeletonRef = useRef<THREE.SkeletonHelper | null>(null)
  const overlayRef = useRef<THREE.Group | null>(null)
  const clipsRef = useRef<THREE.AnimationClip[]>([])
  const actionRef = useRef<THREE.AnimationAction | null>(null)
  // With Reduce Motion nothing moves until the person asks: no turntable, clips paused on their first frame.
  const reduceMotion = useReducedMotion()
  const reduceRef = useRef(reduceMotion)
  reduceRef.current = reduceMotion
  const turntableRef = useRef(turntableDefault && !reduceMotion)
  const restRef = useRef<RestData | null>(null)
  // The SkeletonHelper lives in this group (a child of the scene, not of the model frame,
  // because the helper already draws in world space) with its alignment correction.
  const skelGroupRef = useRef<THREE.Group | null>(null)
  const skelCorrRef = useRef<THREE.Matrix4 | null>(null)
  // Latest props for the async load callback.
  const bonesRef = useRef<{ bones: BoneSegment[] | null | undefined; space: BonesSpace; show: boolean }>({ bones, space: bonesSpace, show: showBonesDefault })
  // The bones overlay's alignment correction, reused for the hit boxes (same space).
  const bonesCorrRef = useRef<THREE.Matrix4 | null>(null)
  // Hit boxes hang on bones; the body shape for moving sits on the model.
  const hitDocRef = useRef<HitboxDoc | null | undefined>(hitboxes)
  const hitGroupsRef = useRef<THREE.Group[]>([])
  const moveGroupRef = useRef<THREE.Group | null>(null)
  const clipInfoRef = useRef<ClipInfo[] | null | undefined>(clipInfo)
  clipInfoRef.current = clipInfo

  const [shading, setShadingState] = useState<Shading>('textured')
  const [turntable, setTurntable] = useState(turntableDefault && !reduceMotion)
  const [grid, setGrid] = useState(true)
  // The skeleton lines start on where the skeleton is the subject (the rig step), off where the finished model is.
  const [showSkeleton, setShowSkeleton] = useState(showBonesDefault)
  const [showBones, setShowBones] = useState(showBonesDefault)
  const [hasSkeleton, setHasSkeleton] = useState(false)
  const [clips, setClips] = useState<string[]>([])
  const [clip, setClip] = useState<string>('')
  const [playing, setPlaying] = useState(true)
  const [status, setStatus] = useState('')
  // Off by default: bones are on, and both together are cluttered (4.2).
  const [showHits, setShowHits] = useState(false)
  const [showMove, setShowMove] = useState(false)
  // What a plain drag does: turn the model (the default) or move the view sideways and up and down.
  const [dragMode, setDragMode] = useState<'turn' | 'move'>('turn')
  const controlsRef = useRef<OrbitControls | null>(null)
  const followResetRef = useRef(false)
  // A taller preview on request, for looking at small parts.
  const [large, setLarge] = useState(false)
  useEffect(() => {
    const c = controlsRef.current
    if (!c) return
    const move = dragMode === 'move'
    c.mouseButtons.LEFT = move ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE
    c.mouseButtons.RIGHT = move ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN
    c.touches.ONE = move ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE
    if (c.domElement) c.domElement.style.cursor = move ? 'move' : 'grab'
  }, [dragMode])
  const showHitsRef = useRef(showHits)
  // Attachment points (Socket_* nodes in the file); off until asked for.
  const [showSockets, setShowSockets] = useState(false)
  const [socketCount, setSocketCount] = useState(0)
  const showSocketsRef = useRef(showSockets)
  const socketGroupsRef = useRef<THREE.Group[]>([])
  const showMoveRef = useRef(showMove)
  const [hoverLabel, setHoverLabel] = useState<string | null>(null)
  // The mouse hint shows once; the ? button brings it back (UI-24).
  const [hintOn, setHintOn] = useState(true)

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
    // Scrolling zooms toward the pointer, so any part can be reached; arrow keys move the view once the picture has focus.
    controls.zoomToCursor = true
    controls.keyPanSpeed = 24
    renderer.domElement.tabIndex = 0
    renderer.domElement.setAttribute('aria-label', '3D preview. Drag to turn, right-drag or arrow keys to move, scroll to zoom, double-click a spot to look at it closely.')
    controls.listenToKeyEvents(renderer.domElement)
    controlsRef.current = controls
    renderer.domElement.style.cursor = 'grab'

    const hemi = new THREE.HemisphereLight(0xffffff, 0x50545c, 1.2)
    scene.add(hemi)
    const key = new THREE.DirectionalLight(0xffffff, 1.6)
    key.position.set(4, 8, 5)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0xffffff, 0.5)
    fill.position.set(-5, 3, -4)
    scene.add(fill)
    // The three lights above are the "Flat" choice; the others light the model from an environment.
    const lightRig = new PreviewLight(renderer, scene, [hemi, key, fill])
    lightRigRef.current = lightRig

    const gridHelper = new THREE.GridHelper(10, 20, 0x5a5f69, 0x3a3e46)
    keepOverlayColours(gridHelper)
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

    let followModel: THREE.Object3D | null = null
    let followBone: THREE.Object3D | null = null
    let followAnchor: THREE.Vector2 | null = null
    let followLimit = 1
    let followJump = false
    const _fv = new THREE.Vector3()
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
      // A move that travels (a captured walk, a roll) would carry the character out of view: once the hips are more
      // than a third of the model's height from where the view last settled, the view moves along with them. Moves
      // done on the spot stay inside that margin, so the view holds still for them.
      const model0 = modelRef.current
      if (model0 !== followModel) {
        followModel = model0
        followBone = null
        model0?.traverse((o) => {
          if (!followBone && (o as THREE.Bone).isBone && /hips|pelvis/i.test(o.name)) followBone = o
        })
        model0?.traverse((o) => {
          if (!followBone && (o as THREE.Bone).isBone) followBone = o
        })
        followAnchor = null
        followLimit = model0 ? Math.max(0.05, new THREE.Box3().setFromObject(model0).getSize(_fv).y * 0.33) : 1
      }
      if (followBone && handles.mixer) {
        ;(followBone as THREE.Object3D).getWorldPosition(_fv)
        // Reset view puts the view back on the middle of the floor; the lines below then bring it to the character.
        if (followResetRef.current) {
          followResetRef.current = false
          followAnchor = new THREE.Vector2(0, 0)
          followJump = true
        }
        if (!followAnchor) followAnchor = new THREE.Vector2(_fv.x, _fv.z)
        const dx = _fv.x - followAnchor.x
        const dz = _fv.z - followAnchor.y
        const d = Math.hypot(dx, dz)
        if (d > followLimit * 4 || (d > followLimit && followAnchor.x === 0 && followAnchor.y === 0 && followJump)) {
          followJump = false
          // The clip started again from its first frame: go back with it in one step.
          followAnchor.set(_fv.x, _fv.z)
          controls.target.x += dx
          controls.target.z += dz
          camera.position.x += dx
          camera.position.z += dz
        } else if (d > followLimit) {
          const k = (d - followLimit) / d
          followAnchor.x += dx * k
          followAnchor.y += dz * k
          controls.target.x += dx * k
          controls.target.z += dz * k
          camera.position.x += dx * k
          camera.position.z += dz * k
        }
      }
      if (focus) {
        const k = Math.min(1, (performance.now() - focus.t0) / focus.ms)
        const e = k * k * (3 - 2 * k)
        controls.target.lerpVectors(focus.fromT, focus.toT, e)
        camera.position.lerpVectors(focus.fromP, focus.toP, e)
        if (k >= 1) focus = null
      }
      controls.update()
      renderer.render(scene, camera)
    }
    raf = requestAnimationFrame(tick)

    // Double-click a spot on the model: the view centres on it and moves closer.
    type Focus = { fromT: THREE.Vector3; toT: THREE.Vector3; fromP: THREE.Vector3; toP: THREE.Vector3; t0: number; ms: number }
    let focus: Focus | null = null
    const shown = (o: THREE.Object3D | null): boolean => {
      for (let n = o; n; n = n.parent) if (!n.visible) return false
      return true
    }
    const onDouble = (e: MouseEvent) => {
      const model = modelRef.current
      if (!model) return
      const rect = renderer.domElement.getBoundingClientRect()
      const at = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1)
      const caster = new THREE.Raycaster()
      caster.setFromCamera(at, camera)
      const hit = caster.intersectObject(model, true).find((i) => (i.object as THREE.Mesh).isMesh && i.object.userData.hitLabel === undefined && shown(i.object))
      if (!hit) return
      const size = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3())
      const closest = Math.max(size.x, size.y, size.z) * 0.22
      const now = camera.position.distanceTo(controls.target)
      const dist = now <= closest ? now : Math.max(now * 0.4, closest)
      const dir = camera.position.clone().sub(controls.target).normalize()
      focus = { fromT: controls.target.clone(), toT: hit.point.clone(), fromP: camera.position.clone(), toP: hit.point.clone().addScaledVector(dir, dist), t0: performance.now(), ms: reduceRef.current ? 1 : 260 }
    }
    const stopFocus = () => {
      focus = null
    }
    renderer.domElement.addEventListener('dblclick', onDouble)
    renderer.domElement.addEventListener('pointerdown', stopFocus)
    renderer.domElement.addEventListener('wheel', stopFocus, { passive: true })
    // The wheel zooms only with Ctrl held or once the picture has been clicked, so the page can scroll past it (UI-07).
    const gateZoom = (e: WheelEvent) => {
      const ok = e.ctrlKey || document.activeElement === renderer.domElement
      controls.enableZoom = ok
      if (!ok) setHoverLabel('Hold Ctrl to zoom, or click the picture first')
    }
    renderer.domElement.addEventListener('wheel', gateZoom, { capture: true, passive: true })

    // Hovering a hit box names it in the status line and brightens it.
    const ray = new THREE.Raycaster()
    const ndc = new THREE.Vector2()
    let hovered: THREE.Mesh | null = null
    const setHovered = (m: THREE.Mesh | null) => {
      if (m === hovered) return
      if (hovered) (hovered.material as THREE.MeshBasicMaterial).opacity = FILL_OPACITY
      hovered = m
      if (m) (m.material as THREE.MeshBasicMaterial).opacity = FILL_HOVER_OPACITY
      setHoverLabel(m ? String(m.userData.hitLabel ?? '') : null)
    }
    const onMove = (e: PointerEvent) => {
      const groups = [...hitGroupsRef.current, ...(moveGroupRef.current ? [moveGroupRef.current] : [])].filter((g) => g.visible && g.parent)
      if (!groups.length) return setHovered(null)
      const rect = renderer.domElement.getBoundingClientRect()
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1)
      ray.setFromCamera(ndc, camera)
      const fills = groups.map((g) => g.children[0]).filter((o): o is THREE.Mesh => !!o && (o as THREE.Mesh).isMesh)
      const hit = ray.intersectObjects(fills, false)[0]
      setHovered((hit?.object as THREE.Mesh | undefined) ?? null)
    }
    const onLeave = () => setHovered(null)
    renderer.domElement.addEventListener('pointermove', onMove)
    renderer.domElement.addEventListener('pointerleave', onLeave)

    return () => {
      cancelAnimationFrame(raf)
      renderer.domElement.removeEventListener('pointermove', onMove)
      renderer.domElement.removeEventListener('pointerleave', onLeave)
      renderer.domElement.removeEventListener('dblclick', onDouble)
      renderer.domElement.removeEventListener('pointerdown', stopFocus)
      renderer.domElement.removeEventListener('wheel', stopFocus)
      renderer.domElement.removeEventListener('wheel', gateZoom, { capture: true })
      controlsRef.current = null
      ro.disconnect()
      controls.dispose()
      lightRig.dispose()
      lightRigRef.current = null
      renderer.dispose()
      if (renderer.domElement.parentNode === host) host.removeChild(renderer.domElement)
      handlesRef.current = null
    }
  }, [])

  // Show the chosen preview light (built the first time it is shown, kept until the viewer closes).
  useEffect(() => {
    let alive = true
    lightRigRef.current?.apply(light).then((note) => alive && setLightNote(note))
    return () => {
      alive = false
    }
  }, [light])

  // Another viewer on the page changed the light for this look: follow it. A new asset's look picks its own.
  useEffect(() => {
    setLight(rememberedLight(look))
    const on = (e: Event) => {
      const id = lightEventApplies(e, look)
      if (id) setLight(id)
    }
    window.addEventListener(LIGHT_EVENT, on)
    return () => window.removeEventListener(LIGHT_EVENT, on)
  }, [look])

  const chooseLight = (id: LightId) => {
    setLight(id)
    rememberLight(look, id)
  }

  // Load the model whenever the url changes.
  useEffect(() => {
    const h = handlesRef.current
    if (!h) return
    if (!url) {
      clearHitboxes()
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
        clearHitboxes()
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
        // Attachment points: mark every Socket_* node the export put on a bone.
        socketGroupsRef.current = []
        const socketNodes: THREE.Object3D[] = []
        model.traverse((o) => {
          if (/^Socket_/.test(o.name)) socketNodes.push(o)
        })
        if (socketNodes.length) {
          model.updateWorldMatrix(true, true)
          const tall = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3())
          const markerSize = Math.max(0.02, Math.max(tall.x, tall.y, tall.z) * 0.045)
          const ok = tokenColor('--ok', '#5fbf7a')
          for (const node of socketNodes) {
            const g = buildSocketMarker(node, model, markerSize, ok)
            g.visible = showSocketsRef.current
            node.add(g)
            socketGroupsRef.current.push(g)
          }
        }
        setSocketCount(socketNodes.length)
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
          keepOverlayColours(group)
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
          // A one-shot clip that reached its last frame stops there; the Play button starts it again.
          hh.mixer.addEventListener('finished', () => setPlaying(false))
          // Nothing asked for: stand in the idle, not whichever clip sorts first (that was a crouch).
          const wanted = defaultClip ? defaultClip.toLowerCase() : 'idle'
          const ordered = orderClips(names, clipInfoRef.current)
          // The clip asked for by its exact name first ("walk" must not pick "crouch_walk"), then by a name containing it.
          const first =
            (wanted && (clipsRef.current.find((c) => (c.name || '').toLowerCase() === wanted) || (defaultClip ? clipsRef.current.find((c) => (c.name || '').toLowerCase().includes(wanted)) : undefined))) ||
            clipsRef.current.find((c) => (c.name || 'clip') === ordered[0]) ||
            clipsRef.current[0]
          const action = hh.mixer.clipAction(first)
          applyLoop(action, first.name || 'clip')
          action.play()
          // Reduce Motion: load paused on frame 0; the Play button starts it.
          const still = reduceRef.current
          action.paused = still
          if (still) hh.mixer.update(0)
          actionRef.current = action
          setClip(first.name || 'clip')
          setPlaying(!still)
        } else {
          setClip('')
        }
        attachOverlay(hh)
        placeAttachments()
        applyClipRequest()
        setStatus(`Preview: ${Math.round(tris).toLocaleString('en-US')} triangles${skinned ? ', skinned' : ''}${names.length ? `, ${names.length} moves` : ''}`)
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
    bonesCorrRef.current = null
    const { bones: segs, space, show } = bonesRef.current
    const model = modelRef.current
    if (!segs || !segs.length || !model) {
      attachHitboxes()
      return
    }
    const overlay = buildBoneOverlay(segs)
    const corr = pickAlignment(segmentPoints(segs), restRef.current, space !== 'gltf', 'bones overlay')
    // Hit boxes are always in glTF space, so they share the correction only when the segments are too.
    if (space === 'gltf') bonesCorrRef.current = corr
    attachHitboxes()
    // The overlay sits next to the model in the frame group; the model's own local transform plus the correction.
    overlay.matrixAutoUpdate = false
    overlay.matrix.copy(model.matrix).multiply(corr)
    overlay.matrixWorldNeedsUpdate = true
    overlay.visible = show
    h.frame.add(overlay)
    overlayRef.current = overlay
  }

  function clearHitboxes() {
    for (const g of hitGroupsRef.current) {
      g.parent?.remove(g)
      disposeGroup(g)
    }
    hitGroupsRef.current = []
    if (moveGroupRef.current) {
      moveGroupRef.current.parent?.remove(moveGroupRef.current)
      disposeGroup(moveGroupRef.current)
      moveGroupRef.current = null
    }
    setHoverLabel(null)
  }

  // (Re)build the hit boxes on their bones, from the rest pose captured at load, and the body shape for moving.
  function attachHitboxes() {
    clearHitboxes()
    const model = modelRef.current
    const rest = restRef.current
    const doc = hitDocRef.current
    if (!model || !rest || !doc) return
    const shapes = hitShapesOf(doc)
    const move = movementShapeOf(doc)
    if (!shapes.length && !move) return
    const corr =
      bonesCorrRef.current ??
      pickAlignment(
        shapes.map((s) => vec3(s.center, [0, 0, 0])),
        rest,
        false,
        'hit boxes',
      )
    const warn = tokenColor('--warn', '#d8a84a')
    let unbound = 0
    for (const s of shapes) {
      const inModel = corr.clone().multiply(shapeMatrix(s))
      const bone = findBone(model, s.bone)
      if (!bone) unbound++
      const parentRest = bone ? (rest.boneRest.get(bone) ?? model.matrixWorld.clone().invert().multiply(bone.matrixWorld)) : new THREE.Matrix4()
      const local = parentRest.clone().invert().multiply(inModel)
      const g = buildShape(s, warn, s.label || s.region || s.name || 'Hit box')
      g.name = s.name ?? 'hitbox'
      local.decompose(g.position, g.quaternion, g.scale)
      g.visible = showHitsRef.current
      ;(bone ?? model).add(g)
      hitGroupsRef.current.push(g)
    }
    if (unbound && import.meta.env.DEV) console.debug(`[viewer] ${unbound} hit boxes name a bone this file does not have; they stay still`)
    if (move) {
      const g = buildShape(move, tokenColor('--accent', '#6ea4e8'), 'Body shape for moving')
      g.name = 'movement-shape'
      corr.clone().multiply(shapeMatrix(move)).decompose(g.position, g.quaternion, g.scale)
      g.visible = showMoveRef.current
      model.add(g)
      moveGroupRef.current = g
    }
  }

  useEffect(() => {
    showSocketsRef.current = showSockets
    for (const g of socketGroupsRef.current) g.visible = showSockets
  }, [showSockets])

  // Hit boxes arrive separately (hitboxes.json is fetched by the rig view).
  useEffect(() => {
    hitDocRef.current = hitboxes
    attachHitboxes()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hitboxes])

  useEffect(() => {
    showHitsRef.current = showHits
    for (const g of hitGroupsRef.current) g.visible = showHits
  }, [showHits])

  useEffect(() => {
    showMoveRef.current = showMove
    if (moveGroupRef.current) moveGroupRef.current.visible = showMove
  }, [showMove])

  // Whether a clip loops: the sidecar says, else its name does.
  function loops(name: string): boolean {
    const info = clipInfoRef.current?.find((c) => c.name === name)
    return typeof info?.loop === 'boolean' ? info.loop : clipLoopsByName(name)
  }

  function applyLoop(action: THREE.AnimationAction, name: string) {
    if (loops(name)) {
      action.setLoop(THREE.LoopRepeat, Infinity)
      action.clampWhenFinished = false
    } else {
      action.setLoop(THREE.LoopOnce, 1)
      action.clampWhenFinished = true
    }
  }

  // The sidecar may arrive after the model: apply its loop flags to the playing clip.
  useEffect(() => {
    if (actionRef.current && clip) applyLoop(actionRef.current, clip)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipInfo])

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

  // Turning Reduce Motion on while the viewer is open stops the turntable and pauses the clip.
  useEffect(() => {
    if (!reduceMotion) return
    setTurntable(false)
    if (actionRef.current && !actionRef.current.paused) {
      actionRef.current.paused = true
      setPlaying(false)
    }
  }, [reduceMotion])

  const changeShading = (s: Shading) => {
    setShadingState(s)
    if (modelRef.current) setShading(modelRef.current, s, originalsRef.current)
  }

  // Items on attachment points: each GLB loaded once, a copy parented to its Socket_* node with the saved offset.
  const attachRef = useRef<ViewerAttachment[] | null | undefined>(attachments)
  attachRef.current = attachments
  const itemCacheRef = useRef(new Map<string, Promise<THREE.Object3D>>())
  const placedRef = useRef<THREE.Object3D[]>([])
  function placeAttachments() {
    for (const o of placedRef.current) o.parent?.remove(o)
    placedRef.current = []
    const model = modelRef.current
    const list = attachRef.current ?? []
    if (!model || !list.length) return
    for (const a of list) {
      const sock = model.getObjectByName(a.socket)
      if (!sock || !a.url) continue
      let p = itemCacheRef.current.get(a.url)
      if (!p) {
        p = new GLTFLoader().loadAsync(a.url).then((g) => g.scene)
        itemCacheRef.current.set(a.url, p)
      }
      p.then((scene) => {
        if (modelRef.current !== model || attachRef.current !== list) return
        const item = scene.clone(true)
        item.name = 'item:' + a.id
        item.position.fromArray(a.position)
        item.quaternion.fromArray(a.rotation).normalize()
        // An item keeps its own size: undo any scale on the bone chain above the socket.
        const ws = sock.getWorldScale(new THREE.Vector3())
        const ms = model.getWorldScale(new THREE.Vector3())
        item.scale.setScalar((a.scale || 1) * (ms.x / (ws.x || 1)))
        setShading(item, shading, originalsRef.current)
        sock.add(item)
        placedRef.current.push(item)
      }).catch(() => setStatus('The item could not be loaded'))
    }
  }
  useEffect(() => {
    placeAttachments()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachments])
  // A clip chosen by buttons outside the viewer (Stand, Walk, Run, Attack): the exact name, else one containing it.
  // Also applied when a model finishes loading, so the first request wins over the default clip.
  const clipRequestRef = useRef(clipRequest)
  clipRequestRef.current = clipRequest
  function applyClipRequest() {
    const req = clipRequestRef.current
    if (!req) return
    const want = req.name.toLowerCase()
    const names = clipsRef.current.map((c) => c.name || 'clip')
    const hit = names.find((n) => n.toLowerCase() === want) ?? names.find((n) => n.toLowerCase().includes(want))
    if (hit) changeClip(hit)
  }
  useEffect(() => {
    applyClipRequest()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipRequest])

  const changeClip = (name: string) => {
    const h = handlesRef.current
    if (!h?.mixer) return
    const c = clipsRef.current.find((x) => (x.name || 'clip') === name)
    if (!c) return
    actionRef.current?.stop()
    const action = h.mixer.clipAction(c)
    applyLoop(action, name)
    action.reset().play()
    // Picking a clip plays it, unless Reduce Motion keeps it still until Play is pressed.
    const still = reduceMotion && !playing
    action.paused = still
    if (still) h.mixer.update(0)
    actionRef.current = action
    setClip(name)
    setPlaying(!still)
  }

  const togglePlay = () => {
    const next = !playing
    setPlaying(next)
    const a = actionRef.current
    if (!a) return
    // A one-shot clip that ended on its last frame starts again from the beginning.
    if (next && a.loop === THREE.LoopOnce && a.time >= a.getClip().duration - 1e-3) a.reset().play()
    a.paused = !next
  }

  const orderedClips = orderClips(clips, clipInfo)
  const hitCount = hitShapesOf(hitboxes).length
  const hasMoveShape = !!movementShapeOf(hitboxes)

  const resetView = () => {
    const h = handlesRef.current
    if (h && modelRef.current) {
      h.modelRoot.rotation.set(0, 0, 0)
      frameModel(h, modelRef.current)
      followResetRef.current = true
    }
  }

  return (
    <section className={'viewer ' + className} style={{ height: large ? 'min(86vh, 960px)' : height }}>
      <div className="viewer-toolbar">
        <div className="segmented">
          {(['textured', 'wireframe', 'normals'] as Shading[]).map((s) => (
            <button key={s} type="button" aria-pressed={shading === s} onClick={() => changeShading(s)}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
        <button type="button" aria-pressed={turntable} onClick={() => setTurntable((t) => !t)} title={reduceMotion && !turntable ? 'Off because your system asks for less motion; press to spin the model' : undefined}>
          Turntable
        </button>
        <details className="viewer-show">
          <summary title="Choose what to show on the model">Show</summary>
          <div className="viewer-show-pop">
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
        {hitCount > 0 && (
          <span className="viewer-toggle">
            <button type="button" aria-pressed={showHits} onClick={() => setShowHits((s) => !s)}>
              Hit boxes ({hitCount})
            </button>
            <Info term="term.hitboxes" />
          </span>
        )}
        {socketCount > 0 && (
          <span className="viewer-toggle">
            <button type="button" aria-pressed={showSockets} onClick={() => setShowSockets((s) => !s)} title="Sockets (attachment points): shows where a weapon, hat or backpack can be attached">
              Sockets ({socketCount})
            </button>
            <Info term="term.socket" />
          </span>
        )}
        {hasMoveShape && (
          <span className="viewer-toggle">
            <button type="button" aria-pressed={showMove} onClick={() => setShowMove((s) => !s)}>
              Body shape
            </button>
            <Info term="term.movement_shape" />
          </span>
        )}
          </div>
        </details>
        {clips.length > 0 && (
          <>
            <select value={clip} onChange={(e) => changeClip(e.target.value)} aria-label="Animation clip">
              {orderedClips.map((c) => (
                <option key={c} value={c}>
                  {/* The plain name the moves list uses, when the clip file gives one (added moves). */}
                  {clipInfo?.find((i) => i.name === c)?.label || clipLabel(c)} {loops(c) ? '(loops)' : '(plays once)'}
                </option>
              ))}
            </select>
            <button type="button" onClick={togglePlay} aria-pressed={playing} className={!playing && reduceMotion ? 'viewer-play' : undefined} title={!playing && reduceMotion ? 'Paused because your system asks for less motion; press to play' : undefined}>
              {playing ? 'Pause' : 'Play'}
            </button>
          </>
        )}
        <div className="segmented" role="group" aria-label="What dragging does">
          <button type="button" aria-pressed={dragMode === 'turn'} onClick={() => setDragMode('turn')} title="Dragging turns the model">
            Turn
          </button>
          <button type="button" aria-pressed={dragMode === 'move'} onClick={() => setDragMode('move')} title="Dragging moves the view sideways, up and down, so you can reach any part">
            Move
          </button>
        </div>
        <button type="button" aria-pressed={hintOn} aria-label="How to move around" title="Shows how to turn, move and zoom" onClick={() => setHintOn((h) => !h)}>
          ?
        </button>
        <button type="button" onClick={resetView} title="Shows the whole model again">
          Reset view
        </button>
        <button type="button" aria-pressed={large} onClick={() => setLarge((l) => !l)} title={large ? 'Back to the normal size' : 'Makes the preview taller, for looking at small parts'}>
          Larger view
        </button>
        <span className="viewer-status" aria-live="polite">
          {hoverLabel ?? status}
        </span>
      </div>
      <div className="canvas-host" ref={hostRef} onPointerDown={() => setHintOn(false)}>
        <LightPicker value={light} onChange={chooseLight} note={lightNote} />
        {hintOn && <div className="viewer-hint">
          {dragMode === 'turn' ? 'Drag to turn. Right-drag to move.' : 'Drag to move. Right-drag to turn.'} Hold Ctrl and scroll to zoom. Double-click a spot to look at it closely.
        </div>}
      </div>
    </section>
  )
}
