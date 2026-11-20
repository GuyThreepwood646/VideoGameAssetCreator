import type { BodyMarks, MarkMove, MarkView } from '../../api'

// Undo and redo for the body marks: a step is the marks as they were (a snapshot), and putting one back means the
// minimum calls to the existing marks endpoints: one PUT for the moves and one reset per mark that has to go back to
// the app's place.

export type MarkSpot = { x: number; y: number } | null
export type MarkSnapshot = Record<string, MarkSpot>

const sep = '|'

export function markKey(view: MarkView, joint: string): string {
  return `${view}${sep}${joint}`
}

// Where every mark is now (null where the app could not see it and nobody placed it).
export function snapshotMarks(d: BodyMarks): MarkSnapshot {
  const out: MarkSnapshot = {}
  for (const [view, joints] of Object.entries(d.marks)) {
    for (const [joint, m] of Object.entries(joints ?? {})) {
      out[markKey(view as MarkView, joint)] = m.x === null || m.y === null ? null : { x: m.x, y: m.y }
    }
  }
  return out
}

// What has to change, now, to make the marks match the target.
export function restorePlan(current: MarkSnapshot, target: MarkSnapshot): { moves: MarkMove[]; resets: { view: MarkView; joint: string }[] } {
  const moves: MarkMove[] = []
  const resets: { view: MarkView; joint: string }[] = []
  for (const [key, want] of Object.entries(target)) {
    const [view, joint] = key.split(sep) as [MarkView, string]
    const now = current[key] ?? null
    if (want) {
      if (!now || now.x !== want.x || now.y !== want.y) moves.push({ view, joint, x: want.x, y: want.y })
    } else if (now) {
      resets.push({ view, joint })
    }
  }
  return { moves, resets }
}
