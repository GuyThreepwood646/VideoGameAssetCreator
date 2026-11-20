import type { BodyMarks, MarkMove, MarkView } from '../../api'

// Undo and redo for the body marks. A step is the marks as they were, with who placed each one: the app or the person.
// Putting a mark back the way it was needs the existing calls: a mark the app placed comes back through the reset call
// (the person's move is dropped, so it is the app's again); a mark the person placed comes back through the marks PUT
// (a move, which makes it the person's). A step's changes go out together, so the person sees one undo.

export type MarkSpot = { x: number; y: number; source: 'app' | 'you' } | null
export type MarkSnapshot = Record<string, MarkSpot>

const sep = '|'

export function markKey(view: MarkView, joint: string): string {
  return `${view}${sep}${joint}`
}

// Where every mark is now and who placed it (null where there is no place: a mark the app could not see and nobody placed).
export function snapshotMarks(d: BodyMarks): MarkSnapshot {
  const out: MarkSnapshot = {}
  for (const [view, joints] of Object.entries(d.marks)) {
    for (const [joint, m] of Object.entries(joints ?? {})) {
      out[markKey(view as MarkView, joint)] = m.x === null || m.y === null ? null : { x: m.x, y: m.y, source: m.source }
    }
  }
  return out
}

function same(a: MarkSpot, b: MarkSpot): boolean {
  if (!a || !b) return a === b
  return a.x === b.x && a.y === b.y && a.source === b.source
}

// What has to change, now, to make the marks match the target: the person's marks by move, the app's by reset.
export function restorePlan(
  current: MarkSnapshot,
  target: MarkSnapshot,
): { moves: MarkMove[]; resets: { view: MarkView; joint: string }[] } {
  const moves: MarkMove[] = []
  const resets: { view: MarkView; joint: string }[] = []
  for (const [key, want] of Object.entries(target)) {
    const [view, joint] = key.split(sep) as [MarkView, string]
    const now = current[key] ?? null
    if (same(now, want)) continue
    if (want === null) {
      // The app's mark on a picture it could not see: put it back there (the reset takes the person's place off).
      resets.push({ view, joint })
    } else if (want.source === 'app') {
      resets.push({ view, joint })
    } else {
      moves.push({ view, joint, x: want.x, y: want.y })
    }
  }
  return { moves, resets }
}
