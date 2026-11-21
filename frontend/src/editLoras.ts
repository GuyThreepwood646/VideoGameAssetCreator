// Your LoRAs for the picture-edit uses (backend stages/picture_loras.py; the model id is reference.EDIT_MODEL).
// The types live in api.ts; the helpers are here.
import type { EditChoice, EditLora, MadeFor, StartOn, Version } from './api'
import { STRENGTH_LABELS } from './help'

export type { EditChoice, EditListing, EditLora } from './api'

export const EDIT_MODEL = 'qwen_edit'
export const EDIT_MODEL_LABEL = 'Picture edits (Qwen-Image-Edit)'

// The four uses; each has its own choice, so a LoRA picked for one never reaches another.
export const EDIT_KNOBS = ['edit_addons', 'view_addons', 'pose_addons', 'face_addons'] as const
export type EditKnob = (typeof EDIT_KNOBS)[number]
export const EDIT_KNOB_LABELS: Record<EditKnob, string> = {
  edit_addons: 'Refine or combined photos',
  view_addons: 'Side views',
  pose_addons: 'Pose redraw',
  face_addons: 'Face close-up',
}

// A picture-edit step takes at most this many of your LoRAs at once (reference.MAX_EDIT_LORAS on the backend).
export const EDIT_LORA_LIMIT = 2

// The schema's knob type for these (api.ts AdjustParam does not list it yet, so the check is by text).
export function isEditKnob(type: string): boolean {
  return type === 'edit_addons'
}

// Which of your LoRAs start on for this kind of asset in one step setting (backend lora_defaults.defaults_for): a painter
// LoRA in the step 1 list ('addons'), a picture-edit LoRA in the uses it names. A picture-edit use takes at most two.
export function loraDefaults(entries: { id: string; stage?: string; default_strength?: string; start_on?: { categories: string[]; uses: string[] } }[] | null | undefined, knob: string, category: string): Record<string, EditChoice> {
  const out: Record<string, EditChoice> = {}
  for (const e of entries ?? []) {
    const so = e.start_on
    if (!so || !so.categories.includes(category)) continue
    const uses = (e.stage ?? 'concept') === 'concept' ? ['addons'] : so.uses
    if (!uses.includes(knob)) continue
    out[e.id] = (e.default_strength as EditChoice | undefined) ?? 'normal'
  }
  return (EDIT_KNOBS as readonly string[]).includes(knob) ? Object.fromEntries(Object.entries(out).slice(0, EDIT_LORA_LIMIT)) : out
}

// What picture_loras.edit_loras records per use, in the version's metrics.
type UseRecord = { applied?: { id: string; label?: string; strength?: string }[]; ignored?: { id?: string; reason?: string }[]; unknown?: unknown[] }

// Your LoRAs for picture edits from the listing (the stage is set by the backend: "edit").
export function editLoras(list: { addons: unknown[] } | null | undefined): EditLora[] {
  return (list?.addons ?? []).map((a) => a as EditLora).filter((a) => a.stage === 'edit')
}

// Plain sentences for the picture-edit LoRAs a version used, per use: "Side views: Ink style (Normal)." Empty when none.
export function editLoraApplied(v: Version | null | undefined): string[] {
  if (!v) return []
  const out: string[] = []
  for (const k of EDIT_KNOBS) {
    const rec = v.metrics?.[k] as UseRecord | undefined
    const applied = rec && Array.isArray(rec.applied) ? rec.applied : []
    if (!applied.length) continue
    const names = applied.map((a) => `${a.label ?? 'your LoRA'} (${a.strength ?? ''})`)
    out.push(`${EDIT_KNOB_LABELS[k]}: ${names.join(', ')}.`)
  }
  return out
}

