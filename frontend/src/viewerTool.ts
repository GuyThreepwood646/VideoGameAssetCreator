// Pointer tools for the 3D preview (WP-21). A tool takes the left pointer while it is active; orbit stays on the right
// button, the wheel and the arrow keys. Escape leaves the tool. A tool hears down, drag, up and hover with the surface
// spot under the pointer (or null when the pointer is off the model), so painting and marking points use one path.
//
// Hits are taken against the mesh in rest pose: the geometry as the file stores it (bind pose for skinned meshes), moved
// by the mesh's own world matrix. A clip playing or the turntable turning does not move the spots a tool finds.
import * as THREE from 'three'
import { MeshBVH } from 'three-mesh-bvh'

// One spot on the model, in scene (world) space.
export type SurfaceHit = {
  point: THREE.Vector3 // world position of the spot
  normal: THREE.Vector3 // unit world normal of the triangle, facing out of the front
  face: number // triangle index in the mesh's geometry
  uv: THREE.Vector2 | null // UV set 0 at the spot, null when the mesh has no UVs
  mesh: THREE.Mesh // the mesh that was hit
  distance: number // from the camera to the spot, in scene units
}

// A tool plugs into the viewer. Keep the object stable (useMemo or useRef): when the object changes the viewer calls
// the old tool's cancel().
export type ViewerTool = {
  id: string
  // Cursor over the picture while the tool is active (default crosshair).
  cursor?: string
  // Left button pressed over the picture; hit is null when the pointer is off the model.
  down?: (hit: SurfaceHit | null, ev: PointerEvent) => void
  // Pointer moved with the button held (the tool owns that drag until up).
  drag?: (hit: SurfaceHit | null, ev: PointerEvent) => void
  // Left button released (also after a cancelled pointer).
  up?: (hit: SurfaceHit | null, ev: PointerEvent) => void
  // Pointer moved with no button held. Only called when defined, so a tool that does not need hover costs nothing.
  hover?: (hit: SurfaceHit | null, ev: PointerEvent) => void
  // Escape, the tool being replaced, or the viewer unmounting: drop anything half done.
  cancel?: () => void
}

const _inv = new THREE.Matrix4()
const _normalMatrix = new THREE.Matrix3()
const _ray = new THREE.Ray()
const _a = new THREE.Vector3()
const _b = new THREE.Vector3()
const _c = new THREE.Vector3()
const _edge1 = new THREE.Vector3()
const _edge2 = new THREE.Vector3()
const _uvA = new THREE.Vector2()
const _uvB = new THREE.Vector2()
const _uvC = new THREE.Vector2()
const _uvOut = new THREE.Vector2()

function isShown(o: THREE.Object3D): boolean {
  for (let n: THREE.Object3D | null = o; n; n = n.parent) if (!n.visible) return false
  return true
}

// Finds the nearest front-facing spot on a set of meshes. Each mesh gets a BVH over its geometry the first time it is
// picked, so a viewer that never uses a tool never pays for one.
export class SurfacePicker {
  private readonly bvhs = new WeakMap<THREE.BufferGeometry, MeshBVH>()
  private readonly meshes: THREE.Mesh[]

  constructor(meshes: THREE.Mesh[]) {
    this.meshes = meshes.filter((m) => !!m.geometry && !!m.geometry.attributes.position)
  }

  // ray is in scene space. Returns the nearest visible spot, or null.
  pick(ray: THREE.Ray): SurfaceHit | null {
    let best: { mesh: THREE.Mesh; face: number; point: THREE.Vector3; local: THREE.Vector3; distance: number } | null = null
    for (const mesh of this.meshes) {
      if (!isShown(mesh)) continue
      mesh.updateWorldMatrix(true, false)
      _inv.copy(mesh.matrixWorld).invert()
      _ray.copy(ray).applyMatrix4(_inv)
      const bvh = this.bvhOf(mesh.geometry)
      const hit = bvh.raycastFirst(_ray, THREE.FrontSide)
      if (!hit || hit.faceIndex == null) continue
      const point = hit.point.clone().applyMatrix4(mesh.matrixWorld)
      const distance = point.distanceTo(ray.origin)
      if (!best || distance < best.distance) best = { mesh, face: hit.faceIndex, point, local: hit.point.clone(), distance }
    }
    if (!best) return null
    const { mesh, face, point, local, distance } = best
    return { point, normal: this.normalAt(mesh, face), face, uv: this.uvAt(mesh, face, local), mesh, distance }
  }

  private bvhOf(geometry: THREE.BufferGeometry): MeshBVH {
    let bvh = this.bvhs.get(geometry)
    if (!bvh) {
      bvh = new MeshBVH(geometry)
      this.bvhs.set(geometry, bvh)
    }
    return bvh
  }

  // Corner indices of triangle `face`, from the index buffer when there is one.
  private corners(geometry: THREE.BufferGeometry, face: number): [number, number, number] {
    const index = geometry.index
    if (index) return [index.getX(face * 3), index.getX(face * 3 + 1), index.getX(face * 3 + 2)]
    return [face * 3, face * 3 + 1, face * 3 + 2]
  }

  private normalAt(mesh: THREE.Mesh, face: number): THREE.Vector3 {
    const pos = mesh.geometry.attributes.position
    const [ia, ib, ic] = this.corners(mesh.geometry, face)
    _a.fromBufferAttribute(pos, ia)
    _b.fromBufferAttribute(pos, ib)
    _c.fromBufferAttribute(pos, ic)
    _edge1.subVectors(_b, _a)
    _edge2.subVectors(_c, _a)
    const n = new THREE.Vector3().crossVectors(_edge1, _edge2)
    _normalMatrix.getNormalMatrix(mesh.matrixWorld)
    n.applyMatrix3(_normalMatrix)
    return n.lengthSq() > 0 ? n.normalize() : n
  }

  private uvAt(mesh: THREE.Mesh, face: number, local: THREE.Vector3): THREE.Vector2 | null {
    const geometry = mesh.geometry
    const uv = geometry.attributes.uv as THREE.BufferAttribute | undefined
    if (!uv) return null
    const pos = geometry.attributes.position
    const [ia, ib, ic] = this.corners(geometry, face)
    _a.fromBufferAttribute(pos, ia)
    _b.fromBufferAttribute(pos, ib)
    _c.fromBufferAttribute(pos, ic)
    _uvA.fromBufferAttribute(uv, ia)
    _uvB.fromBufferAttribute(uv, ib)
    _uvC.fromBufferAttribute(uv, ic)
    THREE.Triangle.getInterpolation(local, _a, _b, _c, _uvA, _uvB, _uvC, _uvOut)
    return _uvOut.clone()
  }
}

// The simplest tool: reports the spot a click lands on. Step 4 uses it to show what the picker finds; a tool that needs
// more (painting, marking) follows the same shape.
export function spotProbe(onSpot: (hit: SurfaceHit | null) => void): ViewerTool {
  return { id: 'spot-probe', cursor: 'crosshair', down: (hit) => onSpot(hit) }
}

// Converts a pointer event to a scene ray through the camera. Used by the viewer; exported for tools that want the ray.
export function pointerRay(e: PointerEvent, canvas: HTMLElement, camera: THREE.Camera, out: THREE.Raycaster): THREE.Ray {
  const rect = canvas.getBoundingClientRect()
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1)
  out.setFromCamera(ndc, camera)
  return out.ray
}
