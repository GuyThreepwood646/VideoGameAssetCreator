import test from 'node:test'
import assert from 'node:assert/strict'
import { confirmLeaveUnsavedPaint, hasUnsavedPaint, setUnsavedPaint, UNSAVED_PAINT_MESSAGE } from '../src/unsavedPaint'

test('unsaved paint is tracked per panel, and leaving asks only while some panel has some', () => {
  const asked: string[] = []
  const g = globalThis as unknown as { window?: unknown }
  g.window = { confirm: (msg: string) => (asked.push(msg), false) }
  try {
    assert.equal(hasUnsavedPaint(), false)
    assert.equal(confirmLeaveUnsavedPaint(), true, 'nothing unsaved: leave without asking')
    assert.equal(asked.length, 0)
    setUnsavedPaint('mesh-a', true)
    setUnsavedPaint('mesh-b', true)
    assert.equal(hasUnsavedPaint(), true)
    assert.equal(confirmLeaveUnsavedPaint(), false, 'the person says stay')
    assert.deepEqual(asked, [UNSAVED_PAINT_MESSAGE])
    setUnsavedPaint('mesh-a', false)
    assert.equal(hasUnsavedPaint(), true, 'mesh-b still has unsaved paint')
    setUnsavedPaint('mesh-b', false)
    assert.equal(hasUnsavedPaint(), false)
  } finally {
    delete g.window
  }
})
