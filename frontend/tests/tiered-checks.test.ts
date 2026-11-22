import test from 'node:test'
import assert from 'node:assert/strict'
import { oneLine } from '../src/pipeline/finish'
import { failingTier } from '../src/help'

test('a check reads as one line: its first sentence, cut at a word when long', () => {
  assert.equal(oneLine('The surface has 100 broken edges. More than 59 can show as holes.'), 'The surface has 100 broken edges.')
  const long = 'Both upper arms are grown into the body and something it wears or carries (a cape, cloak, hair or an item), from the armpit to past the middle of the upper arm, so lifting an arm stretches it. More.'
  const l = oneLine(long)
  assert.ok(l.length <= 154 && l.endsWith('...'), l)
})

test('the version card dot: red only for a likely check, amber for the rest (UX-10)', () => {
  const c = (tier?: string) => ({ checks: [{ name: 'x', passed: false, tier }] })
  assert.equal(failingTier(c('likely')), 'likely')
  assert.equal(failingTier(c('look')), 'look')
  assert.equal(failingTier(c('advice')), 'advice')
  assert.equal(failingTier(c(undefined)), null)
  assert.equal(failingTier({ checks: [{ name: 'x', passed: true }] }), null)
  assert.equal(failingTier({ checks: [{ name: 'a', passed: false, tier: 'advice' }, { name: 'b', passed: false, tier: 'look' }] }), 'look')
})
