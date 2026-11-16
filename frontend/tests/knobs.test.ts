import test from 'node:test'
import assert from 'node:assert/strict'
import { knobShown, optionLabel, paramPlain } from '../src/help'

test('the 3D builder shows only when Pixal3D is offered beside TRELLIS.2', () => {
  const builder = { name: 'builder', type: 'select', options: ['trellis2'] }
  assert.equal(knobShown(builder, 'humanoid'), false, 'one builder: nothing to choose')
  assert.equal(knobShown({ ...builder, options: ['trellis2', 'pixal3d'] }, 'humanoid'), true)
  // other knobs are unchanged: a boolean, a select with choices, a category-only knob
  assert.equal(knobShown({ type: 'boolean', categories: ['humanoid'] }, 'humanoid'), true)
  assert.equal(knobShown({ type: 'boolean', categories: ['humanoid'] }, 'prop_rigid'), false)
  assert.equal(knobShown({ type: 'select', options: null }, 'humanoid'), true)
})

test('the new settings have plain labels', () => {
  assert.equal(optionLabel({ name: 'builder' }, 'pixal3d'), 'Pixal3D (from all four sides)')
  assert.equal(optionLabel({ name: 'builder' }, 'trellis2'), 'TRELLIS.2 (from one picture)')
  assert.equal(paramPlain('quarter_pictures'), 'Paint from the three-quarter pictures')
  assert.equal(paramPlain('builder'), '3D builder')
})
