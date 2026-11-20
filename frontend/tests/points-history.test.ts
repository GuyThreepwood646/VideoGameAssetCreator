import test from 'node:test'
import assert from 'node:assert/strict'
import { EditHistory } from '../src/editHistory'
import { savePointsStep } from '../src/pipeline/views/pointsHistory'
import type { PlacedPart } from '../src/api'

// A stand-in for the parts PUT: keeps the list it was given, and can be told to fail.
function server(initial: PlacedPart[]) {
  let kept = initial
  const calls: PlacedPart[][] = []
  let failNext = false
  return {
    get kept() {
      return kept
    },
    calls,
    failNext: () => (failNext = true),
    save: async (parts: PlacedPart[]) => {
      calls.push(parts)
      if (failNext) {
        failNext = false
        throw new Error('the server did not keep the points')
      }
      kept = JSON.parse(JSON.stringify(parts))
      return kept
    },
  }
}

const stiff = (locations: number[][]): PlacedPart[] => [{ name: 'backpack', kind: 'stiff', location: null, locations } as unknown as PlacedPart]
const sway = (location: number[] | null): PlacedPart[] => [{ name: 'cape', kind: 'sway', location, locations: [] } as unknown as PlacedPart]

test('placing a point is a named step; undo saves the list from before and redo saves the list it kept', async () => {
  const h = new EditHistory(() => 1000)
  const srv = server(stiff([]))
  let shown: PlacedPart[] = srv.kept
  const before = srv.kept
  await savePointsStep({ history: h, label: 'place a point on the backpack', before, next: stiff([[1, 2, 3]]), save: srv.save, show: (p) => (shown = p) })
  assert.deepEqual(shown, stiff([[1, 2, 3]]))
  assert.equal(h.undoLabel, 'place a point on the backpack')
  assert.equal(await h.undo(), true)
  assert.deepEqual(srv.kept, stiff([]), 'the earlier list is saved through the PUT')
  assert.deepEqual(shown, stiff([]))
  assert.equal(await h.redo(), true)
  assert.deepEqual(srv.kept, stiff([[1, 2, 3]]))
})

test('a removed point comes back with undo, and a later change clears the redo', async () => {
  const h = new EditHistory(() => 1000)
  const srv = server(sway([4, 5, 6]))
  let shown: PlacedPart[] = srv.kept
  await savePointsStep({ history: h, label: 'remove the point on the cape', before: srv.kept, next: sway(null), save: srv.save, show: (p) => (shown = p) })
  await h.undo()
  assert.deepEqual(srv.kept, sway([4, 5, 6]))
  assert.equal(h.redoLabel, 'remove the point on the cape')
  await savePointsStep({ history: h, label: 'place a point on the cape', before: shown, next: sway([7, 8, 9]), save: srv.save, show: (p) => (shown = p) })
  assert.equal(h.redoLabel, null)
  assert.deepEqual(srv.kept, sway([7, 8, 9]))
})

test('a failed save makes no step and throws, so the panel can show the error', async () => {
  const h = new EditHistory(() => 1000)
  const srv = server(stiff([]))
  srv.failNext()
  await assert.rejects(savePointsStep({ history: h, label: 'place a point on the backpack', before: srv.kept, next: stiff([[1, 1, 1]]), save: srv.save, show: () => {} }), /did not keep/)
  assert.equal(h.size, 0)
})

test('a failed undo leaves the step where it was, with the error kept', async () => {
  const h = new EditHistory(() => 1000)
  const srv = server(stiff([]))
  let shown: PlacedPart[] = srv.kept
  await savePointsStep({ history: h, label: 'place a point on the backpack', before: srv.kept, next: stiff([[1, 1, 1]]), save: srv.save, show: (p) => (shown = p) })
  srv.failNext()
  assert.equal(await h.undo(), false)
  assert.equal(h.undoLabel, 'place a point on the backpack')
  assert.ok(h.lastError instanceof Error)
  assert.deepEqual(shown, stiff([[1, 1, 1]]), 'the screen still shows the saved list')
})
