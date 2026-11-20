import test from 'node:test'
import assert from 'node:assert/strict'
import { markKey, restorePlan, snapshotMarks, stateRestorePlan, type MarkSnapshot, type MarkState } from '../src/pipeline/views/marksHistory'
import type { BodyMarks } from '../src/api'

function marks(spec: Record<string, Record<string, { x: number | null; y: number | null; source?: 'app' | 'you' }>>): BodyMarks {
  const out: Record<string, Record<string, unknown>> = {}
  for (const [view, joints] of Object.entries(spec)) {
    out[view] = {}
    for (const [joint, p] of Object.entries(joints)) out[view][joint] = { x: p.x, y: p.y, source: p.source ?? 'app', sure: true, guessed: false, stale: false, seen: p.x !== null }
  }
  return { marks: out } as unknown as BodyMarks
}

// The app's own place of each mark (what a reset gives back), and a model of what the marks PUT and reset do.
type Model = { auto: Record<string, { x: number; y: number } | null>; now: MarkSnapshot }
function apply(model: Model, plan: ReturnType<typeof restorePlan>): MarkSnapshot {
  const next = { ...model.now }
  for (const m of plan.moves) next[markKey(m.view, m.joint)] = { x: m.x, y: m.y, source: 'you' }
  for (const r of plan.resets) {
    const k = markKey(r.view, r.joint)
    const a = model.auto[k]
    next[k] = a ? { x: a.x, y: a.y, source: 'app' } : null
  }
  return next
}

test('a snapshot keeps every mark place and who placed it; an unseen mark with no place is null', () => {
  const snap = snapshotMarks(marks({ front: { chin: { x: 10, y: 20 } }, left: { hand_L: { x: null, y: null } }, back: { knee_R: { x: 3, y: 4, source: 'you' } } }))
  assert.deepEqual(snap, { 'front|chin': { x: 10, y: 20, source: 'app' }, 'left|hand_L': null, 'back|knee_R': { x: 3, y: 4, source: 'you' } })
  assert.equal(markKey('front', 'chin'), 'front|chin')
})

test('undoing a move of an app mark gives back the app mark: the restore is a reset, and the source is the app again', () => {
  const auto = { 'front|chin': { x: 10, y: 20 } }
  const before: MarkSnapshot = { 'front|chin': { x: 10, y: 20, source: 'app' } }
  const moved: MarkSnapshot = { 'front|chin': { x: 15, y: 20, source: 'you' } }
  const plan = restorePlan(moved, before)
  assert.deepEqual(plan, { moves: [], resets: [{ view: 'front', joint: 'chin' }] })
  const back = apply({ auto, now: moved }, plan)
  assert.deepEqual(back, before)
})

test('undoing a person move restores the person mark by move, and only what differs is sent', () => {
  const now: MarkSnapshot = { 'front|chin': { x: 12, y: 20, source: 'you' }, 'front|knee_L': { x: 5, y: 6, source: 'app' } }
  const target: MarkSnapshot = { 'front|chin': { x: 10, y: 20, source: 'you' }, 'front|knee_L': { x: 5, y: 6, source: 'app' } }
  assert.deepEqual(restorePlan(now, target), { moves: [{ view: 'front', joint: 'chin', x: 10, y: 20 }], resets: [] })
})

test('a mark that must go back to the app place (null, an unseen app mark) is reset', () => {
  const now: MarkSnapshot = { 'left|hand_L': { x: 3, y: 4, source: 'you' }, 'front|chin': { x: 1, y: 1, source: 'app' } }
  const target: MarkSnapshot = { 'left|hand_L': null, 'front|chin': { x: 1, y: 1, source: 'app' } }
  assert.deepEqual(restorePlan(now, target), { moves: [], resets: [{ view: 'left', joint: 'hand_L' }] })
})

