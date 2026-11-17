import test from 'node:test'
import assert from 'node:assert/strict'
import { featureEffect, featureRoles, usedFeatures, type FeatureSlot } from '../src/featurePhotos'

const pic = { dataUrl: 'data:image/png;base64,AAAA', name: 'p.png', bytes: 3, width: 512, height: 512 }
const slots: FeatureSlot[] = [
  { key: 1, role: 'face', picture: pic, note: '' },
  { key: 2, role: 'outfit', picture: pic, note: 'green cloak' },
  { key: 3, role: 'body', picture: null, note: '' },
]

test('every start offers feature photos for a person; a model you have takes only the face', () => {
  assert.deepEqual(featureRoles('words', 'humanoid'), ['face', 'body', 'outfit', 'other'])
  assert.deepEqual(featureRoles('picture', 'quadruped'), ['face', 'body', 'outfit', 'other'])
  assert.deepEqual(featureRoles('model', 'humanoid'), ['face'])
  assert.deepEqual(featureRoles('model', 'quadruped'), [])
  assert.deepEqual(featureRoles('words', 'material'), [])
})

test('only filled slots of an offered role are sent', () => {
  assert.deepEqual(usedFeatures(slots, 'words', 'humanoid').map((s) => s.key), [1, 2])
  assert.deepEqual(usedFeatures(slots, 'model', 'humanoid').map((s) => s.key), [1])
})

test('the form says that several photos make one picture', () => {
  const used = usedFeatures(slots, 'words', 'humanoid')
  assert.match(featureEffect(used, 'words', 'humanoid'), /one picture of the character from these 2 photos and your description/)
  assert.match(featureEffect(used, 'picture', 'humanoid'), /your picture and these 2 photos/)
  assert.match(featureEffect([slots[0]], 'picture', 'humanoid'), /used as it is in step 1/)
  assert.match(featureEffect([slots[0]], 'model', 'humanoid'), /not redrawn/)
})
