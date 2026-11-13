// Runs every tests/*.test.ts through vite's module loader (no extra test library needed), with node's own test runner.
import { createServer } from 'vite'
import { readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const server = await createServer({ root: join(here, '..'), logLevel: 'error', server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } })
try {
  for (const f of readdirSync(here).filter((n) => n.endsWith('.test.ts'))) await server.ssrLoadModule('/tests/' + f)
} finally {
  // node:test waits for registered tests, so close once they finish.
  const { after } = await import('node:test')
  after(() => server.close())
}
