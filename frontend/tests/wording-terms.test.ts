import test from 'node:test'
import assert from 'node:assert/strict'
import type { AssetSummary, Category } from '../src/api'
import { plainJargon, splitTechnical, stageTech, tryAgainLabel } from '../src/help'
import { groupAdvanced } from '../src/pipeline/FineTune'
import { groupCategories, mostUsedCategory } from '../src/pages/ProjectPage'

test('every step has an industry term, and the backend one wins', () => {
  assert.equal(stageTech('prep'), 'Retopology, UV unwrap, texture bake')
  assert.equal(stageTech('prep', { id: 'prep', label: 'x', adjust_schema: [], technical_short: 'From the backend' }), 'From the backend')
})

test('Try again names what it will make, and is absent for a picture with no description', () => {
  assert.equal(tryAgainLabel('concept', { count: 2, hasDescription: true }), 'Paint 2 new pictures from the description')
  assert.equal(tryAgainLabel('concept', { count: 1, hasDescription: true }), 'Paint 1 new picture from the description')
  assert.equal(tryAgainLabel('concept', { fromPicture: true, hasDescription: false }), null)
  assert.equal(tryAgainLabel('export'), 'Make the files again')
})

test('the project default category is its most-used one', () => {
  const a = (category: string) => ({ category }) as AssetSummary
  assert.equal(mostUsedCategory([]), null)
  assert.equal(mostUsedCategory([a('environment'), a('environment'), a('humanoid')]), 'environment')
})

test('categories are grouped under their headings in order', () => {
  const c = (id: string, group: string) => ({ id, label: id, group }) as Category
  const g = groupCategories([c('humanoid', 'Characters and creatures'), c('vehicle', 'Objects and vehicles'), c('quadruped', 'Characters and creatures')])
  assert.deepEqual(g.map((x) => [x.name, x.items.map((i) => i.id)]), [['Characters and creatures', ['humanoid', 'quadruped']], ['Objects and vehicles', ['vehicle']]])
})

test('advanced settings are grouped, and an unknown one goes under Other', () => {
  const g = groupAdvanced([{ name: 'seed' }, { name: 'texture_size' }, { name: 'face_detail' }, { name: 'hair_detail' }, { name: 'remesh' }, { name: 'brand_new_knob' }])
  assert.deepEqual(g.map((x) => x.name), ['Picture and views', 'Shape', 'Surface and textures', 'Face', 'Hair', 'Other'])
})

test('the industry term is split from the field name', () => {
  assert.deepEqual(splitTechnical('variant (base_asset_id)'), { industry: 'variant', file: 'base_asset_id' })
  assert.deepEqual(splitTechnical('checkpoint (data/models.yaml)'), { industry: 'checkpoint', file: 'data/models.yaml' })
  assert.deepEqual(splitTechnical('feedback (metrics.feedback)'), { industry: 'feedback', file: 'metrics.feedback' })
  assert.deepEqual(splitTechnical('style_lock'), { industry: null, file: 'style_lock' })
  assert.deepEqual(splitTechnical('Latent width'), { industry: 'Latent width', file: null })
})

test('job messages keep their industry terms in brackets', () => {
  assert.equal(plainJargon('uv unwrap done'), 'laying out the textures (UV unwrap) done')
  assert.equal(plainJargon('texture voxel'), 'painting the textures (texture voxel)')
})
