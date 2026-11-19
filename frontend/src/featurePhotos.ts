// Feature photos in the New asset form (components/FeaturePhotos.tsx): which roles each Start from offers, which slots
// are sent, and the one sentence that says what they will do. The backend is vgac/compose.py.
import type { FeatureRole } from './api'
import type { PickedImage } from './components/ImageDrop'

// The kinds of asset the combined picture is drawn for (characters and creatures).
export const FEATURE_CATEGORIES = ['humanoid', 'quadruped', 'multileg', 'winged', 'serpent']
export const MAX_FEATURES = 6

export type FeatureSlot = { key: number; role: FeatureRole; picture: PickedImage | null; note: string }
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

