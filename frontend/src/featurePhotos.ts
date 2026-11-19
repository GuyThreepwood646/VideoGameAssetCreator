// Feature photos in the New asset form (components/FeaturePhotos.tsx): which roles each Start from offers, which slots
// are sent, the person box round the main person in a photo, and the sentences that say what they will do. The backend
// is vgac/compose.py (feature photos) and vgac/people.py (the people in a photo, POST /api/people).
import type { FeatureRole, PeopleResult, PersonBox } from './api'
import type { PickedImage } from './components/ImageDrop'

// The kinds of asset the combined picture is drawn for (characters and creatures).
export const FEATURE_CATEGORIES = ['humanoid', 'quadruped', 'multileg', 'winged', 'serpent']
export const MAX_FEATURES = 6
// The smallest side of a person box, in picture pixels (people.py MIN_SIDE); the whole picture side when smaller.
export const MIN_BOX = 64

export type FeatureSlot = {
  key: number
  role: FeatureRole
  picture: PickedImage | null
  note: string
  // The people found in the picture: undefined = not looked at yet, 'pending' = looking, null = not looked at (no detector).
  people?: PeopleResult | 'pending' | null
  // The box round the main person (suggested, then edited). Used unless `whole` is set.
  box?: PersonBox | null
  // "Use the whole photo" was chosen.
  whole?: boolean
}
export type StartFrom = 'words' | 'picture' | 'model'

let nextKey = 1
export const slot = (role: FeatureRole, picture: PickedImage | null = null): FeatureSlot => ({ key: nextKey++, role, picture, note: '' })

/** Whether the section is shown, and which roles it offers, for this start and kind of asset. */
export function featureRoles(startFrom: StartFrom, category: string): FeatureRole[] {
  if (startFrom === 'model') return category === 'humanoid' ? ['face'] : []
  return FEATURE_CATEGORIES.includes(category) ? ['face', 'body', 'outfit', 'other'] : []
}

/** The slots that count for this start: filled, and of a role it offers. */
export function usedFeatures(slots: FeatureSlot[], startFrom: StartFrom, category: string): FeatureSlot[] {
  const roles = featureRoles(startFrom, category)
  return slots.filter((s) => s.picture && roles.includes(s.role))
}

// "Crop the face from it" only where a face is expected: a body photo of a human shows the whole person, so its face
// can be cut out. An outfit or other photo shows clothes or details, not a face, so it does not get the button.
export const cropsFace = (role: FeatureRole, category: string) => role === 'body' && category === 'humanoid'

// The people in a photo are looked for, and a box suggested, for every photo of a human except the Face picture (which
// keeps its own crop). Non-human subjects get no people check and no people warning.
export const looksForPeople = (role: FeatureRole, category: string) => role !== 'face' && category === 'humanoid'

/** The people found in this slot's photo, or none (not looked at, pending, or the detector is not on this machine). */
export function foundPeople(s: FeatureSlot): PeopleResult['people'] {
  return s.people && s.people !== 'pending' && s.people.status === 'ok' ? s.people.people : []
}

/** The box that will cut this photo before the first picture is drawn, or null for the whole photo. */
export function featureCrop(s: FeatureSlot, category: string): PersonBox | null {
  if (!looksForPeople(s.role, category) || s.whole || !s.box || !foundPeople(s).length) return null
  return s.box
}

/** Whether the photo is used whole although more than one person is in it: the warning the form shows before the build. */
export function wholeWithOthers(s: FeatureSlot, category: string): boolean {
  return looksForPeople(s.role, category) && !!s.whole && foundPeople(s).length > 1
}

/** The one plain line under a photo that has people in it. */
export function peopleLine(people: PeopleResult['people']): string {
  if (people.length === 1) return 'We found one person in this photo. The box round them is suggested: check it, or change it.'
  return `We found ${people.length} people in this photo. The box round the main person is suggested: check it, or change it.`
}

export const WHOLE_WARNING = 'Other people or things in this photo can end up on your character. Crop to one person for the best result.'

/** A box as whole pixels inside the picture and at least MIN_BOX a side (or the picture side, when smaller); null if not. */
export function clampPersonBox(b: PersonBox, width: number, height: number): PersonBox | null {
  const needW = Math.min(MIN_BOX, width)
  const needH = Math.min(MIN_BOX, height)
  if (b.w <= 0 || b.h <= 0 || b.x < 0 || b.y < 0 || b.x + b.w > width + 0.5 || b.y + b.h > height + 0.5) return null
  if (b.w < needW || b.h < needH) return null
  const x = Math.round(b.x)
  const y = Math.round(b.y)
  return { x, y, w: Math.min(Math.round(b.w), width - x), h: Math.min(Math.round(b.h), height - y) }
}

/** What the filled slots will do, in one plain sentence. */
export function featureEffect(used: FeatureSlot[], startFrom: StartFrom, category: string): string {
  if (!used.length) return ''
  const face = category === 'humanoid' && used.some((s) => s.role === 'face')
  const closeup = face ? ' The face picture is also used to sharpen the face in step 4.' : ''
  if (startFrom === 'model') return 'Your model is not redrawn: the face picture is used to paint the face in step 4.'
  if (startFrom === 'picture' && used.every((s) => s.role === 'face')) return 'Your picture is used as it is in step 1.' + closeup
  const photos = `${used.length === 1 ? 'this photo' : `these ${used.length} photos`}`
  const from = startFrom === 'picture' ? `your picture and ${photos}` : `${photos} and your description`
  return `Step 1 draws one picture of the character from ${from}, in this project's look.` + closeup
}
