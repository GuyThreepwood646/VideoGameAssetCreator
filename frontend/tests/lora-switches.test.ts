// Your LoRAs per step: the On or Off switch and strength in a step's settings (EditLoras.tsx), the line that says which are
// on for this run, the defaults for a kind of asset (editLoras.loraDefaults, the backend's lora_defaults.defaults_for), and
// the sentence Your library shows under each LoRA (StartOn.tsx).
import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { EditChoice, EditLora } from '../src/api'
import { LIMIT_REACHED, loraDefaults, onForThisRun, startOnSentence } from '../src/editLoras'
import { EditLoras } from '../src/pipeline/EditLoras'

const ink: EditLora = { id: 'mine_ink', label: 'Ink look', stage: 'edit', default_strength: 'subtle', start_on: { categories: ['humanoid'], uses: ['view_addons', 'face_addons'] } }
const soft: EditLora = { id: 'mine_soft', label: 'Soft light', stage: 'edit', default_strength: 'normal', start_on: { categories: ['humanoid', 'prop_rigid'], uses: ['view_addons'] } }
const third: EditLora = { id: 'mine_third', label: 'Third', stage: 'edit', start_on: { categories: ['humanoid'], uses: ['view_addons'] } }
const paint = { id: 'mine_paint', label: 'Paint look', stage: 'concept', default_strength: 'strong', start_on: { categories: ['humanoid'], uses: [] } }

// Every element in a rendered tree (EditLoras has no hooks, so it can be called as a function).
function walk(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) node.forEach((n) => walk(n, out))
  else if (isValidElement(node)) {
    out.push(node)
    walk((node.props as { children?: ReactNode }).children, out)
  }
  return out
}
type Btn = { role?: string; title?: string; 'aria-label'?: string; 'aria-checked'?: boolean; disabled?: boolean; onClick?: () => void; children?: ReactNode }
function render(choices: Record<string, EditChoice>, calls: [string, EditChoice][] = []) {
  const tree = EditLoras({ purpose: 'Side views', entries: [ink, soft, third], choices, startsOn: { mine_ink: 'subtle' }, kindWords: 'humanoid assets', onChange: (id, c) => calls.push([id, c]) })
  return walk(tree).map((e) => e.props as Btn)
}
const switchOf = (btns: Btn[], label: string) => btns.find((b) => b.role === 'switch' && String(b['aria-label']).startsWith(label))!

test('each LoRA has an On or Off switch; turning one on uses its usual strength, turning it off sends off', () => {
  const calls: [string, EditChoice][] = []
  const btns = render({ mine_ink: 'strong' }, calls)
  const inkSwitch = switchOf(btns, 'Ink look')
  const softSwitch = switchOf(btns, 'Soft light')
  assert.equal(inkSwitch['aria-checked'], true)
  assert.equal(softSwitch['aria-checked'], false)
  inkSwitch.onClick!()
  softSwitch.onClick!()
  assert.deepEqual(calls, [['mine_ink', 'off'], ['mine_soft', 'normal']])
})

test('the strength buttons work only while the LoRA is on', () => {
  const calls: [string, EditChoice][] = []
  const btns = render({ mine_ink: 'strong' }, calls)
  const radios = btns.filter((b) => b.role === 'radio')
  assert.equal(radios.length, 9) // three LoRAs, three strengths
  const inkSubtle = radios[0]
  assert.equal(inkSubtle.disabled, false)
  inkSubtle.onClick!()
  assert.deepEqual(calls, [['mine_ink', 'subtle']])
  assert.ok(radios.slice(3).every((r) => r.disabled)) // Soft light and Third are off
  assert.equal(radios.find((r) => r['aria-checked'])?.children, 'Strong')
})

