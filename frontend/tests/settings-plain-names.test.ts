// WP-67: the settings forms show plain words, never a setting's internal name (auto_reroll, neutral_mouth, ...) as text.
// The internal name stays in the developer details, which are closed by default and not part of these forms.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Asset, AdjustParam, StageInfo, Version } from '../src/api'
import { AdjustField, FineTune } from '../src/pipeline/FineTune'
import { StepSettingsChoice } from '../src/pipeline/StepSettingsChoice'

// Each setting gets a plain hint, so the test can also check that the form really rendered the setting.
const HINTS: Record<string, string> = {
  auto_reroll: 'Tries again on its own when a check fails.',
  views: 'Which sides of the character are drawn.',
  neutral_mouth: 'Draws a closed, calm mouth.',
  pose_ready: 'Poses the figure ready for the rig.',
  size_m: 'How tall the asset is in the game.',
  source_project: 'Paints the look from your pictures.',
  head_build: 'Builds the head with hair from the library.',
  target_faces: 'More triangles mean more detail.',
  turn_method: 'How the views are turned.',
}
const NAMES = Object.keys(HINTS)

const param = (name: string, extra: Partial<AdjustParam> = {}): AdjustParam =>
  ({ name, type: 'number', default: 1, min: 0, max: 10, label: name.replace(/_/g, ' '), hint: HINTS[name], ...extra }) as AdjustParam
const SCHEMA: AdjustParam[] = [
  param('auto_reroll', { type: 'boolean', default: null }),
  param('views', { type: 'multiselect', default: null, options: ['front', 'back'] }),
  param('neutral_mouth', { type: 'boolean', default: false }),
  param('pose_ready', { type: 'boolean', default: false }),
  param('size_m', { type: 'number', default: 1.8 }),
  param('source_project', { type: 'boolean', default: null }),
  param('head_build', { type: 'boolean', default: null }),
  param('target_faces', { type: 'integer', default: 60000, min: 1000, max: 200000 }),
  param('turn_method', { type: 'select', default: 'standard', options: ['standard', 'sheet'] }),
]
const STAGE = { id: 'mesh', label: 'mesh', adjust_schema: SCHEMA, plain_name: 'Build the 3D model', plain_verb: 'Build', help: {}, estimate_seconds: 1 } as unknown as StageInfo

// Text a person can read: tags and attributes removed.
const visibleText = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

// True when the internal name is shown as text: a name with an underscore as a whole word, any name in brackets (the
// old "(name)" hint). Single plain words such as "views" are ordinary English and are not checked on their own.
const showsName = (text: string, name: string) =>
  name.includes('_') ? new RegExp(`(^|[^A-Za-z0-9_])${name}([^A-Za-z0-9_]|$)`).test(text) : text.includes(`(${name})`)

function assertNoNames(text: string) {
  for (const name of NAMES) assert.equal(showsName(text, name), false, `"${name}" is shown as text`)
}

test('every setting of a step renders with its plain hint and no internal name as text', () => {
  // The same control the step settings form draws for each setting (StepSettingsChoice uses AdjustField).
  for (const p of SCHEMA) {
    const text = visibleText(renderToStaticMarkup(createElement(AdjustField, { param: p, value: p.default, disabled: false, onChange: () => {} })))
    assert.ok(text.includes(HINTS[p.name]), `${p.name} renders its hint`)
    assertNoNames(text)
  }
})

test('the step settings form (Choose each step settings) shows no internal name as text', () => {
  const html = renderToStaticMarkup(
    createElement(StepSettingsChoice, { category: 'humanoid', platform: 'pc', look: 'painted', stages: [STAGE], onChange: () => {} }),
  )
  const text = visibleText(html)
  assert.ok(text.includes('Choose each step'), 'the form renders')
  assertNoNames(text)
})

test('the step fine-tune form shows no internal name as text', () => {
  const asset = { id: 'a1', name: 'Knight', category: 'humanoid', style_preset: '', platform_target: 'pc', stages: {} } as unknown as Asset
  const target = { id: 'v1', asset_id: 'a1', stage: 'mesh', params: {}, metrics: {}, files: {}, status: 'done' } as unknown as Version
  const html = renderToStaticMarkup(
    createElement(FineTune, { stage: 'mesh', schema: SCHEMA, target, asset, refData: null, suggestion: null, disabled: false, onSubmit: () => {} }),
  )
  assertNoNames(visibleText(html))
})
