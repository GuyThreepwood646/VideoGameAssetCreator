import test from 'node:test'
import assert from 'node:assert/strict'
import { isRunning, pendingSuggestion, shouldStart, suggestionLine, suggestionNote, suggestionPoints, type SuggestionAnswer } from '../src/suggestion'

const base: SuggestionAnswer = { version_id: 'v1', installed: true, eligible: [{ type: 'backpack', label: 'backpack' }], state: null }

test('a suggestion is asked for only when a back item is kept, the finder is installed and none exists yet', () => {
  assert.equal(shouldStart(base), true)
  assert.equal(shouldStart({ ...base, installed: false }), false, 'not installed: nothing is offered')
  assert.equal(shouldStart({ ...base, eligible: [] }), false, 'no kept back item: nothing is offered')
  assert.equal(shouldStart({ ...base, state: { state: 'dismissed', item: 'backpack' } }), false, 'dismissed is never run again')
  assert.equal(shouldStart({ ...base, state: { state: 'kept', item: 'backpack' } }), false)
  assert.equal(shouldStart(null), false)
})

test('the page keeps asking while the suggestion is running', () => {
  assert.equal(isRunning({ ...base, state: { state: 'running', item: 'backpack' } }), true)
  assert.equal(isRunning({ ...base, state: { state: 'done', item: 'backpack' } }), false)
})

test('only a found, not yet kept or dismissed suggestion is shown as pending', () => {
  const found = { state: 'done' as const, item: 'backpack', label: 'backpack', found: true, points: [[0, 1.1, -0.25]] }
  assert.deepEqual(pendingSuggestion({ ...base, state: found }), found)
  assert.equal(pendingSuggestion({ ...base, state: { ...found, found: false } }), null, 'nothing found')
  assert.equal(pendingSuggestion({ ...base, state: { ...found, points: [] } }), null, 'no points to show')
  assert.equal(pendingSuggestion({ ...base, state: { ...found, state: 'dismissed' } }), null)
  assert.equal(pendingSuggestion({ ...base, state: { ...found, state: 'kept' } }), null)
})

test('the line names the item and says what the person can do', () => {
  assert.equal(
    suggestionLine({ state: 'done', item: 'backpack', label: 'backpack' }),
    'Suggested from the description: backpack. Keep it, adjust it with points, or dismiss it.',
  )
})

test('a large region gets the hair or cape note, a small one does not', () => {
  assert.equal(suggestionNote({ state: 'done', large: true }), 'This may include hair or a cape; check it before keeping it.')
  assert.equal(suggestionNote({ state: 'done', large: false }), null)
})

test('the stored points come back as three numbers each', () => {
  assert.deepEqual(suggestionPoints({ state: 'done', points: [[0, 1.1, -0.25], [1, 2]] }), [[0, 1.1, -0.25]])
})
