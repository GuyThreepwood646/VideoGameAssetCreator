import test from 'node:test'
import assert from 'node:assert/strict'
import { markKey, restorePlan, snapshotMarks, type MarkSnapshot } from '../src/pipeline/views/marksHistory'
import type { BodyMarks } from '../src/api'

function marks(spec: Record<string, Record<string, { x: number | null; y: number | null }>>): BodyMarks {
  const out: Record<string, Record<string, unknown>> = {}
  for (const [view, joints] of Object.entries(spec)) {
    out[view] = {}
    for (const [joint, p] of Object.entries(joints)) out[view][joint] = { ...p, source: 'app', sure: true, guessed: false, stale: false, seen: p.x !== null }
  }
  return { marks: out } as unknown as BodyMarks
}

test('a snapshot keeps every mark place, and an unseen mark is null', () => {
  const snap = snapshotMarks(marks({ front: { chin: { x: 10, y: 20 } }, left: { hand_L: { x: null, y: null } } }))
  assert.deepEqual(snap, { 'front|chin': { x: 10, y: 20 }, 'left|hand_L': null })
  assert.equal(markKey('front', 'chin'), 'front|chin')
})

test('putting marks back sends only the moves that differ, as one batch', () => {
  const now: MarkSnapshot = { 'front|chin': { x: 12, y: 20 }, 'front|knee_L': { x: 5, y: 6 }, 'left|hand_L': null }
  const target: MarkSnapshot = { 'front|chin': { x: 10, y: 20 }, 'front|knee_L': { x: 5, y: 6 }, 'left|hand_L': null }
  assert.deepEqual(restorePlan(now, target), { moves: [{ view: 'front', joint: 'chin', x: 10, y: 20 }], resets: [] })
})

test('a mark that must go back to the app place (null) is reset, and a placed one is moved', () => {
  const now: MarkSnapshot = { 'left|hand_L': { x: 3, y: 4 }, 'front|chin': { x: 1, y: 1 } }
  const target: MarkSnapshot = { 'left|hand_L': null, 'front|chin': { x: 1, y: 1 } }
  assert.deepEqual(restorePlan(now, target), { moves: [], resets: [{ view: 'left', joint: 'hand_L' }] })
  const back: MarkSnapshot = { 'left|hand_L': { x: 7, y: 8 } }
  assert.deepEqual(restorePlan({ 'left|hand_L': null }, back), { moves: [{ view: 'left', joint: 'hand_L', x: 7, y: 8 }], resets: [] })
})
