import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { LORA_FILES, pickLoraPath } from '../src/desktop'
import { addonRemoveWords, ADDON_BROWSER_COPY, ADDON_CHOOSE_TEXT } from '../src/help'

test('the desktop bridge call asks the native picker for a LoRA file and returns the real path', async () => {
  const calls: Array<[string, string | undefined]> = []
  const host = { pickFile: async (title: string, filter?: string) => (calls.push([title, filter]), 'D:\\Downloads\\ink.safetensors') }
  assert.equal(await pickLoraPath(host), 'D:\\Downloads\\ink.safetensors')
  assert.deepEqual(calls, [['Choose your LoRA file', LORA_FILES]])
  assert.ok(LORA_FILES.includes('*.safetensors'))
})

test('a cancelled picker gives null and a browser (no bridge) gives undefined', async () => {
  assert.equal(await pickLoraPath({ pickFile: async () => null }), null)
  assert.equal(await pickLoraPath(null), undefined)
})

test('removal wording says what really happens to the file, by how it is kept', () => {
  assert.ok(addonRemoveWords('in_place').includes('not touched'))
  assert.ok(addonRemoveWords('link').includes('link to your file is removed'))
  assert.ok(addonRemoveWords('copy').includes('copy the app made is deleted'))
  assert.ok(addonRemoveWords('link').includes('original file is not touched'))
  assert.ok(addonRemoveWords('copy').includes('original file is not touched'))
  assert.ok(addonRemoveWords('').includes('stays in the LoRA folder'))
})

test('the browser path says copy and the desktop path says where it is, linked, or copied after asking', () => {
  assert.ok(ADDON_BROWSER_COPY.startsWith('A copy'))
  assert.ok(ADDON_CHOOSE_TEXT.browser.includes('makes a copy'))
  const d = ADDON_CHOOSE_TEXT.desktop
  assert.ok(d.includes('where it is') && d.includes('links it') && d.includes('asked before a copy'))
})

test('the add form uses the picker only when the bridge exists and offers Copy or Cancel for a file on another drive', () => {
  const src = readFileSync(new URL('../src/pipeline/AddMyAddon.tsx', import.meta.url), 'utf8')
  assert.ok(src.includes('hostPick() ? <ChoosePath'))
  assert.ok(src.includes("route === 'copy_needed'"))
  assert.ok(src.includes('Copy ${fmtBytes') && src.includes('Cancel'))
  assert.ok(!/symlink|junction/i.test(src))
})
