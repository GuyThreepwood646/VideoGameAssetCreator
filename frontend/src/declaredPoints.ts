// Declared parts on the rig page (WP-20 step 5): where a hanging part hangs, marked by clicking the model with the
// viewer's pointer tool (viewerTool.ts). Pure helpers here; the hook and panel are in pipeline/views/DeclaredPoints.tsx.
import type { HingeSide, PlacedPart } from './api'
import type { SurfaceHit, ViewerTool } from './viewerTool'

export type Point = [number, number, number]

const round4 = (v: number) => Math.round(v * 10000) / 10000

// The spot in the rig GLB's own frame (metres, y up). The hit is in scene space, where the viewer frames the model and
// the turntable turns it; the hit mesh's own inverse world matrix takes those out and leaves the geometry as the file
// stores it (the rest pose of a skinned mesh).
// A piece already cut free (WP-29) hangs under its moving node and pivot node: the spot goes through the piece's own
// matrix and the pivot's (the moving node counts as at rest), so it lands in the model's frame even while a clip plays.
export function restPoint(hit: SurfaceHit): Point {
  const p = hit.mesh.worldToLocal(hit.point.clone())
  const mover = hit.mesh.parent
  const pivot = mover?.parent
  if (!(hit.mesh as { isSkinnedMesh?: boolean }).isSkinnedMesh && mover && pivot && pivot.name === `${mover.name}_Pivot`) {
    p.applyMatrix4(hit.mesh.matrix).applyMatrix4(pivot.matrix)
  }
  return [round4(p.x), round4(p.y), round4(p.z)]
}

// A tool that hands the spot under a left click to onPick. Off the model (hit null) it does nothing. Escape leaves the
// tool through the viewer's onToolExit, so the owner clears it there.
export function pointTool(onPick: (p: Point) => void): ViewerTool {
  return {
    id: 'declared-point',
    cursor: 'crosshair',
    down: (hit) => {
      if (hit) onPick(restPoint(hit))
    },
  }
}

// The version's parts with one part's point replaced (or cleared). Names match exactly, as the API returned them.
export function withPoint(parts: PlacedPart[], name: string, point: Point | null): PlacedPart[] {
  return parts.map((p) => (p.name === name ? { ...p, location: point ? [...point] : null } : p))
}

// A stiff part takes up to this many points; each grows its own piece and the pieces join (WP-69).
export const MAX_STIFF_POINTS = 8

// The version's parts with one stiff part's point added to its list (capped at MAX_STIFF_POINTS).
export function withStiffPoint(parts: PlacedPart[], name: string, point: Point): PlacedPart[] {
  return parts.map((p) => {
    if (p.name !== name) return p
    const list = [...(p.locations ?? [])]
    if (list.length < MAX_STIFF_POINTS) list.push([...point])
    return { ...p, locations: list }
  })
}

// The version's parts with one point removed from a stiff part's list (by its position in the list).
export function withoutStiffPoint(parts: PlacedPart[], name: string, index: number): PlacedPart[] {
  return parts.map((p) => (p.name === name ? { ...p, locations: (p.locations ?? []).filter((_, i) => i !== index) } : p))
}

// The version's parts with one stiff part's points all cleared.
export function withoutStiffPoints(parts: PlacedPart[], name: string): PlacedPart[] {
  return parts.map((p) => (p.name === name ? { ...p, locations: [] } : p))
}

// Parts that move on an object with no skeleton (WP-29): a hinged lid or door, a spinning wheel, a sliding drawer.
export const MOVING_KINDS = ['hinge', 'spin', 'slide'] as const
export const isMoving = (kind: string) => (MOVING_KINDS as readonly string[]).includes(kind)
// Parts that take a list of points (one click per piece: the four wheels of a cart are four points).
export const isMultiPoint = (kind: string) => kind === 'stiff' || isMoving(kind)

// The plain words the rig page shows for each kind of part.
export const KIND_WORDS: Record<string, string> = {
  sway: 'Hanging part',
  stiff: 'Stiff part',
  hinge: 'Hinged lid or door',
  spin: 'Spinning wheel',
  slide: 'Sliding drawer',
}

// The hinge sides the rig page offers, in the backend's words (declared_parts.SIDES), with what the person reads.
export const HINGE_SIDES: { value: HingeSide; label: string }[] = [
  { value: 'auto', label: 'Found for you' },
  { value: 'back', label: 'Back edge' },
  { value: 'front', label: 'Front edge' },
  { value: 'left', label: 'Left edge' },
  { value: 'right', label: 'Right edge' },
  { value: 'top', label: 'Top edge' },
  { value: 'bottom', label: 'Bottom edge' },
]
export const ANGLE_RANGE: [number, number] = [5, 180] // degrees (declared_parts.ANGLE_RANGE)
export const DISTANCE_RANGE: [number, number] = [0.1, 1.0] // share of the depth behind it (declared_parts.DISTANCE_RANGE)

export type Motion = { side?: HingeSide; open_at_rest?: boolean; angle?: number; distance?: number }

// The version's parts with one moving part's settings changed (angle and distance kept inside their ranges).
export function withMotion(parts: PlacedPart[], name: string, motion: Motion): PlacedPart[] {
  const clamp = (v: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, v))
  const m: Motion = { ...motion }
  if (m.angle !== undefined) m.angle = clamp(Math.round(m.angle), ANGLE_RANGE)
  if (m.distance !== undefined) m.distance = clamp(Math.round(m.distance * 100) / 100, DISTANCE_RANGE)
  return parts.map((p) => (p.name === name ? { ...p, ...m } : p))
}

// The parts that take points: sway (the spring pass reads one point), stiff (the worn pass reads a list) and the moving
// parts of an object with no skeleton (the rig step cuts each piece free and gives it its clips).
export function markable(parts: PlacedPart[]): PlacedPart[] {
  return parts.filter((p) => p.kind === 'sway' || isMultiPoint(p.kind))
}
