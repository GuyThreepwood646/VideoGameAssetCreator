import test from 'node:test'
import assert from 'node:assert/strict'
import { api, removeVersionQuestion } from '../src/api'

test('the remove question says only this version goes and names no other version', () => {
  const q = removeVersionQuestion(1, false)
  assert.ok(q.includes('Delete version 1?'))
  assert.ok(q.includes('Only this version is removed'))
  assert.ok(!/deleted too|also deleted|made from it \(/.test(q))
  assert.ok(removeVersionQuestion(2, true).includes('It failed'))
})

test('removing a version sends one plain delete, never with_later', async () => {
  const urls: string[] = []
  const realFetch = globalThis.fetch
  globalThis.fetch = (async (url: string) => {
    urls.push(String(url))
    return new Response(JSON.stringify({ ok: true, deleted: 'v1', deleted_ids: ['v1'] }), { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  try {
    await api.deleteVersion('v1')
  } finally {
    globalThis.fetch = realFetch
  }
  assert.equal(urls.length, 1)
  assert.ok(urls[0].endsWith('/api/versions/v1'))
  assert.ok(!urls[0].includes('with_later'))
})
