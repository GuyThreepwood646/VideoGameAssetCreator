import test from 'node:test'
import assert from 'node:assert/strict'
import { oneLine } from '../src/pipeline/finish'
import { failingTier } from '../src/help'

test('a check reads as one short line: first sentence, no brackets, cut at a clause, never an ellipsis', () => {
  assert.equal(oneLine('The surface has 100 broken edges. More than 59 can show as holes.'), 'The surface has 100 broken edges')
  const long = 'Both upper arms are grown into the body and something it wears or carries (a cape, cloak, hair or an item), from the armpit to past the middle of the upper arm, so lifting an arm stretches it. More.'
  assert.equal(oneLine(long), 'Both upper arms are grown into the body and something it wears or carries')
  const marks = 'Please check the marks on the body under Mark the body: left shoulder in the front picture; right shoulder in the front picture; left elbow in the front picture.'
  assert.equal(oneLine(marks), 'Please check the marks on the body under Mark the body')
  for (const t of [long, marks, "The neck passes from the face's painting to the body's within 0 cm, at one height all round (it should take at least 2 cm), so a line or band may show."]) {
    const l = oneLine(t)
    assert.ok(l.length <= 120 && !l.includes('...'), l)
  }
})

test('the version card dot: red only for a likely check, amber for the rest (UX-10)', () => {
  const c = (tier?: string) => ({ checks: [{ name: 'x', passed: false, tier }] })
  assert.equal(failingTier(c('likely')), 'likely')
  assert.equal(failingTier(c('look')), 'look')
  assert.equal(failingTier(c('advice')), 'advice')
  assert.equal(failingTier(c(undefined)), 'look') // no tier of its own: the default tier is "Worth a look", amber (F6)
  assert.equal(failingTier({ checks: [{ name: 'x', passed: true }] }), null)
  assert.equal(failingTier({ checks: [{ name: 'a', passed: false, tier: 'advice' }, { name: 'b', passed: false, tier: 'look' }] }), 'look')
})