test('one step with mixed sources ("put back the app\'s marks") restores each mark the right way in one plan', () => {
  // Before the step: the app's chin, and the person's knee (moved from the app's place) and elbow (placed by the person, unseen by the app).
  const auto = { 'front|chin': { x: 10, y: 20 }, 'front|knee_L': { x: 30, y: 40 }, 'left|elbow_R': null }
  const before: MarkSnapshot = { 'front|chin': { x: 10, y: 20, source: 'app' }, 'front|knee_L': { x: 33, y: 40, source: 'you' }, 'left|elbow_R': { x: 7, y: 8, source: 'you' } }
  // The step: put back the app's marks, which resets all of them.
  const now: MarkSnapshot = { 'front|chin': { x: 10, y: 20, source: 'app' }, 'front|knee_L': { x: 30, y: 40, source: 'app' }, 'left|elbow_R': null }
  const plan = restorePlan(now, before)
  assert.deepEqual(plan.moves, [{ view: 'front', joint: 'knee_L', x: 33, y: 40 }, { view: 'left', joint: 'elbow_R', x: 7, y: 8 }])
  assert.deepEqual(plan.resets, [])
  assert.deepEqual(apply({ auto, now }, plan), before)
})

test('the app mark that was reset and the person mark that was dropped come back together after a put-back', () => {
  const auto = { 'front|chin': { x: 10, y: 20 }, 'front|knee_L': { x: 30, y: 40 } }
  const before: MarkSnapshot = { 'front|chin': { x: 12, y: 20, source: 'you' }, 'front|knee_L': { x: 30, y: 40, source: 'app' } }
  const now: MarkSnapshot = { 'front|chin': { x: 10, y: 20, source: 'app' }, 'front|knee_L': { x: 30, y: 40, source: 'app' } }
  const plan = restorePlan(now, before)
  assert.deepEqual(plan, { moves: [{ view: 'front', joint: 'chin', x: 12, y: 20 }], resets: [] })
  assert.deepEqual(apply({ auto, now }, plan), before)
})

test('a mark already exactly as it was is left alone', () => {
  const same: MarkSnapshot = { 'front|chin': { x: 10, y: 20, source: 'app' } }
  assert.deepEqual(restorePlan(same, same), { moves: [], resets: [] })
})

test('a person move, then undo after "These look right": the accept is kept (the flag is not touched)', () => {
  const before: MarkState = { marks: { 'front|knee_L': { x: 30, y: 40, source: 'app' } }, accepted: true }
  const afterMove: MarkState = { marks: { 'front|knee_L': { x: 33, y: 40, source: 'you' } }, accepted: true }
  const plan = stateRestorePlan(afterMove, before)
  assert.deepEqual(plan.moves, [])
  assert.deepEqual(plan.resets, [{ view: 'front', joint: 'knee_L' }])
  assert.equal(plan.accepted, true, 'the reset clears the accept, so it is set again after the put-back')
})

test('undo of "These look right" clears the flag and changes no marks', () => {
  const before: MarkState = { marks: { 'front|knee_L': { x: 30, y: 40, source: 'app' } }, accepted: false }
  const accepted: MarkState = { marks: { 'front|knee_L': { x: 30, y: 40, source: 'app' } }, accepted: true }
  assert.deepEqual(stateRestorePlan(accepted, before), { moves: [], resets: [], accepted: false })
})

test('redo of "These look right" sets the flag again', () => {
  const before: MarkState = { marks: {}, accepted: false }
  const accepted: MarkState = { marks: {}, accepted: true }
  assert.deepEqual(stateRestorePlan(before, accepted), { moves: [], resets: [], accepted: true })
})

test('an undo of "Put back the app\'s marks" brings back the person marks and the accept they had', () => {
  const before: MarkState = { marks: { 'front|chin': { x: 12, y: 20, source: 'you' } }, accepted: true }
  const putBack: MarkState = { marks: { 'front|chin': { x: 10, y: 20, source: 'app' } }, accepted: false }
  assert.deepEqual(stateRestorePlan(putBack, before), { moves: [{ view: 'front', joint: 'chin', x: 12, y: 20 }], resets: [], accepted: true })
})

test('a step with no change to the flag and no mark change makes no calls', () => {
  const s: MarkState = { marks: { 'front|chin': { x: 10, y: 20, source: 'app' } }, accepted: true }
  assert.deepEqual(stateRestorePlan(s, s), { moves: [], resets: [], accepted: null })
})
