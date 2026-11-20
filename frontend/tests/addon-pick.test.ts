import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { addonRemoveWords, ADDON_CHOOSE_TEXT } from '../src/help'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('removal wording says the file is not touched and stays in its LoRA folder', () => {
  assert.ok(addonRemoveWords('in_place').includes('not touched'))
  assert.ok(addonRemoveWords('in_place').includes('stays in its LoRA folder'))
  assert.ok(addonRemoveWords('').includes('stays where it is'))
})

test('the choose text says the LoRA is used where it is and nothing is copied', () => {
  for (const text of [ADDON_CHOOSE_TEXT.desktop, ADDON_CHOOSE_TEXT.browser]) {
    assert.ok(text.includes('LoRA folders'))
    assert.ok(text.includes('stays where it is') && text.includes('nothing is copied'))
    assert.ok(!/copy of|makes a copy|copied only|Copy\b/.test(text))
  }
})

test('the add form lists the LoRA files in the linked folders and never copies, links or uploads one', () => {
  const src = read('../src/pipeline/AddMyAddon.tsx')
  assert.ok(src.includes('api.availableAddons()'))
  assert.ok(src.includes("how: `Used where it is, in ${f.folder}."))
  for (const gone of ['pickLoraPath', 'placeAddon', 'uploadAddon', 'copy_needed', 'unregisteredAddons', 'openAddonFolder', 'hostPick']) {
    assert.ok(!src.includes(gone), `AddMyAddon still uses ${gone}`)
  }
  assert.ok(!/symlink|junction|hard link|copyFile|copy_needed/i.test(src))
})

test('the LoRA folder list adds and removes folders and asks the folder picker only in the desktop app', () => {
  const src = read('../src/components/LoraFolders.tsx')
  assert.ok(src.includes('api.addLoraFolder(p)') && src.includes('api.removeLoraFolder(f.path)'))
  assert.ok(src.includes('host.pickFolder(') && src.includes('if (!host) return'))
  assert.ok(src.includes('ComfyUI reads these folders when it starts.'))
  assert.ok(!/symlink|junction/i.test(src))
})

test('the setup page and the ComfyUI install step both offer the LoRA folders', () => {
  assert.ok(read('../src/SetupCheck.tsx').includes('<LoraFolders'))
  assert.ok(read('../src/components/ComfyInstall.tsx').includes('Add your LoRA folders (optional)'))
})
