import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { SUPPORT_URL, SupportLink } from '../src/SupportLink'

const html = renderToStaticMarkup(createElement(SupportLink))

test('the support link points at the Ko-fi page and says where it goes', () => {
  assert.equal(SUPPORT_URL, 'https://ko-fi.com/guythreepwood646')
  assert.ok(html.includes(`href="${SUPPORT_URL}"`))
  assert.ok(html.includes('title="Opens ko-fi.com in your browser"'))
  assert.ok(html.includes('>Support VGAC<'))
})

test('the support link opens outside the app, without opener or referrer', () => {
  assert.ok(html.includes('target="_blank"'))
  assert.ok(html.includes('rel="noopener noreferrer"'))
})

test('the address is written once in the source and the top bar renders the link', () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
  assert.ok(/<SupportLink\s*\/>/.test(read('../src/App.tsx')))
  assert.ok(read('../src/pages/CreditsPage.tsx').includes('<SupportLink'))
  assert.ok(!read('../src/App.tsx').includes('ko-fi.com'))
  assert.ok(!read('../src/pages/CreditsPage.tsx').includes('ko-fi.com'))
})
