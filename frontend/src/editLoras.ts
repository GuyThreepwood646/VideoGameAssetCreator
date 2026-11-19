// Your LoRAs for the picture-edit uses (backend stages/picture_loras.py; the model id is reference.EDIT_MODEL).
// Kept in their own file until the shared API types (api.ts) take them.
import type { Version } from './api'

export const EDIT_MODEL = 'qwen_edit'
export const EDIT_MODEL_LABEL = 'Picture edits (Qwen-Image-Edit)'

// The four uses; each has its own choice, so a LoRA picked for one never reaches another.
export const EDIT_KNOBS = ['edit_addons', 'view_addons', 'pose_addons', 'face_addons'] as const
export type EditKnob = (typeof EDIT_KNOBS)[number]
export const EDIT_KNOB_LABELS: Record<EditKnob, string> = {
  edit_addons: 'Refine',
  view_addons: 'Side views',
  pose_addons: 'Pose redraw',
  face_addons: 'Face close-up',
}

export type EditChoice = 'off' | 'subtle' | 'normal' | 'strong'

// A picture-edit step takes at most this many of your LoRAs at once (reference.MAX_EDIT_LORAS on the backend).
export const EDIT_LORA_LIMIT = 2

// The schema's knob type for these (api.ts AdjustParam does not list it yet, so the check is by text).
export function isEditKnob(type: string): boolean {
  return type === 'edit_addons'
}

// One of your own LoRAs as GET /api/my-addons lists it: its stage ("edit" for picture edits) and its warning.
export type EditLora = { id: string; label: string; stage?: string; warning?: string; mine?: boolean; file_name?: string }

// The listing with the picture-edit model the backend adds (user_addons.listing: edit_models).
export type EditListing = { addons: EditLora[]; edit_models?: { id: string; label: string }[] }

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
export function madeForOf(x: unknown): string {
  const m = (x as { made_for?: unknown } | null | undefined)?.made_for
  return m === MADE_FOR_EDIT || m === MADE_FOR_PLAIN ? m : ''
}

// A LoRA entry's warning, when the backend gives one (a LoRA made for plain Qwen-Image).
export function warningOf(x: unknown): string {
  const w = (x as { warning?: unknown } | null | undefined)?.warning
  return typeof w === 'string' ? w : ''
}
