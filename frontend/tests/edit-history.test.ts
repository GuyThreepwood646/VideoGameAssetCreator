import test from 'node:test'
import assert from 'node:assert/strict'
import { EditHistory, HISTORY_LIMIT, MERGE_MS, isTextTarget } from '../src/editHistory'

function clock() {
  let t = 1000
  return { now: () => t, tick: (ms: number) => (t += ms) }
}

test('undo and redo walk the steps in order and name the step', async () => {
  const c = clock()
  const h = new EditHistory(c.now)
  const log: string[] = []
  h.record({ label: 'move the left knee', undo: () => log.push('u1'), redo: () => log.push('r1') })
  h.record({ label: 'remove the chin', undo: () => log.push('u2'), redo: () => log.push('r2') })
  assert.equal(h.undoLabel, 'remove the chin')
  assert.equal(await h.undo(), true)
  assert.equal(h.undoLabel, 'move the left knee')
  assert.equal(h.redoLabel, 'remove the chin')
  assert.equal(await h.redo(), true)
  assert.deepEqual(log, ['u2', 'r2'])
})

test('a new step clears the redo steps', async () => {
  const h = new EditHistory(clock().now)
  h.record({ label: 'a', undo: () => {}, redo: () => {} })
  await h.undo()
  assert.equal(h.canRedo, true)
  h.record({ label: 'b', undo: () => {}, redo: () => {} })
  assert.equal(h.canRedo, false)
  assert.equal(h.redoLabel, null)
})

test('the history keeps at most the step limit, dropping the oldest', () => {
  const h = new EditHistory(clock().now)
  for (let i = 0; i < HISTORY_LIMIT + 7; i++) h.record({ label: `step ${i}`, undo: () => {}, redo: () => {} })
  assert.equal(h.size, HISTORY_LIMIT)
  assert.equal(h.undoLabel, `step ${HISTORY_LIMIT + 6}`)
})

test('repeated nudges of the same item within a second merge into one step', async () => {
  const c = clock()
  const h = new EditHistory(c.now)
  const seen: string[] = []
  h.record({ label: 'nudge the sword up', key: 'att1', undo: () => seen.push('undo-first'), redo: () => seen.push('redo-1') })
  c.tick(300)
  h.record({ label: 'nudge the sword up', key: 'att1', undo: () => seen.push('undo-second'), redo: () => seen.push('redo-2') })
  c.tick(MERGE_MS - 100)
  h.record({ label: 'nudge the sword left', key: 'att1', undo: () => seen.push('undo-third'), redo: () => seen.push('redo-3') })
  assert.equal(h.size, 1)
  assert.equal(h.undoLabel, 'nudge the sword left')
  await h.undo()
  // The merged step puts back the state from before the first nudge.
  assert.deepEqual(seen, ['undo-first'])
  await h.redo()
  // and forward to the state after the latest nudge.
  assert.deepEqual(seen, ['undo-first', 'redo-3'])
})

test('nudges more than a second apart, or of another item, are separate steps', () => {
  const c = clock()
  const h = new EditHistory(c.now)
  h.record({ label: 'a', key: 'att1', undo: () => {}, redo: () => {} })
  c.tick(MERGE_MS + 1)
  h.record({ label: 'b', key: 'att1', undo: () => {}, redo: () => {} })
  h.record({ label: 'c', key: 'att2', undo: () => {}, redo: () => {} })
  assert.equal(h.size, 3)
})

test('no new step is made while a save is in flight, and undo and redo wait for it', async () => {
  const h = new EditHistory(clock().now)
  let release: () => void = () => {}
  h.record({ label: 'a', undo: () => new Promise<void>((r) => (release = r)), redo: () => {} })
  const pending = h.undo()
  assert.equal(h.busy, true)
  assert.equal(h.canUndo, false)
  assert.equal(h.record({ label: 'b', undo: () => {}, redo: () => {} }), false)
  assert.equal(await h.undo(), false)
  release()
  assert.equal(await pending, true)
  assert.equal(h.busy, false)
  assert.equal(h.redoLabel, 'a')
})

test('a failed put-back leaves the step where it was and keeps the error', async () => {
  const h = new EditHistory(clock().now)
  const boom = new Error('server said no')
  h.record({ label: 'move the chin', undo: () => Promise.reject(boom), redo: () => {} })
  assert.equal(await h.undo(), false)
  assert.equal(h.lastError, boom)
  assert.equal(h.undoLabel, 'move the chin')
  assert.equal(h.canRedo, false)
  assert.equal(h.busy, false)
})

test('a failed redo keeps the step on the redo side', async () => {
  const h = new EditHistory(clock().now)
  h.record({ label: 'move the chin', undo: () => {}, redo: () => Promise.reject(new Error('no')) })
  await h.undo()
  assert.equal(await h.redo(), false)
  assert.equal(h.redoLabel, 'move the chin')
  assert.ok(h.lastError instanceof Error)
})

test('subscribers hear about every change of the history', async () => {
  const h = new EditHistory(clock().now)
  let n = 0
  const off = h.subscribe(() => n++)
  h.record({ label: 'a', undo: () => {}, redo: () => {} })
  await h.undo()
  off()
  h.record({ label: 'b', undo: () => {}, redo: () => {} })
  assert.equal(n, 3)
})

test('keys never act inside text fields, selects or editable text', () => {
  assert.equal(isTextTarget({ tagName: 'INPUT' } as unknown as EventTarget), true)
  assert.equal(isTextTarget({ tagName: 'TEXTAREA' } as unknown as EventTarget), true)
  assert.equal(isTextTarget({ tagName: 'SELECT' } as unknown as EventTarget), true)
  assert.equal(isTextTarget({ tagName: 'DIV', isContentEditable: true } as unknown as EventTarget), true)
  assert.equal(isTextTarget({ tagName: 'BUTTON' } as unknown as EventTarget), false)
  assert.equal(isTextTarget(null), false)
})
