// Hair notes for the asset form (docs/HAIR-RESEARCH.md 5.4): a copy of the word lists and the notes in
// backend/vgac/hairpic.py, which is the source; backend/tests/test_hair_pictures.py checks the two stay the same.
// The notes say what the app does today; they never promise hair that moves.

export const HAIR_WORDS = [
  'hair', 'haired', 'hairstyle', 'haircut', 'ponytail', 'pony tail', 'braid', 'braids', 'braided', 'plait', 'plaits',
  'plaited', 'bun', 'topknot', 'top knot', 'fringe', 'bangs', 'mohawk', 'mohican', 'mullet', 'dreadlocks', 'dreads',
  'locs', 'cornrows', 'afro', 'curls', 'ringlets', 'pigtails', 'bob', 'crew cut', 'buzz cut', 'quiff', 'pompadour',
  'undercut', 'locks', 'tresses',
]
export const BALD_WORDS = ['bald', 'shaved head', 'shaven head', 'hairless', 'no hair', 'bald headed', 'clean shaven head']
export const COVER_WORDS = ['helmet', 'helm', 'helmeted', 'hood', 'hooded', 'cowl', 'hijab', 'headscarf', 'head scarf', 'turban',
  'balaclava', 'great helm', 'full helm']
export const COVER_NEGATORS = ['no', 'without', 'not', 'removed', 'off', 'bare', 'carrying', 'holding']
export const COVER_AFTER = ['down', 'lowered', 'off', 'under']
export const NO_HAIR_BODIES = ['robot', 'android', 'skeleton', 'skeletal', 'golem', 'automaton', 'mech', 'droid', 'mummy']
export const LONG_WORDS = ['long', 'flowing', 'loose', 'cascading', 'untied', 'waist length', 'hip length', 'shoulder length',
  'knee length', 'free flowing', 'lank']
export const LONG_NOUNS = ['hair', 'locks', 'tresses', 'mane']
export const LONG_AFTER = ['down', 'loose', 'flowing', 'falls', 'falling', 'cascading', 'cascades', 'reaching', 'hanging', 'spilling']
export const TIED_WORDS = ['ponytail', 'pony tail', 'braid', 'braids', 'braided', 'plait', 'plaits', 'plaited', 'bun', 'tied',
  'topknot', 'top knot', 'updo', 'pigtails', 'tied back']
export const FUR_WORDS = ['fur', 'furry', 'furred', 'fluffy', 'mane', 'shaggy', 'woolly', 'wooly', 'hairy', 'pelt', 'fleece']
export const NO_FUR_WORDS = ['scales', 'scaly', 'scaled', 'reptile', 'lizard', 'crocodile', 'alligator', 'turtle', 'tortoise',
  'dragon', 'dinosaur', 'frog', 'toad', 'salamander', 'newt', 'robot', 'robotic', 'mechanical', 'metal',
  'stone', 'skeleton', 'skeletal', 'undead bones', 'hairless', 'slime', 'insect', 'beetle', 'armadillo',
  'rhino', 'rhinoceros', 'elephant', 'hippo', 'hippopotamus', 'chitin', 'golem', 'crystal', 'bony']

export const HAIR_NOTES: Record<string, string> = {
  photoreal_hair:
    'Hair is built as one solid shape with its colour and strands painted on from the picture. It looks right from a few steps away and simpler up close. Short, tied-back or braided hair comes out best; long loose hair and wispy fringes come out worst.',
  long_loose:
    'Long loose hair will be joined to the shoulders in the 3D model and will not swing. A ponytail or braid that hangs clear of the neck can move. Hair that is tied back, braided or cut short also keeps a cleaner shape.',
  fur: 'Fur is painted onto the surface of the model, not made of separate hairs. It reads as fur at game distance and looks smoother up close.',
}

// Which kind of hair each category has (its `hair` key in backend/vgac/data/categories).
export const CATEGORY_HAIR: Record<string, 'head' | 'fur' | 'fur_if_named'> = {
  humanoid: 'head',
  quadruped: 'fur',
  winged: 'fur_if_named',
  multileg: 'fur_if_named',
  serpent: 'fur_if_named',
}

function norm(text: string): string {
  return ' ' + (text || '').toLowerCase().replace(/[^a-z]+/g, ' ').trim().split(/\s+/).filter(Boolean).join(' ') + ' '
}

const has = (t: string, phrase: string) => t.includes(' ' + phrase + ' ')
const anyOf = (t: string, phrases: string[]) => phrases.some((p) => has(t, p))

function find(words: string[], phrase: string): number[] {
  const p = phrase.split(' ')
  const out: number[] = []
  for (let i = 0; i + p.length <= words.length; i++) if (p.every((w, k) => words[i + k] === w)) out.push(i)
  return out
}

function covered(t: string): boolean {
  const words = t.trim().split(' ')
  for (const c of COVER_WORDS) {
    const n = c.split(' ').length
    for (const i of find(words, c)) {
      const before = words.slice(Math.max(0, i - 3), i)
      const after = words.slice(i + n, i + n + 1)
      if (before.some((w) => COVER_NEGATORS.includes(w)) || after.some((w) => COVER_AFTER.includes(w))) continue
      return true
    }
  }
  return false
}

export function longLoose(t: string): boolean {
  if (anyOf(t, TIED_WORDS)) return false
  const words = t.trim().split(' ')
  for (const noun of LONG_NOUNS) {
    for (const i of find(words, noun)) {
      const before = ' ' + words.slice(Math.max(0, i - 3), i).join(' ') + ' '
      if (LONG_WORDS.some((w) => has(before, w))) return true
      if (words.slice(i + 1, i + 4).some((w) => LONG_AFTER.includes(w))) return true
    }
  }
  return false
}

export function hairKind(text: string, category: string): 'head' | 'fur' | null {
  const kind = CATEGORY_HAIR[category]
  const t = norm(text)
  if (kind === 'head') {
    if (anyOf(t, BALD_WORDS)) return null
    if (!anyOf(t, HAIR_WORDS) && (covered(t) || anyOf(t, NO_HAIR_BODIES))) return null
    return 'head'
  }
  if (kind === 'fur' || kind === 'fur_if_named') {
    if (anyOf(t, NO_FUR_WORDS) && !anyOf(t, FUR_WORDS)) return null
    if (kind === 'fur_if_named' && !anyOf(t, FUR_WORDS)) return null
    return 'fur'
  }
  return null
}

// The notes for a description, a style id and a category id, in order: [{id, text}].
export function hairNotes(text: string, style: string, category: string): { id: string; text: string }[] {
  if (!text.trim()) return []
  const t = norm(text)
  const kind = hairKind(text, category)
  const ids: string[] = []
  if (kind === 'head' && style === 'realistic_pbr' && anyOf(t, HAIR_WORDS)) ids.push('photoreal_hair')
  if (kind === 'head' && longLoose(t)) ids.push('long_loose')
  if (kind === 'fur') ids.push('fur')
  return ids.map((id) => ({ id, text: HAIR_NOTES[id] }))
}
