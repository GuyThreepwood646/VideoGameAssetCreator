// Which graphics cards a job used, in plain words, from the job's record (backend/vgac/cardlog.py): the "cards" list
// (own, lent and drawn cards, with the kind of work, the step and the times) and the "moved" list. Pure functions, so
// the job log can show them and a test can check the words.

const STAGE_WORDS: Record<string, string> = {
  concept: 'picture',
  turnaround: 'side views',
  mesh: 'build',
  prep: 'clean-up',
  rig: 'rig',
  export: 'export',
  material: 'material',
}

type CardEntry = {
  card: string
  role: string
  kind?: string
  step?: string
  started_at?: string | null
  ended_at?: string | null
  seconds?: number
}

type MoveEntry = { from: string; to: string; why?: string }

/** A duration in plain words: "45 s", "12 min", "7 min 30 s", "1 h 5 min". */
export function durationText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  const mins = Math.floor(s / 60)
  if (mins < 60) {
    const rest = s % 60
    return rest ? `${mins} min ${rest} s` : `${mins} min`
  }
  return `${Math.floor(mins / 60)} h ${mins % 60} min`
}

function entrySeconds(c: CardEntry, now: number): number {
  if (typeof c.seconds === 'number') return c.seconds
  if (!c.started_at) return 0
  const end = c.ended_at ? new Date(c.ended_at).getTime() : now
  return Math.max(0, (end - new Date(c.started_at).getTime()) / 1000)
}

function joinWords(parts: string[]): string {
  if (parts.length <= 1) return parts.join('')
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/** The sentences for one job: what each card was used for (with the time), what it was planned on, and any moves. */
export function cardUseLines(result: unknown, now: number = Date.now()): string[] {
  if (!result || typeof result !== 'object') return []
  const r = result as { cards?: unknown; moved?: unknown }
  const cards = Array.isArray(r.cards) ? (r.cards as CardEntry[]) : []
  const moved = Array.isArray(r.moved) ? (r.moved as MoveEntry[]) : []
  const lines: string[] = []

  const used = cards.filter((c) => c.role === 'own' || c.role === 'lent' || c.role === 'drew')
  const byCard = new Map<string, { seconds: number; labels: string[] }>()
  for (const c of used) {
    const stage = (c.kind ?? '').split('.')[c.kind?.startsWith('material.') ? 1 : 0] ?? ''
    const label = c.role === 'own' ? STAGE_WORDS[stage] ?? stage : c.step ?? ''
    const cur = byCard.get(c.card) ?? { seconds: 0, labels: [] }
    cur.seconds += entrySeconds(c, now)
    if (label && !cur.labels.includes(label)) cur.labels.push(label)
    byCard.set(c.card, cur)
  }
  const parts: string[] = []
  for (const [card, v] of byCard) {
    const who = card === 'host' ? 'this PC' : `graphics card ${card}`
    const tail = v.labels.length ? ` (${v.labels.join(', ')})` : ''
    parts.push(`${who} for ${durationText(v.seconds)}${tail}`)
  }
  if (parts.length) lines.push(`Used ${joinWords(parts)}.`)

  const planned = cards.find((c) => c.role === 'planned')
  if (planned && !used.length) lines.push(`Planned on graphics card ${planned.card}.`)

  for (const m of moved) {
    const why = m.why ? ` (${m.why})` : ''
    lines.push(`Moved from graphics card ${m.from} to graphics card ${m.to}${why}.`)
  }
  return lines
}
