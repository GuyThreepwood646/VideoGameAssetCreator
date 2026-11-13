// Wording and small calculations for the guided "get this pack" panel (PackInstall.tsx; backend/vgac/packs.py).
// Kept apart from the component so the words can be tested without a browser.
import type { PackChoice, PackKit, PackSource } from './api'

export function fmtSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(1) + ' GB'
  return Math.max(1, Math.round(bytes / 1024 ** 2)) + ' MB'
}

export type KitText = {
  title: string
  button: string
  what: string
  who: string
  keeps: string
  time: string
  where: string
  doneWord: string
}

export const KIT_TEXT: Record<string, KitText> = {
  hair: {
    title: 'Get the hair library',
    button: 'Get the hair library',
    what: 'The hair library is 26 ready-made hairstyles you can fit to a character. They are free and in the public domain (CC0), so you can use them in any game.',
    who: 'They come from three free packs made by Quaternius, the MakeHuman team and MakeHuman community artists. The app downloads the packs for you, then builds the hairstyles on this computer with Blender.',
    keeps: 'about 1 GB of free space while it builds, and 16 MB kept afterwards',
    time: 'A few minutes, mostly the download.',
    where: 'Hairstyles appear in the hair panel of every character as soon as it is done. Nothing needs restarting.',
    doneWord: 'hairstyles',
  },
}

export function kitText(kit: string): KitText {
  return KIT_TEXT[kit] ?? { title: 'Get this pack', button: 'Get this pack', what: '', who: '', keeps: '', time: '', where: '', doneWord: 'files' }
}

// The sources the chosen packs need, with the ones those need, each once.
export function neededSources(kit: PackKit, chosen: string[]): PackSource[] {
  const ids: string[] = []
  const byId = new Map(kit.sources.map((s) => [s.id, s]))
  for (const c of kit.choices) {
    if (!chosen.includes(c.id)) continue
    for (const sid of c.sources) {
      for (const n of [...(byId.get(sid)?.needs ?? []), sid]) if (!ids.includes(n)) ids.push(n)
    }
  }
  return ids.map((i) => byId.get(i)).filter((s): s is PackSource => !!s)
}

// What is still to be downloaded for the choice (zips not yet on disk and not yet unpacked).
export function downloadBytes(kit: PackKit, chosen: string[]): number {
  return neededSources(kit, chosen).filter((s) => s.state === 'missing').reduce((n, s) => n + s.size, 0)
}

export function dependencyNote(c: PackChoice): string {
  return c.requires.length ? `Also needs ${c.requires.join(' and ')}, which the app fetches with it.` : ''
}

// One sentence on how each needed file arrives: by the app or by the person.
export function arrivalNote(sources: PackSource[]): string {
  const auto = sources.filter((s) => s.direct && s.state === 'missing')
  const hand = sources.filter((s) => !s.direct && s.state === 'missing')
  const parts: string[] = []
  if (auto.length) parts.push(`The app downloads ${auto.map((s) => s.name).join(' and ')} (${fmtSize(auto.reduce((n, s) => n + s.size, 0))}).`)
  if (hand.length) parts.push(`${hand.map((s) => s.name).join(' and ')} (${fmtSize(hand.reduce((n, s) => n + s.size, 0))}) is on itch.io, which only hands a file over after a click, so you download that one yourself and give it to the app here.`)
  return parts.join(' ')
}

export function canStart(kit: PackKit, chosen: string[]): boolean {
  return chosen.length > 0 && kit.run.state !== 'working'
}

export function enoughSpace(kit: PackKit, chosen: string[]): boolean {
  // The same rule as the backend: what is left to download plus about 1.15 times each unpacked pack, plus a margin.
  const need = neededSources(kit, chosen).filter((s) => s.state !== 'unpacked').reduce((n, s) => n + (s.state === 'missing' ? s.size : 0) + s.size * 1.15, 0) + 400 * 1024 ** 2
  return kit.free_bytes === 0 || kit.free_bytes >= need
}

export function progressWords(kit: PackKit): string {
  const r = kit.run
  if (r.phase === 'download' && r.bytes_total > 0) return `${r.text}: ${fmtSize(r.bytes_done)} of ${fmtSize(r.bytes_total)}`
  return r.text
}
