// Declared parts on the rig page (WP-20 step 5): where a hanging part hangs, marked by clicking the model with the
// viewer's pointer tool (viewerTool.ts). Pure helpers here; the hook and panel are in pipeline/views/DeclaredPoints.tsx.
import type { PlacedPart } from './api'
import type { SurfaceHit, ViewerTool } from './viewerTool'

export type Point = [number, number, number]

const round4 = (v: number) => Math.round(v * 10000) / 10000

// The spot in the rig GLB's own frame (metres, y up). The hit is in scene space, where the viewer frames the model and
// the turntable turns it; the hit mesh's own inverse world matrix takes those out and leaves the geometry as the file
// stores it (the rest pose of a skinned mesh).
export function restPoint(hit: SurfaceHit): Point {
  const p = hit.mesh.worldToLocal(hit.point.clone())
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

// The parts that take points: sway (the spring pass reads one point) and stiff (the worn pass reads a list).
export function markable(parts: PlacedPart[]): PlacedPart[] {
  return parts.filter((p) => p.kind === 'sway' || p.kind === 'stiff')
}
