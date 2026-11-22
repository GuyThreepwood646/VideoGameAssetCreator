import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { attentionItems, facesForward, finishLines, verdictLine, type AttentionItem } from '../src/pipeline/finish'
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
    // A check two steps both report is one item, from the latest step (WP-95, UX-05): versions run in step order here.
    const latest = new Map<string, { stage: string; id: string; passed: boolean }>()
    for (const v of r.versions.filter((x) => x.selected)) for (const c of v.checks) latest.set(c.id, { stage: v.stage, id: c.id, passed: c.passed })
    const failed = [...latest.values()].filter((c) => !c.passed)
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
  // Two of those 13 failed a texture check at step 3 that step 4 measured again and passed: the later value wins (WP-95), so 11.
  assert.equal(hiddenAll, 11)
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

test("a check both step 4 and step 6 report is shown once, with the latest step's value (WP-95)", () => {
  const mk = (stage: string, passed: boolean, value: number) => ({ id: stage + '-v', asset_id: 'a', stage, status: 'done', selected: true, parent_version_id: null, params: {}, files: {}, metrics: { checks: [{ id: 'manifold', name: 'manifold', passed, value, limit: 59, help: `edges ${value}`, tier: 'look' }] } })
  const vs = [mk('prep', false, 80), mk('export', false, 100)]
  const stages: Record<string, unknown[]> = { prep: [vs[0]], export: [vs[1]] }
  const a = { id: 'a', name: 'a', category: 'humanoid', platform_target: 'pc', project_id: 'p', stages, selected: { prep: 'prep-v', export: 'export-v' } } as unknown as Asset
  const items = attentionItems(a)
  assert.equal(items.length, 1)
  assert.equal(items[0].stage, 'export')
  assert.equal(items[0].check.value, 100)
  // The later step passing it removes the item.
  vs[1] = mk('export', true, 10)
  stages.export = [vs[1]]
  assert.equal(attentionItems(a).length, 0)
})

test("the verdict never says the asset failed and counts each tier; the tier is the backend's", () => {
  const item = (tier: 'likely' | 'look' | 'advice') => ({ stage: 'prep', id: 'x', text: '', rank: 0, tier, check: { name: 'x' } }) as unknown as AttentionItem
  assert.equal(verdictLine([]), 'Ready to use. Every check passed.')
  assert.equal(verdictLine([item('likely'), item('look'), item('look'), item('advice')]), 'Ready to use. One thing is likely to show in the game, two are worth a look, one note is advice only.')
  assert.equal(verdictLine([item('advice'), item('advice')]), 'Ready to use. Two notes are advice only.')
  assert.ok(!/fail/i.test(verdictLine([item('likely')])))
})
