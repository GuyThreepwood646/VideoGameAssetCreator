// The piece a hinged or sliding part's points fall on (WP-29), shown on the rig page before the points are kept. It is
// the same two-front growth as the rig step's finder (backend/vgac/blender/moving_parts.py find_pieces): one front
// grows from the points and one from the farthest share of the surface, both paying a toll to step into a concave
// crease (the groove round a lid or a drawer front), and a point belongs to the piece when the points' front reaches it
// first. Wheels are not shown here: the rig step finds them from their round shape, which this growth does not do.

export type Vec3 = [number, number, number]

export const CREASE_TOLL = 5 // times the model's size, for a right-angle crease (moving_parts.CREASE_TOLL)
export const FAR_SHARE = 0.3 // share of the surface, farthest from the points, the other front starts from
export const WELD = 1e-5 // metres: seam copies closer than this are one surface point

// Seam copies share a point. -> [the welded point of each vertex, the welded positions].
export function weld(pos: ArrayLike<number>, tol = WELD): [Int32Array, Float64Array] {
  const n = Math.floor(pos.length / 3)
  const of = new Int32Array(n)
  const keys = new Map<string, number>()
  const out: number[] = []
  for (let i = 0; i < n; i++) {
    const k = `${Math.round(pos[3 * i] / tol)},${Math.round(pos[3 * i + 1] / tol)},${Math.round(pos[3 * i + 2] / tol)}`
    let w = keys.get(k)
    if (w === undefined) {
      w = out.length / 3
      keys.set(k, w)
      out.push(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2])
    }
    of[i] = w
  }
  return [of, Float64Array.from(out)]
}

type Graph = { adj: [number, number][][]; sharp: Float64Array; size: number }

// Welded edges with their lengths, and each point's crease sharpness: 0 flat or convex, 1 a right angle or sharper,
// concave when either face's centre lies in front of the other face (moving_parts.surface_graph).
export function surfaceGraph(w: Float64Array, faces: number[]): Graph {
  const n = w.length / 3
  const adj: [number, number][][] = Array.from({ length: n }, () => [])
  const sharp = new Float64Array(n)
  const seen = new Map<string, number>()
  const normal: number[][] = []
  const centre: number[][] = []
  const P = (i: number) => [w[3 * i], w[3 * i + 1], w[3 * i + 2]]
  for (let f = 0; f < faces.length / 3; f++) {
    const [a, b, c] = [faces[3 * f], faces[3 * f + 1], faces[3 * f + 2]]
    if (a === b || b === c || a === c) continue
    const pa = P(a), pb = P(b), pc = P(c)
    const u = [pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]]
    const v = [pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]]
    const nn = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    const len = Math.hypot(nn[0], nn[1], nn[2]) || 1e-12
    const id = normal.length
    normal.push([nn[0] / len, nn[1] / len, nn[2] / len])
    centre.push([(pa[0] + pb[0] + pc[0]) / 3, (pa[1] + pb[1] + pc[1]) / 3, (pa[2] + pb[2] + pc[2]) / 3])
    for (const [p, q] of [[a, b], [b, c], [c, a]]) {
      const lo = Math.min(p, q), hi = Math.max(p, q)
      const key = `${lo},${hi}`
      const other = seen.get(key)
      if (other === undefined) {
        seen.set(key, id)
        const pl = P(lo), ph = P(hi)
        const d = Math.hypot(pl[0] - ph[0], pl[1] - ph[1], pl[2] - ph[2])
        adj[lo].push([hi, d])
        adj[hi].push([lo, d])
        continue
      }
      const n1 = normal[other], n2 = normal[id], c1 = centre[other], c2 = centre[id]
      const front = (nn_: number[], from: number[], to: number[]) => nn_[0] * (to[0] - from[0]) + nn_[1] * (to[1] - from[1]) + nn_[2] * (to[2] - from[2]) > 1e-9
      if (front(n1, c1, c2) || front(n2, c2, c1)) {
        const dot = Math.max(-1, Math.min(1, n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]))
        const s = Math.min(Math.acos(dot) / (Math.PI / 2), 1)
        sharp[lo] = Math.max(sharp[lo], s)
        sharp[hi] = Math.max(sharp[hi], s)
      }
    }
  }
  let size = 0
  for (let k = 0; k < 3; k++) {
    let lo = Infinity, hi = -Infinity
    for (let i = k; i < w.length; i += 3) {
      lo = Math.min(lo, w[i])
      hi = Math.max(hi, w[i])
    }
    if (hi > lo) size = Math.max(size, hi - lo)
  }
  return { adj, sharp, size }
}

