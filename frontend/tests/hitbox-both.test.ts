import test from 'node:test'
import assert from 'node:assert/strict'
import { checkFix, checkSentence } from '../src/help'
import type { Check } from '../src/api'

// The backend's combined hit-box check (rig.hitbox_checks, vars.both) as the API serves it: cover 68 percent (the audit's
// snake) and the overreach of 7.7 times (the audit's beetle), one skeleton. The check carries the backend's own sentence
// and fix; the frontend's own wording and fix are the fallback when a payload has neither.
const COMBINED = "The hit boxes are the wrong size in places: they cover only 68% of the body, so some hits would pass through, and some boxes stick out 7.7 times the body's size, so other hits would land in thin air."
const served: Check = {
  name: 'hitbox_cover',
  id: 'hitbox_cover',
  passed: false,
  value: 0.68,
  limit: 0.85,
  severity: 'warn',
  tier: 'likely',
  headline: 'The hit boxes are the wrong size in places',
  vars: { n: 11, cover: '68%', times: '7.7', both: true },
  help: COMBINED,
  fix: { stage: 'rig', params: { hitbox_fit: 'normal' }, label: 'Fit the hit boxes again' },
  fix_label: 'Fit the hit boxes again',
}

test('a served combined hit-box check shows the combined sentence, the one re-fit label and hitbox_fit normal', () => {
  assert.equal(checkSentence(served), COMBINED)
  const fix = checkFix(served)
  assert.ok(fix, 'the combined item has a fix')
  assert.equal(fix.label, 'Fit the hit boxes again')
  assert.equal(fix.stage, 'rig')
  assert.deepEqual(fix.params, { hitbox_fit: 'normal' })
})

test('without the backend sentence and fix, the frontend builds the same combined item from vars.both', () => {
  const bare: Check = { name: 'hitbox_cover', id: 'hitbox_cover', passed: false, value: 0.68, limit: 0.85, vars: { n: 11, cover: '68%', times: '7.7', both: true } }
  assert.equal(checkSentence(bare), COMBINED)
  const fix = checkFix(bare)
  assert.ok(fix)
  assert.equal(fix.label, 'Fit the hit boxes again')
  assert.deepEqual(fix.params, { hitbox_fit: 'normal' })
  assert.ok(!/bigger|smaller|looser|loose|snug/i.test(fix.label), 'no bigger-or-smaller label on the combined item')
})

test('a cover-only fault keeps its own sentence and its own bigger fix', () => {
  const cover: Check = { name: 'hitbox_cover', id: 'hitbox_cover', passed: false, value: 0.6, limit: 0.85, vars: { n: 11, cover: '60%' } }
  assert.equal(checkSentence(cover), 'The hit boxes cover only 60% of the body, so some hits would pass through.')
  const fix = checkFix(cover)
  assert.ok(fix)
  assert.equal(fix.label, 'Make the hit boxes bigger')
  assert.deepEqual(fix.params, { hitbox_fit: 'loose' })
})