// Plain sentences for the LoRAs a version could not use, per use: a chosen LoRA the ComfyUI folder no longer has
// (ignored), or one the list no longer knows (unknown). Empty when every chosen LoRA was used.
export function editLoraNotes(v: Version | null | undefined): string[] {
  if (!v) return []
  const out: string[] = []
  for (const k of EDIT_KNOBS) {
    const rec = v.metrics?.[k] as UseRecord | undefined
    if (!rec || typeof rec !== 'object') continue
    const reasons = [...new Set((rec.ignored ?? []).map((i) => i.reason || 'it could not be used'))]
    if (reasons.length) out.push(`${EDIT_KNOB_LABELS[k]}: a LoRA you chose was not used (${reasons.join('; ')}).`)
    const unknown = rec.unknown ?? []
    if (unknown.length) out.push(`${EDIT_KNOB_LABELS[k]}: a LoRA you chose is not on your list any more, so it was not used.`)
  }
  return out
}

// True for one of your LoRAs that is for picture edits (its stage from the listing is "edit").
export function isEditLora(x: unknown): boolean {
  return (x as { stage?: unknown } | null | undefined)?.stage === 'edit'
}

// What a picture-edit LoRA was made for (user_addons.MADE_FOR_EDIT / MADE_FOR_PLAIN). Plain also covers "not sure".
export const MADE_FOR_EDIT = 'qwen_image_edit'
export const MADE_FOR_PLAIN = 'qwen_image'
export const PLAIN_WARNING =
  'This LoRA was made for Qwen-Image, a close relative of the model the app uses for picture edits. It may only partly work, or change results in ways you do not expect. Try it at Subtle first.'

// The "made for" the file itself settled, from the listing or the upload's prefill ("" when the file cannot tell).
export function madeForOf(x: unknown): MadeFor | '' {
  const m = (x as { made_for?: unknown } | null | undefined)?.made_for
  return m === MADE_FOR_EDIT || m === MADE_FOR_PLAIN ? m : ''
}

// A LoRA entry's warning, when the backend gives one (a LoRA made for plain Qwen-Image).
export function warningOf(x: unknown): string {
  const w = (x as { warning?: unknown } | null | undefined)?.warning
  return typeof w === 'string' ? w : ''
}

// "On for this run: Ink look (Normal, on by default for Humanoid assets), Soft light (Strong)." or the sentence for none.
// `byDefault`: the LoRAs on because of their default, not chosen on this asset.
export function onForThisRun(entries: EditLora[], choices: Record<string, EditChoice>, byDefault: string[] = [], kindWords = 'this kind of asset'): string {
  const on = entries
    .filter((e) => (choices[e.id] ?? 'off') !== 'off')
    .map((e) => `${e.label} (${STRENGTH_LABELS[choices[e.id] as Exclude<EditChoice, 'off'>]}${byDefault.includes(e.id) ? `, on by default for ${kindWords}` : ''})`)
  return on.length ? `On for this run: ${on.join(', ')}.` : 'None of your LoRAs are on for this run.'
}

// What the three strengths change, in one line under each step's LoRA list.
export const STRENGTH_WORDS = "Subtle, Normal and Strong set how strongly the LoRA's look is applied: Subtle is a light touch, Strong the most."

// The sentence when a step already has as many of your LoRAs on as it takes.
export const LIMIT_REACHED = `A step can have at most ${EDIT_LORA_LIMIT} of your LoRAs on. Turn one off to switch another on.`

// The kinds of assets a LoRA starts on for, in the words the app uses for them.
function kinds(ids: string[], labels: Map<string, string>): string {
  return ids.map((c) => labels.get(c) ?? c).join(', ')
}

// The one plain line under a LoRA in Your library.
export function startOnSentence(a: { stage?: string; start_on?: StartOn }, labels: Map<string, string>): string {
  const so = a.start_on
  if (!so || so.categories.length === 0) return 'Starts off everywhere: switch it on in a step when you want it.'
  const where = kinds(so.categories, labels)
  if (!isEditLora(a)) return `Starts on for the first picture of: ${where}.`
  const steps = EDIT_KNOBS.filter((k) => so.uses.includes(k)).map((k) => EDIT_KNOB_LABELS[k])
  return `Starts on for: ${where}; in ${steps.join(', ')}.`
}

