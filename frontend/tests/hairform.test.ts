import test from 'node:test'
import assert from 'node:assert/strict'
import { hairFormState, hairLibraryOffer } from '../src/pipeline/HairSetting'

const OK = { ok: true, reason: '' }
const KEPT = 'The hair stays built into the 3D model.'

test('a library choice that the look or description rules out is disabled, with the reason and what stays', () => {
  const rule = hairLibraryOffer('a knight, bald, scarred', 'realistic_pbr', 'humanoid')
  const s = hairFormState(rule, { state: 'ready', installed: true, styles: 26 }, 'pc_mid')
  assert.equal(s.usable, false)
  assert.equal(s.reason, 'The description says the character is bald. ' + KEPT)
  assert.equal(s.notInstalled, false)
})

test('a model the person imported keeps its own hair', () => {
  const s = hairFormState({ ok: false, reason: 'A 3D model you bring is used as it is.' }, { state: 'ready', installed: true, styles: 26 }, 'pc_mid')
  assert.equal(s.usable, false)
  assert.equal(s.reason, 'A 3D model you bring is used as it is. ' + KEPT)
})

test('while the library is being checked the choice is disabled, not guessed', () => {
  const s = hairFormState(OK, { state: 'checking' }, 'pc_mid')
  assert.equal(s.usable, false)
  assert.equal(s.reason, 'Checking the hair library...')
})

test('a failed check says so and keeps the hair in the model', () => {
  const s = hairFormState(OK, { state: 'failed' }, 'pc_mid')
  assert.equal(s.usable, false)
  assert.equal(s.reason, 'The hair library could not be checked just now. ' + KEPT)
})

test('an installed library with fitting hairstyles is usable', () => {
  const s = hairFormState(OK, { state: 'ready', installed: true, styles: 26 }, 'pc_mid')
  assert.deepEqual(s, { usable: true, reason: '', notInstalled: false })
})

test('a missing library with no hairstyles of the person own is disabled with the install pointer', () => {
  const s = hairFormState(OK, { state: 'ready', installed: false, styles: 0 }, 'pc_mid')
  assert.equal(s.usable, false)
  assert.equal(s.notInstalled, true)
  assert.equal(s.reason, 'The hair library is not installed yet. ' + KEPT)
})

test('the person own hairstyles make the library usable even when the built-in one is missing', () => {
  const s = hairFormState(OK, { state: 'ready', installed: false, styles: 2 }, 'pc_mid')
  assert.deepEqual(s, { usable: true, reason: '', notInstalled: false })
})

test('on Phone with no light hairstyle the reason says so', () => {
  const s = hairFormState(OK, { state: 'ready', installed: true, styles: 0 }, 'mobile_low')
  assert.equal(s.usable, false)
  assert.equal(s.reason, 'No hairstyle in the library is light enough for Phone. ' + KEPT)
})
