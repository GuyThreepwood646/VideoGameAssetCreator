import test from 'node:test'
import assert from 'node:assert/strict'
import { CATEGORY_HELP, exampleName, examplePrompts, failingTier, fmtDuration, stagePlainFor, totalEstimate } from '../src/help'
import { builtFromNumber, stageEstimateSeconds, stepsFor, STAGES } from '../src/api'
import type { StageInfo } from '../src/api'

// The scorecard audit's wording and small screen fixes (WP audit batch A): F4, F6, F10, F12, F13.

// F4: a texture-only build was shown as 37 minutes, from the stage list's history for every category together.
// The suggestion's own per-step estimates (this category, from its own history) now decide the total.
test('a texture-only asset is estimated from its own steps, not the stage list (F4)', () => {
  const materialEstimates = { concept: 50, turnaround: 0, mesh: 40, prep: 5, rig: 1, export: 5 }
  const stageList = STAGES.map((id) => ({ id, estimate_seconds: 1800 })) as unknown as StageInfo[]
  const total = totalEstimate(materialEstimates, { stages: stageList })
  assert.equal(total, 101)
  assert.equal(fmtDuration(total), 'about 2 minutes')
})

test('a step the suggestion does not name falls back to the stage list', () => {
  const stageList = [{ id: 'export', estimate_seconds: 120 }] as unknown as StageInfo[]
  const expected = STAGES.reduce((t, s) => t + (s === 'export' ? 120 : stageEstimateSeconds(s)), 0)
  assert.equal(totalEstimate({}, { stages: stageList }), expected)
})

// F13: a repeating surface does not run "Check all sides", so it is not a step that failed to finish.
test('a repeating surface counts only the steps it runs (F13)', () => {
  assert.deepEqual(stepsFor('material'), ['concept', 'mesh', 'prep', 'rig', 'export'])
  assert.equal(stepsFor('humanoid').length, STAGES.length)
  assert.equal(stagePlainFor('mesh', 'material'), 'Make the surface maps')
  assert.equal(stagePlainFor('mesh', 'humanoid'), 'Build the 3D model')
})

// F6: a failing check with no tier of its own (the view consistency check) reads as the default tier, "look".
test('a failing version with no tier shows the default look tier (F6)', () => {
  assert.equal(failingTier({ consistency_ok: false }), 'look')
  assert.equal(failingTier({ checks: [{ name: 'x', passed: false }] }), 'look')
  assert.equal(failingTier({ checks: [{ name: 'x', passed: true }] }), null)
})

// F10: the Describe and Name examples follow the category.
test('the example text follows the category (F10)', () => {
  assert.match(examplePrompts('foliage')[0], /fern|dead oak|oak/i)
  assert.equal(exampleName('foliage'), 'Oak')
  assert.equal(exampleName('humanoid'), 'Knight')
  assert.notEqual(examplePrompts('vehicle')[0], examplePrompts('humanoid')[0])
})

// F12: the surface help names the buttons the panel shows.
test('the repeating surface help uses the button names (F12)', () => {
  const all = JSON.stringify(CATEGORY_HELP.material)
  assert.ok(all.includes('Solid object') && all.includes('Plant or tree'), all)
  assert.ok(!all.includes('Rigid prop'), all)
})

// F23: a step's version names the earlier version it was really built from (its parent), not the kept one.
test('a version names its parent, not the kept version of the earlier step (F23)', () => {
  const v = (id: string, parent: string | null) => ({ id, parent_version_id: parent, status: 'done' }) as never
  const asset = {
    stages: { mesh: [v('m1', null), v('m2', null)], prep: [v('p1', 'm1')] },
    selected: { mesh: 'm2', prep: 'p1' },
  } as never
  assert.equal(builtFromNumber(asset, 'mesh', v('p1', 'm1')), 1)
  assert.equal(builtFromNumber(asset, 'mesh', v('p2', 'm2')), 2)
  assert.equal(builtFromNumber(asset, 'mesh', v('p3', null)), 0)
  assert.equal(builtFromNumber(asset, null, v('p1', 'm1')), 0)
})
