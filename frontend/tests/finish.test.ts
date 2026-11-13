import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { attentionItems, facesForward, finishLines } from '../src/pipeline/finish'
import type { Asset } from '../src/api'

type Fx = { project: string; asset: string; category: string; versions: { id: string; stage: string; status: string; selected: boolean; checks: { id: string; passed: boolean; severity?: string; help?: string; message?: string }[]; old_card?: unknown }[] }
const runs: Fx[] = JSON.parse(readFileSync(new URL('./finish-runs.json', import.meta.url), 'utf8'))

function toAsset(r: Fx): Asset {
  const versions = r.versions.map((v) => ({ id: v.id, asset_id: r.asset, stage: v.stage, status: v.status, selected: v.selected, parent_version_id: null, params: {}, files: {}, metrics: { checks: v.checks } }))
  const selected: Record<string, string> = {}
  for (const v of versions) if (v.selected) selected[v.stage] = v.id
  const stages: Record<string, unknown[]> = {}
  for (const v of versions) (stages[v.stage] ??= []).push(v)
  return { id: r.asset, name: r.asset, category: r.category, platform_target: 'pc', project_id: 'p', stages, selected } as unknown as Asset
}
const find = (project: string, name: string) => toAsset(runs.find((r) => r.project === project && r.asset === name)!)

// What the card listed before (the ids it could show as a warning).
const OLD_CARD = /^(size_m|height_m|facing|grid_fit|materials_connected|clips_loop|controller_ready|clips|hitbox_cover|hitbox_overreach|collider_present|openings_clear|collider_count|collider_triangles|baked_light|unity_humanoid_avatar|hair_bound)$/

test('every failed check on a kept version is listed; each run before and after', () => {
  const rows: string[] = []
  let hidden = 0
  let hiddenAll = 0
  for (const r of runs) {
    const failed = r.versions.filter((v) => v.selected).flatMap((v) => v.checks.filter((c) => !c.passed).map((c) => ({ stage: v.stage, id: c.id })))
    const before = failed.filter((c) => OLD_CARD.test(c.id) && ['prep', 'rig', 'export'].includes(c.stage)).length
    const items = attentionItems(toAsset(r))
    assert.equal(items.length, failed.length, r.asset)
    for (const i of items) assert.ok(i.text.length > 0)
    if (failed.some((c) => /^(face_matches_picture|metal_mixed|hair_matches_picture|clips_face_forward|facing_tests)$/.test(c.id))) hidden++
    if (failed.some((c) => !OLD_CARD.test(c.id))) hiddenAll++
    rows.push(`${r.project} ${r.asset}: before ${before}, after ${items.length} (${items.map((i) => i.id).join(', ') || 'clean'})`)
  }
  console.log(rows.join('\n'))
  assert.equal(runs.length, 15)
  // 11 runs hid one of the five named face, metal, hair and facing checks; counting steps 1 to 3 as well, 13 hid something.
  assert.equal(hidden, 11)
  assert.equal(hiddenAll, 13)
})

test('the four runs that showed a clean card now show something', () => {
  for (const [p, n] of [['RCT Realistic PC', 'M1-clothed-hard'], ['RCT Realistic PC', 'W2-clothed'], ['RCT Realistic Showcase', 'W1-bikini'], ['RCT2 Realistic PC', 'W2-clothed']]) {
    assert.ok(attentionItems(find(p, n)).length >= 1, `${p} ${n}`)
  }
})

test('M1-shorts does not say its front faces forward', () => {
  const a = find('RCT Realistic PC', 'M1-shorts')
  assert.equal(facesForward(a), false)
  assert.ok(!finishLines(a).some((l) => /facing forward/i.test(l.text)))
  const items = attentionItems(a)
  assert.equal(items[0].id, 'clips_face_forward')
  assert.match(items[0].text, /178 degrees/)
})

test('failed checks on versions that are not kept do not count, and a clean run says nothing is listed', () => {
  const a = find('RCT Realistic PC', 'M1-shorts')
  const clean = toAsset({ project: 'x', asset: 'clean', category: 'humanoid', versions: [
    { id: 'c1', stage: 'concept', status: 'done', selected: true, checks: [{ id: 'in_frame', passed: true }] },
    { id: 'p0', stage: 'prep', status: 'done', selected: false, checks: [{ id: 'face_matches_picture', passed: false, help: 'old' }] },
    { id: 'p1', stage: 'prep', status: 'done', selected: true, checks: [{ id: 'face_matches_picture', passed: true }, { id: 'facing_tests', passed: true }, { id: 'facing', passed: true }] },
  ] })
  assert.equal(attentionItems(clean).length, 0)
  assert.equal(facesForward(clean), true)
  assert.ok(attentionItems(a).length > 0)
})

test('facing_tests failing also stops the facing phrase', () => {
  const a = toAsset({ project: 'x', asset: 'f', category: 'humanoid', versions: [
    { id: 'p1', stage: 'prep', status: 'done', selected: true, checks: [{ id: 'facing', passed: true }, { id: 'facing_tests', passed: false, help: 'The model may face the wrong way.' }] },
  ] })
  assert.equal(facesForward(a), false)
  assert.equal(attentionItems(a)[0].text, 'The model may face the wrong way.')
})