// Cheapest cost from any source to every point (Dijkstra on a binary heap); toll: extra cost of stepping onto a point.
export function spread(adj: [number, number][][], sources: number[], toll?: Float64Array): Float64Array {
  const dist = new Float64Array(adj.length).fill(Infinity)
  const heap: [number, number][] = []
  const push = (d: number, v: number) => {
    heap.push([d, v])
    let i = heap.length - 1
    while (i > 0) {
      const p = (i - 1) >> 1
      if (heap[p][0] <= heap[i][0]) break
      ;[heap[p], heap[i]] = [heap[i], heap[p]]
      i = p
    }
  }
  const pop = (): [number, number] => {
    const top = heap[0]
    const last = heap.pop()!
    if (heap.length) {
      heap[0] = last
      let i = 0
      for (;;) {
        const l = 2 * i + 1, r = l + 1
        let m = i
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r
        if (m === i) break
        ;[heap[m], heap[i]] = [heap[i], heap[m]]
        i = m
      }
    }
    return top
  }
  for (const s of sources) {
    if (dist[s] > 0) {
      dist[s] = 0
      push(0, s)
    }
  }
  while (heap.length) {
    const [d, u] = pop()
    if (d > dist[u]) continue
    for (const [v, len] of adj[u]) {
      const nd = d + len + (toll ? toll[v] : 0)
      if (nd < dist[v]) {
        dist[v] = nd
        push(nd, v)
      }
    }
  }
  return dist
}

// The piece the points fall on, as a 0/1 mask over the mesh's own vertices (seam copies included). pos: the mesh's
// positions (x, y, z flat); index: its triangles; points: in the same frame. Only the parts of the grown front that
// hold a point are kept.
export function growMovingPiece(pos: ArrayLike<number>, index: ArrayLike<number>, points: Vec3[], toll = CREASE_TOLL, farShare = FAR_SHARE): Uint8Array {
  const n = Math.floor(pos.length / 3)
  const out = new Uint8Array(n)
  if (!points.length || !n) return out
  const [of, w] = weld(pos)
  const faces = Array.from(index, (i) => of[i])
  const g = surfaceGraph(w, faces)
  const m = w.length / 3
  const src = new Set<number>()
  for (const p of points) {
    let best = -1, bd = Infinity
    for (let i = 0; i < m; i++) {
      const d = (w[3 * i] - p[0]) ** 2 + (w[3 * i + 1] - p[1]) ** 2 + (w[3 * i + 2] - p[2]) ** 2
      if (d < bd) [bd, best] = [d, i]
    }
    if (best >= 0) src.add(best)
  }
  const plain = spread(g.adj, [...src])
  const reached = Array.from(plain).filter((d) => Number.isFinite(d)).sort((a, b) => a - b)
  const cut = reached[Math.min(reached.length - 1, Math.floor(reached.length * (1 - farShare)))]
  const far: number[] = []
  for (let i = 0; i < m; i++) if (Number.isFinite(plain[i]) && plain[i] >= cut) far.push(i)
  const tolls = new Float64Array(m)
  for (let i = 0; i < m; i++) tolls[i] = toll * g.size * g.sharp[i] ** 2
  const near = spread(g.adj, [...src], tolls)
  const away = spread(g.adj, far, tolls)
  const mine = new Uint8Array(m)
  for (let i = 0; i < m; i++) mine[i] = Number.isFinite(plain[i]) && near[i] < away[i] ? 1 : 0
  // keep the connected parts that hold a point
  const keep = new Uint8Array(m)
  const stack = [...src].filter((s) => mine[s])
  for (const s of stack) keep[s] = 1
  while (stack.length) {
    const u = stack.pop()!
    for (const [v] of g.adj[u]) {
      if (mine[v] && !keep[v]) {
        keep[v] = 1
        stack.push(v)
      }
    }
  }
  for (let i = 0; i < n; i++) out[i] = keep[of[i]]
  return out
}
