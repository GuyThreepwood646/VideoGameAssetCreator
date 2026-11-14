import test from 'node:test'
import assert from 'node:assert/strict'
import { checkFixNote } from '../src/help'
import type { Check } from '../src/api'

test('a failed check shows the line the backend gave it, and a passed check shows none', () => {
  const failed: Check = { name: 'unity_import', passed: false, fix_note: 'Nothing in the app fixes this. Open the Unity console.', fix_kind: 'outside' }
  assert.equal(checkFixNote(failed), 'Nothing in the app fixes this. Open the Unity console.')
  const passed: Check = { name: 'unity_import', passed: true, fix_note: 'left over', fix_kind: 'outside' }
  assert.equal(checkFixNote(passed), null)
})

test('a check without a line, or with a blank one, shows nothing', () => {
  assert.equal(checkFixNote({ name: 'manifold', passed: false }), null)
  assert.equal(checkFixNote({ name: 'manifold', passed: false, fix_note: '   ' }), null)
})

test('a Try again button keeps its words and note', () => {
  const c: Check = { name: 'lod_count', passed: false, fix: { stage: 'export', params: {}, label: 'Try again' }, fix_note: 'This runs the step again with the same settings.', fix_kind: 'rerun' }
  assert.match(checkFixNote(c) ?? '', /same settings/)
})
