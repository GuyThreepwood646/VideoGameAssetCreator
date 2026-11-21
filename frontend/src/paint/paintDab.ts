// One brush dab on the model (WP-22). The dab works on the surface, not on the picture: every triangle near the spot is
// found in 3D (a BVH query), and each picture pixel that lies inside that triangle is painted if its 3D position is
// inside the brush. Two sides of a seam share the spot in 3D, so they paint the same colour there even though their
// picture areas are far apart. Triangles that face away from the brush, or that lie on the far side of a thin part, are
// skipped, so a stroke on the front does not reach the back.
//
// Smudge (WP-32) drags the colour under the brush along the stroke. Fill (WP-32) floods one connected surface: the
// triangles that share an edge with the clicked one, painted where the colour is close to the clicked colour.
import * as THREE from 'three'
import type { MeshBVH } from 'three-mesh-bvh'
import type { PaintLayer } from './paintLayer'

export type Brush = {
  rgb: [number, number, number] // 0 to 255, sRGB
  radius: number // metres on the model, from the centre of the spot
  hardness: number // 0 soft to 1 hard edge
  strength: number // 0 to 1
  erase: boolean // paint back to the original picture instead of the colour
}

// A mesh that uses the picture being painted, with the BVH that answers queries for it.
export type DabTarget = { mesh: THREE.Mesh; bvh: MeshBVH }

export type Dab = {
  point: THREE.Vector3 // world position of the spot
  normal: THREE.Vector3 // unit world normal at the spot
  hitMesh: THREE.Mesh // the mesh the spot is on
  face: number // the triangle the spot is on in hitMesh
  faceOnly: boolean // paint only that triangle
}

// Triangles facing more than about 78 degrees away from the brush are skipped.
export const REJECT_DOT = 0.2
// Points further than this share of the radius from the brush's plane are skipped (the far side of a thin part).
const SLAB = 0.5
const EPS = 1e-6

// Called for each picture texel inside a triangle, with the texel's index and its 3D point on the surface. Returns true
// when the texel changed.
type TexelVisit = (i: number, p: THREE.Vector3) => boolean

const _inv = new THREE.Matrix4()
const _c = new THREE.Vector3()
const _s = new THREE.Vector3()
const _wa = new THREE.Vector3()
const _wb = new THREE.Vector3()
const _wc = new THREE.Vector3()
const _n = new THREE.Vector3()
const _e1 = new THREE.Vector3()
const _e2 = new THREE.Vector3()
const _p = new THREE.Vector3()
const _q = new THREE.Vector3()

// The smooth edge: full strength inside hardness * radius, falling to nothing at the radius.
export function falloff(t: number, hardness: number): number {
  if (t >= 1) return 0
  const h = Math.min(0.999, Math.max(0, hardness))
  if (t <= h) return 1
  const k = (t - h) / (1 - h)
  return 1 - k * k * (3 - 2 * k)
}

// How strongly the brush reaches a surface point: 0 outside the brush, the edge falloff inside it.
function brushAlpha(p: THREE.Vector3, dab: Dab, brush: Brush): number {
  _q.subVectors(p, dab.point)
  const d = _q.length()
  if (d >= brush.radius) return 0
  if (Math.abs(_q.dot(dab.normal)) > SLAB * brush.radius) return 0
  return falloff(d / brush.radius, brush.hardness) * brush.strength
}

// Paints one dab onto the layer. The layer must be inside a stroke (beginStroke). Returns the number of pixels changed.
export function paintDab(layer: PaintLayer, targets: DabTarget[], dab: Dab, brush: Brush): number {
  const stroke = layer.stroke
  if (!stroke) throw new Error('paintDab outside a stroke')
  const { start, mask } = stroke
  const px = layer.pixels
  const orig = layer.original
  const [r, g, b] = brush.rgb
  return sweepDab(layer, targets, dab, brush, (i, p) => {
    const alpha = brushAlpha(p, dab, brush)
    if (alpha <= 0) return false
    // The strongest brush value each texel has had in this stroke: overlapping dabs do not build past the strength.
    if (alpha <= mask[i]) return false
    mask[i] = alpha
    const o = i * 4
    for (let c = 0; c < 3; c++) {
      const target = brush.erase ? orig[o + c] : c === 0 ? r : c === 1 ? g : b
      const from = start[o + c]
      px[o + c] = Math.round(from + (target - from) * alpha)
    }
    return true
  })
}

