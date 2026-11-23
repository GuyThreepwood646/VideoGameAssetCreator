// The new-asset panel and the step screen for objects with moving parts (F15, F16): the panel sends parts only when the
// person typed them (the backend names the moving part from the item), the note says the parts move once marked, and
// the arm tool is shown only on a rig that has a skeleton.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
const panel = read('../src/pages/ProjectPage.tsx')
const stage = read('../src/pipeline/StagePanel.tsx')
const help = read('../src/help.ts')

test('the panel sends parts only when the person typed them', () => {
  assert.match(panel, /\.\.\.\(partsTyped \? \{ parts: parts\.map\(\(name\) => \(\{ name \}\)\) \} : \{\}\)/)
  assert.doesNotMatch(panel, /setParts\(c\.parts_default/)
  assert.doesNotMatch(panel, /setParts\(first\.parts_default/)
})

test('the category note says the moving parts turn or slide once marked, not that it will not animate', () => {
  assert.match(panel, /MOVING_PARTS_CATEGORIES\.includes\(cat\.id\) \? 'no skeleton; its moving parts \(a lid, a door, a wheel\) turn or slide once they are marked in step 5\.'/)
  assert.match(panel, /const MOVING_PARTS_CATEGORIES = \['vehicle', 'prop_articulated'\]/)
})

test('the arm tool is shown only on a rig with a skeleton', () => {
  assert.match(stage, /const hasSkeleton = \(ref\?\.categories\.find\(\(c\) => c\.id === category\)\?\.rig_engine \?\? 'none'\) !== 'none'/)
  assert.match(stage, /\{hasSkeleton && <ShapeTab version=\{version\} onChanged=\{onChanged\} \/>\}/)
})

test('the object help no longer says the lid does not open', () => {
  assert.doesNotMatch(help, /does not open yet/)
})
