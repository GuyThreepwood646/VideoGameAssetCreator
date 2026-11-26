// Small pop-up menus (Light, View): placement that no parent can cut off, and closing that a touch cannot trigger by accident.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { isOutsideClick, popPlacement, SHEET_MAX_WIDTH } from '../src/popMenu'

const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
const box = (left: number, top: number, w: number, h: number) => ({ left, top, right: left + w, bottom: top + h, width: w, height: h })

test('a narrow window gets the bottom sheet, a wider one a fixed placement', () => {
  const pop = { width: 260, height: 228 }
  assert.equal(popPlacement(box(100, 400, 90, 40), pop, { width: 375, height: 812 }), null)
  assert.equal(popPlacement(box(100, 400, 90, 40), pop, { width: SHEET_MAX_WIDTH, height: 800 }), null)
  assert.notEqual(popPlacement(box(100, 400, 90, 40), pop, { width: SHEET_MAX_WIDTH + 1, height: 800 }), null)
})

test('a pop-up under a button near the window bottom opens above it instead of past the edge', () => {
  const pop = { width: 260, height: 228 }
  const p = popPlacement(box(640, 700, 90, 28), pop, { width: 1024, height: 768 })!
  assert.ok(p.top + pop.height <= 768, 'bottom edge inside the window')
  assert.ok(p.top + pop.height <= 700, 'sits above the button')
})

test('a pop-up under a button with room below sits right under it, and stays inside the window sideways', () => {
  const pop = { width: 260, height: 228 }
  const a = popPlacement(box(300, 100, 90, 28), pop, { width: 1024, height: 768 })!
  assert.equal(a.top, 132)
  assert.equal(a.left, 300)
  const b = popPlacement(box(950, 100, 90, 28), pop, { width: 1024, height: 768 })!
  assert.ok(b.left + pop.width <= 1024)
})

test('a click inside the wrapper (the button or the pop-up) never closes the menu, a click outside does', () => {
  const inside = {}
  const wrap = { contains: (n: unknown) => n === inside }
  assert.equal(isOutsideClick(wrap, inside), false)
  assert.equal(isOutsideClick(wrap, {}), true)
  assert.equal(isOutsideClick(null, {}), false)
})

test('the Light and View menus close on a click, not on pointerdown, so a touch scroll or drag leaves them open', () => {
  for (const f of ['../src/components/LightPicker.tsx', '../src/ViewMenu.tsx']) {
    const src = read(f)
    assert.ok(!/addEventListener\(\s*'pointerdown'/.test(src), f + ' must not close on pointerdown')
    assert.ok(/addEventListener\(\s*'click'/.test(src), f + ' closes on click')
    assert.ok(/isOutsideClick/.test(src), f + ' uses the shared outside test')
  }
})

test('the Light menu is fixed to the window at wide widths and its tiles are finger-sized on touch screens', () => {
  const src = read('../src/components/LightPicker.tsx')
  assert.ok(/popPlacement/.test(src))
  const css = read('../src/styles.css')
  assert.match(css, /\.viewer-toolbar \.viewer-light-pop \{[^}]*position: fixed/)
  assert.match(css, /@media \(hover: none\)[^{]*\{[^}]*\.viewer-light-tile \{[^}]*min-height: 44px/)
})

test('on a phone the top bar links have a tap area of at least 24 px and a drop-down never grows wider than the window', () => {
  const css = read('../src/styles.css').split('\r\n').join('\n')
  const narrow = css.slice(css.indexOf('@media (max-width: 700px) {\n  :root {\n    --control-md'))
  assert.match(narrow, /\.topnav a \{[^}]*padding: 5px 0/)
  assert.match(narrow, /(^|\n)  select \{[^}]*max-width: 100%/)
})

test('in a window 880 px wide a button near the right edge gets a pop-up that stays inside the window', () => {
  const pop = { width: 260, height: 228 }
  const p = popPlacement(box(639, 44, 120, 28), pop, { width: 880, height: 1700 })!
  assert.notEqual(p, null)
  assert.ok(p.left + pop.width <= 880 - 12, 'right edge inside the window')
  assert.ok(p.left >= 12)
})
