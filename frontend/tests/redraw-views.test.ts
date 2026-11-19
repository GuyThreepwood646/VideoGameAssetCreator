import test from 'node:test'
import assert from 'node:assert/strict'
import { PLAIN_VIEW_REDRAW, redrawViewsSeconds, redrawViewsWarning } from '../src/help'
import { redrawnLine } from '../src/pipeline/views/TurnaroundView'
import type { Version } from '../src/api'

test('a redraw of some views takes that share of the step time, and one view is not warned about', () => {
  assert.equal(redrawViewsSeconds(600, 2, 4), 300)
  assert.equal(redrawViewsSeconds(600, 4, 4), 600)
  assert.equal(redrawViewsWarning(1, 150, true), null)
  const w = redrawViewsWarning(4, 600, true)
  assert.ok(w && w.includes('4 views') && w.includes('10 minutes') && w.includes('Every one of them gets your words'))
  assert.ok(redrawViewsWarning(2, 300, false)?.includes('drawn again the same way'))
})

test('after a change the version says which views were redrawn and which were carried over', () => {
  const v = { params: { refined_views: ['left', 'back'], instruction: 'remove the people' } } as unknown as Version
  assert.equal(redrawnLine(v, ['front', 'left', 'back', 'right']), 'Redrawn in this version for "remove the people": Left side and back. Front and right side are carried over unchanged.')
  const plain = { params: { refined_views: ['front'], instruction: PLAIN_VIEW_REDRAW } } as unknown as Version
  assert.ok(!redrawnLine(plain, ['front', 'left'])!.includes('for "'))
  assert.equal(redrawnLine({ params: {} } as unknown as Version, ['front']), null)
})
