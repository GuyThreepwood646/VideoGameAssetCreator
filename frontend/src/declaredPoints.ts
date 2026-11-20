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

// Only sway parts take a point (the rig's spring pass reads them); other kinds are listed by the API but not marked.
export function markable(parts: PlacedPart[]): PlacedPart[] {
  return parts.filter((p) => p.kind === 'sway')
}
