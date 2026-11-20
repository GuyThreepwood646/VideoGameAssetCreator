// The suggested worn item on the rig page (WP-69 follow-up): the finder proposes where a kept back-worn item sits, and
// the person keeps it, adjusts it with points, or dismisses it. Pure helpers here; the calls are in api.ts and the
// panel in pipeline/views/DeclaredPoints.tsx. Nothing is applied silently.
import type { ItemSuggestionState, SuggestionAnswer } from './api'
import type { Point } from './declaredPoints'

// Whether the page should ask the server to start a suggestion: a back-worn item is kept, the finder is installed,
// and no suggestion exists yet for this rig version (a dismissed or kept one is never run again).
export function shouldStart(a: SuggestionAnswer | null): boolean {
  return !!a && a.installed && a.eligible.length > 0 && a.state === null
}

// True while the server is still making the suggestion (the page asks again after a short wait).
export function isRunning(a: SuggestionAnswer | null): boolean {
  return !!a && a.state?.state === 'running'
}

// The suggestion to show as pending: found, not yet kept or dismissed, with points.
export function pendingSuggestion(a: SuggestionAnswer | null): ItemSuggestionState | null {
  const s = a?.state
  if (!s || s.state !== 'done' || !s.found || !s.points?.length || !s.item) return null
  return s
}

// The plain line the person reads, naming the item.
export function suggestionLine(s: ItemSuggestionState): string {
  const label = s.label || s.item || 'item'
  return `Suggested from the description: ${label}. Keep it, adjust it with points, or dismiss it.`
}

// The note for a region that looks large: it may take in hair or a cape.
export function suggestionNote(s: ItemSuggestionState): string | null {
  return s.large ? 'This may include hair or a cape; check it before keeping it.' : null
}

// The stored points as the rig's pending points (the same glTF frame the points are kept in).
export function suggestionPoints(s: ItemSuggestionState): Point[] {
  return (s.points ?? []).filter((p) => p.length === 3).map((p) => [p[0], p[1], p[2]] as Point)
}
