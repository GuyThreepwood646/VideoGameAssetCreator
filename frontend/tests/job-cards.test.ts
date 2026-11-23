// The job log says which graphics cards a job used, in plain words, from the job's record (cardlog.py): the lent and
// drawn cards with their step, the card the job ran on with its time, planned cards and moves between cards.
import test from 'node:test'
import assert from 'node:assert/strict'
import { cardUseLines, durationText } from '../src/cardUse'

test('the times read in plain words', () => {
  assert.equal(durationText(45), '45 s')
  assert.equal(durationText(720), '12 min')
  assert.equal(durationText(450), '7 min 30 s')
  assert.equal(durationText(3900), '1 h 5 min')
})

test('a clean-up that lent card a for the face redraw and built on card b says so, with the times', () => {
  const result = {
    cards: [
      { card: 'host', role: 'own', kind: 'prep.run', seconds: 60 },
      { card: 'a', role: 'lent', kind: 'turnaround.run', step: 'face redraw', seconds: 450 },
      { card: 'b', role: 'own', kind: 'mesh.run', seconds: 720 },
    ],
  }
  assert.deepEqual(cardUseLines(result), [
    'Used this PC for 1 min (clean-up), graphics card a for 7 min 30 s (face redraw) and graphics card b for 12 min (build).',
  ])
})

test('the same card used twice in one job is added up once', () => {
  const result = {
    cards: [
      { card: 'b', role: 'own', kind: 'turnaround.run', seconds: 300 },
      { card: 'b', role: 'lent', kind: 'turnaround.run', step: 'turnaround side', seconds: 120 },
    ],
  }
  assert.deepEqual(cardUseLines(result), ['Used graphics card b for 7 min (side views, turnaround side).'])
})

test('a job queued but not started says which card it is planned on', () => {
  assert.deepEqual(cardUseLines({ cards: [{ card: 'a', role: 'planned' }] }), ['Planned on graphics card a.'])
})

test('a move between cards is kept on the record', () => {
  const result = {
    cards: [{ card: 'b', role: 'own', kind: 'concept.run', seconds: 30 }],
    moved: [{ from: 'a', to: 'b', why: 'sharing: the card it was queued on is busy' }],
  }
  assert.deepEqual(cardUseLines(result), [
    'Used graphics card b for 30 s (picture).',
    'Moved from graphics card a to graphics card b (sharing: the card it was queued on is busy).',
  ])
})

test('a job with no record of cards shows nothing', () => {
  assert.deepEqual(cardUseLines(null), [])
  assert.deepEqual(cardUseLines({ description: 'an older record' }), [])
})

test('a running card entry with no end time counts up to now', () => {
  const started = new Date(1_000_000).toISOString()
  const now = 1_000_000 + 90_000
  const lines = cardUseLines({ cards: [{ card: 'b', role: 'own', kind: 'mesh.run', started_at: started, ended_at: null }] }, now)
  assert.deepEqual(lines, ['Used graphics card b for 1 min 30 s (build).'])
})
