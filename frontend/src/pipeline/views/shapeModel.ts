// The Shape tab's settings and geometry helpers (WP-26). Pure functions: the tab keeps the state, the backend does the
// cut. Ranges match backend/vgac/separate.py (GAP_RANGE, START_RANGE) and main.py ShapeIn.
import * as THREE from 'three'
import type { ShapeArm, ShapeBody } from '../../api'

export type ShapeSide = 'L' | 'R' | 'both'
export type ShapeSettings = { side: ShapeSide; gapMm: number; startPercent: number }

export const GAP_MM = { min: 2, max: 20, step: 1 }
export const START_PERCENT = { min: 15, max: 60, step: 1 }
export const DEFAULT_SHAPE: ShapeSettings = { side: 'both', gapMm: 6, startPercent: 30 }

export const SIDE_CHOICES: { side: ShapeSide; label: string }[] = [
  { side: 'L', label: 'Left arm' },
  { side: 'R', label: 'Right arm' },
  { side: 'both', label: 'Both arms' },
]

export function clampShape(s: ShapeSettings): ShapeSettings {
  const side: ShapeSide = s.side === 'L' || s.side === 'R' ? s.side : 'both'
  const gapMm = Math.min(GAP_MM.max, Math.max(GAP_MM.min, Math.round(s.gapMm)))
  const startPercent = Math.min(START_PERCENT.max, Math.max(START_PERCENT.min, Math.round(s.startPercent)))
  return { side, gapMm, startPercent }
}

// The request body: parameters only, in the units the server takes (metres and a share of the upper arm).
export function shapeBody(s: ShapeSettings): ShapeBody {
  const c = clampShape(s)
  return { operation: 'separate', side: c.side, gap: c.gapMm / 1000, start: c.startPercent / 100 }
}

export function armWords(side: ShapeSide): string {
  return SIDE_CHOICES.find((c) => c.side === side)?.label.toLowerCase() ?? 'arms'
}

export function gapWords(mm: number): string {
  return `${mm} mm`
}

export function startWords(percent: number): string {
  return `${percent} percent of the way down the upper arm, from the shoulder`
}

// The arm nearest a point on the model: the side whose shoulder-to-elbow segment is closest. Null with no arms.
export function nearestArm(point: { x: number; y: number; z: number }, arms: Partial<Record<'L' | 'R', ShapeArm>>): 'L' | 'R' | null {
  let best: 'L' | 'R' | null = null
  let bestD = Infinity
  for (const side of ['L', 'R'] as const) {
    const arm = arms[side]
    if (!arm) continue
    const d = distanceToSegment(point, arm.shoulder, arm.elbow)
    if (d < bestD) {
      bestD = d
      best = side
    }
  }
  return best
}

export function distanceToSegment(p: { x: number; y: number; z: number }, a: number[], b: number[]): number {
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const ap = [p.x - a[0], p.y - a[1], p.z - a[2]]
  const len2 = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2] || 1e-12
  const t = Math.min(1, Math.max(0, (ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / len2))
  const q = [a[0] + t * ab[0] - p.x, a[1] + t * ab[1] - p.y, a[2] + t * ab[2] - p.z]
  return Math.hypot(q[0], q[1], q[2])
}

// The triangles of one mesh inside the band where the cut starts: the centroid within the band's half width of the
// plane through cut_start (normal = the upper arm's axis) and within radius_m of it. Returns vertex indices, three per
// triangle, as RegionHighlight takes them. Uses the mesh's world matrix, so the band sits where the model is drawn.
export function bandVertices(mesh: THREE.Mesh, arm: ShapeArm): Uint32Array {
  const pos = mesh.geometry.getAttribute('position') as THREE.BufferAttribute | undefined
  if (!pos) return new Uint32Array(0)
  mesh.updateWorldMatrix(true, false)
  const index = mesh.geometry.getIndex()
  const triCount = index ? index.count / 3 : pos.count / 3
  const v = new THREE.Vector3()
  const cut = new THREE.Vector3(...arm.cut_start)
  const axis = new THREE.Vector3(...arm.axis).normalize()
  const at = (i: number) => v.fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld)
  const out: number[] = []
  const c = new THREE.Vector3()
  for (let t = 0; t < triCount; t++) {
    const ia = index ? index.getX(t * 3) : t * 3
    const ib = index ? index.getX(t * 3 + 1) : t * 3 + 1
    const ic = index ? index.getX(t * 3 + 2) : t * 3 + 2
    const a = at(ia).clone()
    const b = at(ib).clone()
    const cc = at(ic).clone()
    c.copy(a).add(b).add(cc).multiplyScalar(1 / 3)
    const offPlane = Math.abs(c.clone().sub(cut).dot(axis))
    if (offPlane <= arm.half_band_m && c.distanceTo(cut) <= arm.radius_m) out.push(ia, ib, ic)
  }
  return new Uint32Array(out)
}

// The mesh that carries most of the band, with its triangles (the viewer's highlight takes one mesh).
export function bandHighlight(meshes: THREE.Mesh[], arm: ShapeArm): { mesh: THREE.Mesh; triangles: Uint32Array } | null {
  let best: { mesh: THREE.Mesh; triangles: Uint32Array } | null = null
  for (const mesh of meshes) {
    const triangles = bandVertices(mesh, arm)
    if (triangles.length && (!best || triangles.length > best.triangles.length)) best = { mesh, triangles }
  }
  return best
}
