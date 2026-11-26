import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { applyGlow } from '../src/glowMap'

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
