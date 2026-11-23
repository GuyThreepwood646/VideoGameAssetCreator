// Phone-width windows: the app must fit a 360 px wide window (a phone reaching the PC by remote desktop).
// These checks read the stylesheet and the launcher's window settings; the layout itself is checked in a browser.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
const FLOOR = 360

type Rule = { selector: string; body: string; narrow: boolean }

// Splits the stylesheet into rules, marking those inside a max-width media query that applies at 360 px.
function rules(css: string): Rule[] {
  const out: Rule[] = []
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  let i = 0
  const walk = (end: number, narrow: boolean) => {
    while (i < end) {
      const open = text.indexOf('{', i)
      if (open < 0 || open >= end) { i = end; return }
      const head = text.slice(i, open).trim()
      if (head.startsWith('@')) {
        const m = /max-width:\s*(\d+)px/.exec(head)
        let depth = 1
        let j = open + 1
        while (depth > 0 && j < text.length) { if (text[j] === '{') depth++; else if (text[j] === '}') depth--; j++ }
        i = open + 1
        walk(j - 1, narrow || (head.startsWith('@media') && m !== null && Number(m[1]) >= FLOOR))
        i = j
      } else {
        const close = text.indexOf('}', open)
        out.push({ selector: head, body: text.slice(open + 1, close), narrow })
        i = close + 1
      }
    }
  }
  walk(text.length, false)
  return out
}

const css = read('../src/styles.css')
const all = rules(css)
const narrowSelectors = new Set(all.filter((r) => r.narrow).flatMap((r) => r.selector.split(',').map((s) => s.trim())))
const px = (body: string, prop: string) => [...body.matchAll(new RegExp(`(?:^|;|\\s)${prop}:\\s*(\\d+)px`, 'g'))].map((m) => Number(m[1]))

test('the stylesheet has phone-width breakpoints at 700 px and 420 px', () => {
  assert.match(css, /@media \(max-width: 700px\)/)
  assert.match(css, /@media \(max-width: 420px\)/)
})

test('no rule outside a narrow query sets a min-width above 360 px', () => {
  const wide = all.filter((r) => !r.narrow && px(r.body, 'min-width').some((v) => v > FLOOR))
  assert.deepEqual(wide.map((r) => r.selector), [])
})

test('every fixed width above 360 px has a max-width or a narrow-window override', () => {
  const bad = all
    .filter((r) => !r.narrow && px(r.body, 'width').some((v) => v > FLOOR))
    .filter((r) => !/max-width/.test(r.body))
    .filter((r) => !r.selector.split(',').every((s) => narrowSelectors.has(s.trim())))
  assert.deepEqual(bad.map((r) => r.selector), [])
})

test('grids with fixed columns wider than a phone stack in a narrow window', () => {
  const fixedSum = (body: string) => {
    const m = /grid-template-columns:\s*([^;]+)/.exec(body)
    if (!m || /auto-(fill|fit)/.test(m[1])) return 0
    return [...m[1].matchAll(/(\d+)px/g)].reduce((a, x) => a + Number(x[1]), 0)
  }
  const bad = all
    .filter((r) => !r.narrow && fixedSum(r.body) > 240)
    .filter((r) => !r.selector.split(',').every((s) => narrowSelectors.has(s.trim())))
  assert.deepEqual(bad.map((r) => r.selector), [])
})

test('both launcher windows can be made phone-narrow', () => {
  const cfg = JSON.parse(read('../../desktop/config.json'))
  assert.ok(cfg.window.minWidth <= FLOOR, `minWidth ${cfg.window.minWidth}`)
  assert.match(read('../../desktop/main.js'), /minWidth: w\.minWidth \|\| 360\b/)
  const starter = /MinimumSize = new System\.Drawing\.Size\((\d+), \d+\)/.exec(read('../../starter/src/WebWindow.cs'))
  assert.ok(starter && Number(starter[1]) <= FLOOR, `VGAC.exe app window minimum ${starter?.[1]}`)
})
