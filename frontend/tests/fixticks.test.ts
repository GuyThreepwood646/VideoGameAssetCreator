import test from 'node:test'
import assert from 'node:assert/strict'
import { tickable } from '../src/pipeline/FixTicks'
import { FIX_TICK_LABELS } from '../src/help'
import type { Check } from '../src/api'

test('only failed checks the backend marked as fixable by one redraw get a tick box', () => {
  const checks: Check[] = [
    { name: 'in_frame', passed: false, fix_snippet: 'frame' },
    { name: 'arms_away', passed: true, fix_snippet: 'pose_a' },
    { name: 'big_enough', passed: false },
    { name: 'hair_off_eyes', passed: false, fix_snippet: 'hair_off' },
  ]
  assert.deepEqual(tickable(checks).map((c) => c.name), ['in_frame', 'hair_off_eyes'])
})

test('every check the redraw words cover has plain tick box words', () => {
  for (const id of ['in_frame', 'arms_away', 'pose_for_3d', 'soft_light', 'hair_off_eyes', 'face_drawn']) assert.ok(FIX_TICK_LABELS[id], id)
})
