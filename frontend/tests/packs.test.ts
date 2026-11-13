import test from 'node:test'
import assert from 'node:assert/strict'
import { arrivalNote, canStart, dependencyNote, downloadBytes, enoughSpace, fmtSize, neededSources, progressWords } from '../src/packsText'
import type { PackKit } from '../src/api'

const MB = 1024 ** 2
const kit = (over: Partial<PackKit> = {}): PackKit => ({
  id: 'hair', label: 'Hair library', summary: '', download_bytes: 0, free_bytes: 50 * 1024 * MB, cleanable_bytes: 0,
  run: { state: 'idle', phase: '', text: '', fraction: 0, error: null, waiting_for: [], choices: [], bytes_done: 0, bytes_total: 0, seconds: 0 },
  sources: [
    { id: 'sys', name: 'System pack', size: 280 * MB, page: 'p', direct: true, needs: [], what: '', click_text: '', state: 'missing' },
    { id: 'h01', name: 'hair01 pack', size: 227 * MB, page: 'p', direct: true, needs: ['sys'], what: '', click_text: '', state: 'missing' },
    { id: 'qua', name: 'Quaternius pack', size: 129 * MB, page: 'p', direct: false, needs: [], what: '', click_text: 'Click.', state: 'missing' },
  ],
  choices: [
    { id: 'c_sys', name: 'System styles', what: '', styles: 7, sources: ['sys'], requires: [], built: false },
    { id: 'c_h01', name: 'hair01 styles', what: '', styles: 11, sources: ['h01'], requires: ['System pack'], built: false },
    { id: 'c_qua', name: 'Quaternius styles', what: '', styles: 5, sources: ['qua'], requires: [], built: false },
  ],
  ...over,
})

test('sizes are written in plain units', () => {
  assert.equal(fmtSize(640 * MB), '640 MB')
  assert.equal(fmtSize(1.5 * 1024 * MB), '1.5 GB')
  assert.equal(fmtSize(10), '1 MB')
})

test('a pack that needs another brings it along, once', () => {
  const k = kit()
  assert.deepEqual(neededSources(k, ['c_h01']).map((s) => s.id), ['sys', 'h01'])
  assert.deepEqual(neededSources(k, ['c_h01', 'c_sys']).map((s) => s.id), ['sys', 'h01'])
  assert.equal(downloadBytes(k, ['c_h01']), (280 + 227) * MB)
  assert.equal(dependencyNote(k.choices[1]), 'Also needs System pack, which the app fetches with it.')
  assert.equal(dependencyNote(k.choices[0]), '')
})

test('downloaded or unpacked packs are not downloaded again', () => {
  const k = kit()
  k.sources[0].state = 'unpacked'
  assert.equal(downloadBytes(k, ['c_h01']), 227 * MB)
})

test('the itch.io pack is said to be a click-through that the person handles', () => {
  const k = kit()
  const note = arrivalNote(neededSources(k, ['c_sys', 'c_qua']))
  assert.match(note, /The app downloads System pack/)
  assert.match(note, /Quaternius pack .* is on itch\.io, which only hands a file over after a click, so you download that one yourself/)
})

test('start needs a choice and is off while a run is working; too little space is noticed', () => {
  const k = kit()
  assert.equal(canStart(k, []), false)
  assert.equal(canStart(k, ['c_sys']), true)
  k.run.state = 'working'
  assert.equal(canStart(k, ['c_sys']), false)
  assert.equal(enoughSpace(kit({ free_bytes: 100 * MB }), ['c_sys']), false)
  assert.equal(enoughSpace(kit(), ['c_sys', 'c_h01', 'c_qua']), true)
})

test('progress words show bytes while downloading', () => {
  const k = kit()
  k.run = { ...k.run, state: 'working', phase: 'download', text: 'Downloading System pack', bytes_done: 100 * MB, bytes_total: 400 * MB }
  assert.equal(progressWords(k), 'Downloading System pack: 100 MB of 400 MB')
  k.run = { ...k.run, phase: 'build', text: 'Building the 3D hairstyles in Blender (3 of 26)' }
  assert.equal(progressWords(k), 'Building the 3D hairstyles in Blender (3 of 26)')
})
