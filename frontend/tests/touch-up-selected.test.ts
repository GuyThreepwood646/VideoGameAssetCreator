// The Touch up choices show the one in use: What to paint, and the brush mode (WP-32 review). The selected button is
// the one with aria-checked and the seg-on class, the class the segmented controls style as selected.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { TouchUp } from '../src/pipeline/views/TouchUp'
import { DEFAULT_PAINT, type PaintSettings } from '../src/paint/paintSession'
import type { PaintState } from '../src/paint/usePaint'

function paintWith(settings: PaintSettings): PaintState {
  return {
    settings,
    setSettings: () => {},
    warnings: [],
    blocked: false,
    start: () => {},
    toolFor: () => null,
    undo: () => {},
    redo: () => {},
    resetAll: () => {},
    canUndo: true,
    canRedo: false,
    undoCount: 1,
    undoLabel: 'a shine brush stroke',
    redoLabel: null,
    dirty: true,
    markSaved: () => {},
    notice: null,
    saveMaps: () => null,
    saveable: true,
    maps: { shine: true, metal: true },
  } as unknown as PaintState
}

// The label of the button that is selected inside the group with this aria-label, and whether it has the seg-on class.
function selectedIn(html: string, group: string): { label: string; styled: boolean; count: number } {
  const start = html.indexOf(`aria-label="${group}"`)
  assert.ok(start >= 0, `group ${group} is shown`)
  const end = html.indexOf('</span>', start)
  const part = html.slice(start, end)
  const buttons = [...part.matchAll(/<button[^>]*aria-checked="true"[^>]*>([^<]*)<\/button>/g)]
  const styled = [...part.matchAll(/<button[^>]*aria-checked="true"[^>]*class="([^"]*)"/g)].map((m) => m[1])
  return { label: buttons[0]?.[1] ?? '', styled: styled.some((c) => c.split(' ').includes('seg-on')), count: buttons.length }
}

test('the What to paint choice shows the picture in use, whichever one was picked last', () => {
  for (const [target, label] of [['colour', 'Colour'], ['shine', 'Shine'], ['metal', 'Metal'], ['colour', 'Colour']] as const) {
    const html = renderToStaticMarkup(createElement(TouchUp, { paint: paintWith({ ...DEFAULT_PAINT, target }), painting: true, onStart: () => {}, onStop: () => {}, onSave: () => {}, saving: false, saveNote: null }))
    const sel = selectedIn(html, 'What to paint')
    assert.equal(sel.count, 1, 'exactly one picture is selected')
    assert.equal(sel.label, label, `the selected picture is ${label}`)
    assert.ok(sel.styled, 'the selected picture is drawn as selected')
  }
})

test('the brush mode shows the mode in use', () => {
  for (const mode of ['paint', 'smudge', 'fill', 'erase', 'pick'] as const) {
    const html = renderToStaticMarkup(createElement(TouchUp, { paint: paintWith({ ...DEFAULT_PAINT, mode }), painting: true, onStart: () => {}, onStop: () => {}, onSave: () => {}, saving: false, saveNote: null }))
    const sel = selectedIn(html, 'What the brush does')
    assert.equal(sel.count, 1)
    assert.ok(sel.styled, `mode ${mode} is drawn as selected`)
  }
})