// Smudge (WP-32): the colour under the brush at the previous dab is dragged into the texels under this one. carry holds
// that colour for the stroke (null before the first dab). Each dab takes the colour under the brush before it paints,
// so a steady stroke carries the colour along with it. Undo still compares against the stroke's start copy.
export function smudgeDab(layer: PaintLayer, targets: DabTarget[], dab: Dab, brush: Brush, carry: { rgb: [number, number, number] | null }): number {
  const px = layer.pixels
  let sr = 0
  let sg = 0
  let sb = 0
  let sw = 0
  // Pass one: the colour under the brush now (nothing is changed).
  sweepDab(layer, targets, dab, brush, (i, p) => {
    const alpha = brushAlpha(p, dab, brush)
    if (alpha <= 0) return false
    const o = i * 4
    sr += px[o] * alpha
    sg += px[o + 1] * alpha
    sb += px[o + 2] * alpha
    sw += alpha
    return false
  })
  const pickup: [number, number, number] | null = sw > 0 ? [sr / sw, sg / sw, sb / sw] : null
  const drag = carry.rgb ?? pickup
  carry.rgb = pickup ?? carry.rgb
  if (!drag) return 0
  // Pass two: move the texels under the brush toward the dragged colour.
  return sweepDab(layer, targets, dab, brush, (i, p) => {
    const alpha = brushAlpha(p, dab, brush)
    if (alpha <= 0) return false
    const o = i * 4
    for (let c = 0; c < 3; c++) {
      const cur = px[o + c]
      px[o + c] = Math.round(cur + (drag[c] - cur) * alpha)
    }
    return true
  })
}

// Runs visit over every texel inside the brush's reach, on the triangles that face the brush.
function sweepDab(layer: PaintLayer, targets: DabTarget[], dab: Dab, brush: Brush, visit: TexelVisit): number {
  let changed = 0
  for (const t of targets) {
    const mesh = t.mesh
    mesh.updateWorldMatrix(true, false)
    const m = mesh.matrixWorld
    _inv.copy(m).invert()
    _c.copy(dab.point).applyMatrix4(_inv)
    const scale = _s.setFromMatrixScale(m)
    const localRadius = brush.radius / ((scale.x + scale.y + scale.z) / 3)
    const tris = dab.faceOnly ? (mesh === dab.hitMesh ? [dab.face] : []) : nearTriangles(t.bvh, _c, localRadius)
    for (const f of tris) changed += rasterTriangle(layer, mesh, f, dab.normal, visit)
  }
  return changed
}

// Triangles whose surface comes within the radius of the local point c.
function nearTriangles(bvh: MeshBVH, c: THREE.Vector3, radius: number): number[] {
  const out: number[] = []
  bvh.shapecast({
    intersectsBounds: (box) => box.distanceToPoint(c) <= radius,
    intersectsTriangle: (tri, index) => {
      if (tri.closestPointToPoint(c, _q).distanceTo(c) <= radius) out.push(index)
      return false
    },
  })
  return out
}

// Visits the picture texels inside triangle f of the mesh. A facing vector, when given, skips a triangle that turns away
// from it (the back-surface rejection). Changed texels are reported to the layer so the GPU gets only that rectangle.
function rasterTriangle(layer: PaintLayer, mesh: THREE.Mesh, f: number, facing: THREE.Vector3 | null, visit: TexelVisit): number {
  const geo = mesh.geometry
  const pos = geo.attributes.position as THREE.BufferAttribute
  const uv = geo.attributes.uv as THREE.BufferAttribute | undefined
  if (!uv) return 0
  const idx = geo.index
  const ia = idx ? idx.getX(f * 3) : f * 3
  const ib = idx ? idx.getX(f * 3 + 1) : f * 3 + 1
  const ic = idx ? idx.getX(f * 3 + 2) : f * 3 + 2
  const m = mesh.matrixWorld
  _wa.fromBufferAttribute(pos, ia).applyMatrix4(m)
  _wb.fromBufferAttribute(pos, ib).applyMatrix4(m)
  _wc.fromBufferAttribute(pos, ic).applyMatrix4(m)
  _e1.subVectors(_wb, _wa)
  _e2.subVectors(_wc, _wa)
  _n.crossVectors(_e1, _e2)
  if (_n.lengthSq() === 0) return 0
  _n.normalize()
  if (facing && _n.dot(facing) < REJECT_DOT) return 0

  const W = layer.width
  const H = layer.height
  // Picture pixel coordinates of the corners (row = v * height, as the texture is not flipped).
  const ax = uv.getX(ia) * W
  const ay = uv.getY(ia) * H
  const bx = uv.getX(ib) * W
  const by = uv.getY(ib) * H
  const cx = uv.getX(ic) * W
  const cy = uv.getY(ic) * H
  const denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
  if (Math.abs(denom) < 1e-12) return 0
  const x0 = Math.floor(Math.min(ax, bx, cx))
  const x1 = Math.ceil(Math.max(ax, bx, cx))
  const y0 = Math.floor(Math.min(ay, by, cy))
  const y1 = Math.ceil(Math.max(ay, by, cy))
  // A triangle much bigger than the picture would wrap the picture; those are not painted.
  if (x1 - x0 > W || y1 - y0 > H) return 0

  let changed = 0
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (let y = y0; y < y1; y++) {
    const cyp = y + 0.5
    for (let x = x0; x < x1; x++) {
      const cxp = x + 0.5
      const l1 = ((by - cy) * (cxp - cx) + (cx - bx) * (cyp - cy)) / denom
      const l2 = ((cy - ay) * (cxp - cx) + (ax - cx) * (cyp - cy)) / denom
      const l3 = 1 - l1 - l2
      if (l1 < -EPS || l2 < -EPS || l3 < -EPS) continue
      _p.set(
        _wa.x * l1 + _wb.x * l2 + _wc.x * l3,
        _wa.y * l1 + _wb.y * l2 + _wc.y * l3,
        _wa.z * l1 + _wb.z * l2 + _wc.z * l3,
      )
      // Texels outside 0 to 1 repeat the picture.
      const wx = ((x % W) + W) % W
      const wy = ((y % H) + H) % H
      if (!visit(wy * W + wx, _p)) continue
      changed++
      if (wx < minX) minX = wx
      if (wy < minY) minY = wy
      if (wx > maxX) maxX = wx
      if (wy > maxY) maxY = wy
    }
  }
  if (changed > 0) layer.touch(minX, minY, maxX + 1, maxY + 1)
  return changed
}