test('with two on, a third cannot be switched on, the step says why, and the two can be switched off', () => {
  const btns = render({ mine_ink: 'normal', mine_soft: 'normal' })
  assert.equal(switchOf(btns, 'Third').disabled, true)
  assert.equal(switchOf(btns, 'Third').title, LIMIT_REACHED)
  assert.equal(switchOf(btns, 'Ink look').disabled, false)
  const html = renderToStaticMarkup(createElement(EditLoras, { purpose: 'Side views', entries: [ink, soft, third], choices: { mine_ink: 'normal', mine_soft: 'strong' }, onChange: () => {} }))
  assert.ok(html.includes('2 are on, the most one step takes. Turn one off to switch another on.'))
  const free = renderToStaticMarkup(createElement(EditLoras, { purpose: 'Side views', entries: [ink, soft, third], choices: { mine_ink: 'normal' }, onChange: () => {} }))
  assert.ok(!free.includes(LIMIT_REACHED))
})

test('the step says in plain words which LoRAs are on for this run, and which start on for this kind of asset', () => {
  assert.equal(onForThisRun([ink, soft], { mine_ink: 'normal', mine_soft: 'off' }), 'On for this run: Ink look (Normal).')
  assert.equal(onForThisRun([ink, soft], {}), 'None of your LoRAs are on for this run.')
  // A LoRA on because of its default says so; one chosen on this asset does not.
  assert.equal(
    onForThisRun([ink, soft], { mine_ink: 'subtle', mine_soft: 'strong' }, ['mine_ink'], 'Humanoid assets'),
    'On for this run: Ink look (Subtle, on by default for Humanoid assets), Soft light (Strong).',
  )
  const html = renderToStaticMarkup(createElement(EditLoras, { purpose: 'Side views', entries: [ink, soft], choices: { mine_ink: 'subtle' }, startsOn: { mine_ink: 'subtle' }, byDefault: ['mine_ink'], kindWords: 'Humanoid assets', onChange: () => {} }))
  assert.ok(html.includes('On for this run: Ink look (Subtle, on by default for Humanoid assets).'))
  assert.ok(html.includes('Starts on for Humanoid assets (set in System, Your library).'))
  assert.equal(html.split('Starts on for').length - 1, 1) // only the one that starts on says so
  assert.ok(!/mine_|_addons/.test(html.replace(/data-[a-z-]+="[^"]*"/g, '')), 'no internal names on screen')
})

test('the defaults for a kind of asset follow each LoRA: its kinds, its steps, its strength, two at most', () => {
  const all = [ink, soft, third, paint]
  assert.deepEqual(loraDefaults(all, 'view_addons', 'humanoid'), { mine_ink: 'subtle', mine_soft: 'normal' })
  assert.deepEqual(loraDefaults(all, 'view_addons', 'prop_rigid'), { mine_soft: 'normal' })
  assert.deepEqual(loraDefaults(all, 'face_addons', 'humanoid'), { mine_ink: 'subtle' })
  assert.deepEqual(loraDefaults(all, 'pose_addons', 'humanoid'), {})
  assert.deepEqual(loraDefaults(all, 'addons', 'humanoid'), { mine_paint: 'strong' }) // a painter LoRA: the first picture
  assert.deepEqual(loraDefaults(all, 'view_addons', 'quadruped'), {})
  assert.deepEqual(loraDefaults(null, 'view_addons', 'humanoid'), {})
})

test('Your library says where each LoRA starts on, in plain words', () => {
  const labels = new Map([['humanoid', 'Humanoid'], ['prop_rigid', 'Rigid prop']])
  assert.equal(startOnSentence(ink, labels), 'Starts on for: Humanoid; in Side views, Face close-up.')
  assert.equal(startOnSentence(soft, labels), 'Starts on for: Humanoid, Rigid prop; in Side views.')
  assert.equal(startOnSentence({ stage: 'concept', start_on: paint.start_on }, labels), 'Starts on for the first picture of: Humanoid.')
  assert.equal(startOnSentence({ stage: 'edit', start_on: { categories: [], uses: [] } }, labels), 'Starts off everywhere: switch it on in a step when you want it.')
})
