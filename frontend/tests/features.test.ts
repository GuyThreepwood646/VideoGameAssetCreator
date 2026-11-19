import test from 'node:test'
import assert from 'node:assert/strict'
import { clampPersonBox, cropsFace, featureCrop, featureEffect, featureRoles, foundPeople, looksForPeople, peopleLine, slot, usedFeatures, wholeWithOthers, type FeatureSlot } from '../src/featurePhotos'

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

test('"Crop the face from it" only sits under a body photo of a human; not under outfit or other photos', () => {
  assert.equal(cropsFace('body', 'humanoid'), true)
  assert.equal(cropsFace('outfit', 'humanoid'), false)
  assert.equal(cropsFace('other', 'humanoid'), false)
  assert.equal(cropsFace('body', 'quadruped'), false)
})

const ok = (n: number) => ({ status: 'ok' as const, picture: { width: 800, height: 800 }, people: Array.from({ length: n }, () => ({ box: { x: 1, y: 1, w: 100, h: 100 }, score: 0.9 })), main: 0, suggested: { x: 0, y: 0, w: 200, h: 200 } })

test('a person box is whole pixels inside the picture and at least 64 px a side, or the picture side when smaller', () => {
  assert.deepEqual(clampPersonBox({ x: 10.4, y: 20.6, w: 100.2, h: 120 }, 800, 800), { x: 10, y: 21, w: 100, h: 120 })
  assert.equal(clampPersonBox({ x: -1, y: 0, w: 100, h: 100 }, 800, 800), null)
  assert.equal(clampPersonBox({ x: 750, y: 0, w: 100, h: 100 }, 800, 800), null)
  assert.equal(clampPersonBox({ x: 0, y: 0, w: 40, h: 100 }, 800, 800), null)
  assert.deepEqual(clampPersonBox({ x: 0, y: 0, w: 40, h: 50 }, 40, 50), { x: 0, y: 0, w: 40, h: 50 })
})

test('the person box and the whole-photo warning apply to a human photo that is not the Face picture', () => {
  assert.equal(looksForPeople('body', 'humanoid'), true)
  assert.equal(looksForPeople('face', 'humanoid'), false)
  assert.equal(looksForPeople('outfit', 'quadruped'), false)
  const body = { ...slot('body', null), picture: { dataUrl: 'x', name: 'b.png', bytes: 1, width: 800, height: 800 }, people: ok(2), box: null, whole: true }
  assert.equal(wholeWithOthers(body, 'humanoid'), true)
  assert.equal(wholeWithOthers({ ...body, people: ok(1) }, 'humanoid'), false)
  assert.equal(wholeWithOthers({ ...body, whole: false, box: { x: 0, y: 0, w: 200, h: 200 } }, 'humanoid'), false)
  assert.equal(wholeWithOthers(body, 'quadruped'), false)
})

test('the crop sent is the box when one is used, and nothing when the whole photo is used or no person was found', () => {
  const box = { x: 10, y: 20, w: 300, h: 400 }
  const body = { ...slot('body', null), picture: { dataUrl: 'x', name: 'b.png', bytes: 1, width: 800, height: 800 }, people: ok(1), box }
  assert.deepEqual(featureCrop(body, 'humanoid'), box)
  assert.equal(featureCrop({ ...body, whole: true }, 'humanoid'), null)
  assert.equal(featureCrop({ ...body, people: { ...ok(0), status: 'none' } }, 'humanoid'), null)
  assert.equal(featureCrop({ ...body, people: null }, 'humanoid'), null)
  assert.equal(featureCrop(body, 'quadruped'), null)
  assert.equal(foundPeople({ ...body, people: 'pending' }).length, 0)
  assert.equal(foundPeople(body).length, 1)
})

test('the plain line says how many people were found', () => {
  assert.equal(peopleLine(ok(1).people), 'We found one person in this photo. The box round them is suggested: check it, or change it.')
  assert.match(peopleLine(ok(3).people), /^We found 3 people in this photo\./)
})
