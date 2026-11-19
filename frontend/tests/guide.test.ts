import test from 'node:test'
import assert from 'node:assert/strict'
import { conceptCountDefault, conceptCountLabel, rememberConceptCount } from '../src/guide'

// A small in-memory localStorage, put on globalThis for the duration of each test.
function withStorage(run: (store: Map<string, string>) => void) {
  const store = new Map<string, string>()
  const fake = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
  }
  const g = globalThis as { localStorage?: unknown }
  const before = g.localStorage
  g.localStorage = fake
  try {
    run(store)
  } finally {
    g.localStorage = before
  }
}

test('step 1 draws two pictures when nothing is remembered', () => {
  withStorage(() => {
    assert.equal(conceptCountDefault(), 2)
  })
})

test('a bad remembered value falls back to two pictures', () => {
  withStorage((store) => {
    for (const bad of ['0', '3', '4', 'one', '', 'NaN']) {
      store.set('vgac.conceptCount', bad)
      assert.equal(conceptCountDefault(), 2, `value ${JSON.stringify(bad)}`)
    }
  })
})

test('no localStorage at all falls back to two pictures', () => {
  // Node has no localStorage: the helpers must not throw.
  assert.equal(conceptCountDefault(), 2)
  assert.doesNotThrow(() => rememberConceptCount(1))
})

test('remembering one picture is read back as one, and two as two', () => {
  withStorage(() => {
    rememberConceptCount(1)
    assert.equal(conceptCountDefault(), 1)
    rememberConceptCount(2)
    assert.equal(conceptCountDefault(), 2)
  })
})

test('the start button names the count', () => {
  assert.equal(conceptCountLabel(2), 'Draw 2 pictures')
  assert.equal(conceptCountLabel(1), 'Draw 1 picture')
})