// --- Fill (WP-32) -----------------------------------------------------------------------------------------------------

export type FillSeed = { mesh: THREE.Mesh; face: number; rgb: [number, number, number] }
export type Fill = { rgb: [number, number, number]; tolerance: number; strength: number } // tolerance 0 to 1 of the colour range

// Connected triangles per geometry: two triangles are neighbours when they share an edge (by welded local position), so
// the fill follows one surface and never jumps to a part that only touches it at a corner or lies apart in 3D.
const neighbourCache = new WeakMap<THREE.BufferGeometry, number[][]>()

function neighboursOf(geo: THREE.BufferGeometry): number[][] {
  const cached = neighbourCache.get(geo)
  if (cached) return cached
  const pos = geo.attributes.position as THREE.BufferAttribute
  const idx = geo.index
  const triCount = Math.floor((idx ? idx.count : pos.count) / 3)
  const vertKey = (v: number): string => {
    const i = idx ? idx.getX(v) : v
    return `${Math.round(pos.getX(i) * 1e5)},${Math.round(pos.getY(i) * 1e5)},${Math.round(pos.getZ(i) * 1e5)}`
  }
  const edges = new Map<string, number[]>()
  const out: number[][] = Array.from({ length: triCount }, () => [])
  for (let f = 0; f < triCount; f++) {
    const keys = [vertKey(f * 3), vertKey(f * 3 + 1), vertKey(f * 3 + 2)]
    for (let k = 0; k < 3; k++) {
      const a = keys[k]
      const b = keys[(k + 1) % 3]
      const e = a < b ? `${a}|${b}` : `${b}|${a}`
      const list = edges.get(e)
      if (list) {
        for (const g of list) {
          if (g === f) continue
          out[f].push(g)
          out[g].push(f)
        }
        list.push(f)
      } else {
        edges.set(e, [f])
      }
    }
  }
  neighbourCache.set(geo, out)
  return out
}

// Fills the connected surface of the seed triangle, texel by texel: a texel is painted when its colour is within the
// tolerance of the seed colour. One call is one undo step (the caller wraps it in a stroke).
export function fillRegion(layer: PaintLayer, seed: FillSeed, fill: Fill): number {
  const px = layer.pixels
  const done = new Uint8Array(layer.width * layer.height)
  const limit = fill.tolerance * 255 * Math.sqrt(3)
  const target = fill.rgb
  const near = (o: number): boolean => {
    const dr = px[o] - seed.rgb[0]
    const dg = px[o + 1] - seed.rgb[1]
    const db = px[o + 2] - seed.rgb[2]
    return Math.sqrt(dr * dr + dg * dg + db * db) <= limit
  }
  const adjacency = neighboursOf(seed.mesh.geometry)
  const seen = new Uint8Array(adjacency.length)
  const queue: number[] = [seed.face]
  seen[seed.face] = 1
  let changed = 0
  while (queue.length) {
    const f = queue.pop()!
    changed += rasterTriangle(layer, seed.mesh, f, null, (i) => {
      if (done[i]) return false
      const o = i * 4
      if (!near(o)) return false
      done[i] = 1
      for (let c = 0; c < 3; c++) {
        const cur = px[o + c]
        px[o + c] = Math.round(cur + (target[c] - cur) * fill.strength)
      }
      return true
    })
    for (const n of adjacency[f]) {
      if (seen[n]) continue
      seen[n] = 1
      queue.push(n)
    }
  }
  return changed
}
