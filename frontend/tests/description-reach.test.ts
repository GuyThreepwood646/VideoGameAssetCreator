import test from 'node:test'
import assert from 'node:assert/strict'
import { DESCRIPTION_NOTE, DRAW_NEW_STEP_ONE_LABEL, showsDescriptionNote } from '../src/pipeline/descriptionNote'
import { STAGES } from '../src/api'

test('the note says what the description is for and what to do to change the character', () => {
  assert.equal(
    DESCRIPTION_NOTE,
    'Your description is used to draw the step 1 picture and for a few checks later. The steps after that work from the step 1 picture, so to change the character, draw a new step 1 picture.',
  )
  assert.equal(DRAW_NEW_STEP_ONE_LABEL, 'Draw a new step 1 picture')
})

test('the note shows on steps 2 to 6 and not on step 1', () => {
  assert.equal(showsDescriptionNote('concept'), false)
  for (const s of STAGES.slice(1)) assert.equal(showsDescriptionNote(s), true, s)
  assert.equal(STAGES.length, 6)
})
