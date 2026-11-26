import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { applyGlow, composeGlow } from '../src/glowMap'

test('the glow picture goes on materials that have a colour picture, set up like a glTF texture', () => {
  const colour = new THREE.Texture()
  colour.channel = 0
  const lit = new THREE.MeshStandardMaterial({ map: colour })
  const plain = new THREE.MeshStandardMaterial()
  const glow = new THREE.Texture()
  const n = applyGlow([lit, [plain]], glow)
  assert.equal(n, 1)
  assert.equal(lit.emissiveMap, glow)
  assert.equal(glow.flipY, false)
  assert.equal(glow.colorSpace, THREE.SRGBColorSpace)
  assert.equal(lit.emissive.getHex(), 0xffffff)
  assert.equal(plain.emissiveMap, null)
})

test('composeGlow lays the brush over the parent glow in the colour under it', () => {
  const out = new Uint8Array(12)
  const parent = new Uint8Array([10, 20, 30, 255, 40, 50, 60, 255, 70, 80, 90, 255])
  const colour = new Uint8Array([200, 100, 0, 255, 200, 100, 0, 255, 200, 100, 0, 255])
  const edits = new Uint8Array([0, 0, 0, 0, 255, 0, 0, 0, 1, 0, 0, 0])
  composeGlow(out, parent, edits, colour)
  assert.deepEqual(Array.from(out.slice(0, 3)), [10, 20, 30])
  assert.deepEqual(Array.from(out.slice(4, 7)), [200, 100, 0])
  assert.deepEqual(Array.from(out.slice(8, 11)), [0, 0, 0])
  composeGlow(out, null, edits, colour)
  assert.deepEqual(Array.from(out.slice(0, 3)), [0, 0, 0])
})

test('composeGlow keeps the parent glow strength under a colour repaint, in the new colour', () => {
  const out = new Uint8Array(4)
  const parent = new Uint8Array([100, 100, 0, 255])
  const original = new Uint8Array([200, 200, 0, 255])
  const colour = new Uint8Array([0, 0, 100, 255])
  composeGlow(out, parent, new Uint8Array(4), colour, original)
  assert.deepEqual(Array.from(out.slice(0, 3)), [0, 0, 50])
  composeGlow(out, parent, new Uint8Array(4), original, original)
  assert.deepEqual(Array.from(out.slice(0, 3)), [100, 100, 0])
})
