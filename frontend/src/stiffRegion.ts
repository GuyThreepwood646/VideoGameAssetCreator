// The region a stiff part covers, grown from its points on the model (WP-69). This mirrors backend/vgac/blender/
// worn_region.py (grow_region and fill_holes), so the preview on the rig page is the region the rig step will make.
// Keep the two in step: the same rules, the same constants.
//
// Growth crosses mesh edges and seam duplicates (same position, normals within about 30 degrees), and stops at a
// concave edge: where the next face lies behind this one (a valley, such as where a backpack meets the back). Convex
// ridges, such as a box's own edges, are crossed. Safety nets: a radius from the point and a vertex limit. Then holes
// (a vertex outside whose edge neighbours are all inside) are taken in, for a few rounds.

export const STIFF_RADIUS = 0.8 // metres from a point; the region never reaches further
export const STIFF_LIMIT = 4000 // vertices per point
export const STIFF_HOLE_ROUNDS = 3
const CREASE_DOT = 0.87 // seam duplicates join only when their normals agree within about 30 degrees

export type Vec3 = [number, number, number]

// positions: flat x, y, z per vertex. indices: triangles (three per face), into positions.
export function growStiffRegion(
  positions: ArrayLike<number>,
  indices: ArrayLike<number>,
  point: Vec3,
  radius = STIFF_RADIUS,
  limit = STIFF_LIMIT,
): Uint8Array {
  const V = Math.floor(positions.length / 3)
  const mask = new Uint8Array(V)
  const F = Math.floor(indices.length / 3)
  if (V === 0 || F === 0) return mask
  const px = (v: number) => positions[3 * v]
  const py = (v: number) => positions[3 * v + 1]
  const pz = (v: number) => positions[3 * v + 2]
  const dist = new Float64Array(V)
  let start = 0
  for (let v = 0; v < V; v++) {
    const dx = px(v) - point[0]
    const dy = py(v) - point[1]
    const dz = pz(v) - point[2]
    dist[v] = Math.sqrt(dx * dx + dy * dy + dz * dz)
    if (dist[v] < dist[start]) start = v
  }
  if (dist[start] > radius) return mask

  // face centres and unit normals (counter-clockwise order, as the glTF file stores them)
  const fc = new Float64Array(F * 3)
  const fn = new Float64Array(F * 3)
  for (let f = 0; f < F; f++) {
    const a = indices[3 * f]
    const b = indices[3 * f + 1]
    const c = indices[3 * f + 2]
    fc[3 * f] = (px(a) + px(b) + px(c)) / 3
    fc[3 * f + 1] = (py(a) + py(b) + py(c)) / 3
    fc[3 * f + 2] = (pz(a) + pz(b) + pz(c)) / 3
    const ux = px(b) - px(a)
    const uy = py(b) - py(a)
    const uz = pz(b) - pz(a)
    const wx = px(c) - px(a)
    const wy = py(c) - py(a)
    const wz = pz(c) - pz(a)
    let nx = uy * wz - uz * wy
    let ny = uz * wx - ux * wz
    let nz = ux * wy - uy * wx
    const len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1e-12
    nx /= len
    ny /= len
    nz /= len
    fn[3 * f] = nx
    fn[3 * f + 1] = ny
    fn[3 * f + 2] = nz
  }
  // vertex normals: the face normals summed at each vertex, normalised
  const vn = new Float64Array(V * 3)
  for (let f = 0; f < F; f++) {
    for (let k = 0; k < 3; k++) {
      const v = indices[3 * f + k]
      vn[3 * v] += fn[3 * f]
      vn[3 * v + 1] += fn[3 * f + 1]
      vn[3 * v + 2] += fn[3 * f + 2]
    }
  }
  for (let v = 0; v < V; v++) {
    const len = Math.sqrt(vn[3 * v] ** 2 + vn[3 * v + 1] ** 2 + vn[3 * v + 2] ** 2) || 1e-12
    vn[3 * v] /= len
    vn[3 * v + 1] /= len
    vn[3 * v + 2] /= len
  }

  // edges: (min, max) -> the faces that use them
  const edgeFaces = new Map<number, number[]>()
  const key = (p: number, q: number) => (p < q ? p * V + q : q * V + p)
  for (let f = 0; f < F; f++) {
    for (let k = 0; k < 3; k++) {
      const p = indices[3 * f + k]
      const q = indices[3 * f + ((k + 1) % 3)]
      const e = key(p, q)
      const list = edgeFaces.get(e)
      if (list) list.push(f)
      else edgeFaces.set(e, [f])
    }
  }
  const adj: number[][] = Array.from({ length: V }, () => [])
  // every mesh edge, for the hole fill (the growth's own joins are not used there, as in worn_region.py)
  const meshAdj: number[][] = Array.from({ length: V }, () => [])
  for (const e of edgeFaces.keys()) {
    const p = Math.floor(e / V)
    const q = e - p * V
    meshAdj[p].push(q)
    meshAdj[q].push(p)
  }
  for (const [e, faces] of edgeFaces) {
    const p = Math.floor(e / V)
    const q = e - p * V
    let concave = false
    if (faces.length === 2) {
      const f1 = faces[0]
      const f2 = faces[1]
      const d12 = (fc[3 * f2] - fc[3 * f1]) * fn[3 * f1] + (fc[3 * f2 + 1] - fc[3 * f1 + 1]) * fn[3 * f1 + 1] + (fc[3 * f2 + 2] - fc[3 * f1 + 2]) * fn[3 * f1 + 2]
      const d21 = (fc[3 * f1] - fc[3 * f2]) * fn[3 * f2] + (fc[3 * f1 + 1] - fc[3 * f2 + 1]) * fn[3 * f2 + 1] + (fc[3 * f1 + 2] - fc[3 * f2 + 2]) * fn[3 * f2 + 2]
      concave = d12 < -1e-6 || d21 < -1e-6
    }
    if (!concave) {
      adj[p].push(q)
      adj[q].push(p)
    }
  }
  // seam duplicates: the same position, normals agreeing
  const buckets = new Map<string, number[]>()
  for (let v = 0; v < V; v++) {
    const k = `${px(v).toFixed(5)},${py(v).toFixed(5)},${pz(v).toFixed(5)}`
    const list = buckets.get(k)
    if (list) list.push(v)
    else buckets.set(k, [v])
  }
  for (const group of buckets.values()) {
    if (group.length < 2) continue
    for (const a of group) {
      for (const b of group) {
        if (a < b && vn[3 * a] * vn[3 * b] + vn[3 * a + 1] * vn[3 * b + 1] + vn[3 * a + 2] * vn[3 * b + 2] > CREASE_DOT) {
          adj[a].push(b)
          adj[b].push(a)
        }
      }
    }
  }

  // flood from the start vertex, within the radius and the limit
  mask[start] = 1
  const stack = [start]
  let count = 1
  while (stack.length && count < limit) {
    const u = stack.pop() as number
    for (const w of adj[u]) {
      if (count >= limit) break
      if (!mask[w] && dist[w] <= radius) {
        mask[w] = 1
        count++
        stack.push(w)
      }
    }
  }
  fillHoles(mask, meshAdj, STIFF_HOLE_ROUNDS)
  return mask
}

// Take in vertices outside the region whose edge neighbours are all inside it, for a few rounds (in place).
export function fillHoles(mask: Uint8Array, adj: number[][], rounds = STIFF_HOLE_ROUNDS): number {
  let added = 0
  for (let r = 0; r < rounds; r++) {
    const grow: number[] = []
    for (let v = 0; v < mask.length; v++) {
      if (!mask[v] && adj[v].length && adj[v].every((w) => mask[w])) grow.push(v)
    }
    if (!grow.length) break
    for (const v of grow) mask[v] = 1
    added += grow.length
  }
  return added
}

// The triangles whose three vertices are all in the region, as a flat index list (for the preview).
export function regionTriangles(indices: ArrayLike<number>, mask: Uint8Array): Uint32Array {
  const out: number[] = []
  for (let f = 0; f < Math.floor(indices.length / 3); f++) {
    const a = indices[3 * f]
    const b = indices[3 * f + 1]
    const c = indices[3 * f + 2]
    if (mask[a] && mask[b] && mask[c]) out.push(a, b, c)
  }
  return Uint32Array.from(out)
}
