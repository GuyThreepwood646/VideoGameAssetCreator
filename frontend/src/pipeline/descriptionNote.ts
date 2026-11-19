// The note under the description on steps 2 to 6, and its button. The description draws the step 1 picture and a few
// checks later; the steps after step 1 work from the kept step 1 picture, so a changed character needs a new step 1.
import type { StageId } from '../api'

export const DESCRIPTION_NOTE =
  'Your description is used to draw the step 1 picture and for a few checks later. The steps after that work from the step 1 picture, so to change the character, draw a new step 1 picture.'

export const DRAW_NEW_STEP_ONE_LABEL = 'Draw a new step 1 picture'

// Shown on every step except step 1, where the description is already the main thing on the page.
export function showsDescriptionNote(stage: StageId): boolean {
  return stage !== 'concept'
}
