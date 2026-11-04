import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin } from 'vite'

const MIME: Record<string, string> = {
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.json': 'application/json',
  '.fbx': 'application/octet-stream',
  '.bin': 'application/octet-stream',
}

// In mock mode the backend is not running, so the dev server serves the
// library folder itself (the sample GLB lives there). Same URL shape as the
// backend: /api/library/{path relative to the library folder}.
function serveLibrary(): Plugin {
  const root = fileURLToPath(new URL('../library', import.meta.url))
  return {
    name: 'vgac-serve-library',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? ''
        if (!url.startsWith('/api/library/')) return next()
        const rel = decodeURIComponent(url.slice('/api/library/'.length).split('?')[0])
        const file = path.resolve(root, rel)
        if (!file.startsWith(root) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
          res.statusCode = 404
          res.end('not found: ' + rel)
          return
        }
        res.setHeader('content-type', MIME[path.extname(file).toLowerCase()] ?? 'application/octet-stream')
        res.setHeader('cache-control', 'no-cache')
        fs.createReadStream(file).pipe(res)
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }
  const mock = env.VITE_MOCK === '1'
  return {
    plugins: [react(), ...(mock ? [serveLibrary()] : [])],
    define: {
      // Keep VITE_MOCK visible to the client even when it came from the shell.
      'import.meta.env.VITE_MOCK': JSON.stringify(mock ? '1' : ''),
    },
    server: {
      port: 5173,
      proxy: mock
        ? undefined
        : {
            '/api': {
              target: 'http://127.0.0.1:8765',
              changeOrigin: false,
              ws: true,
            },
          },
    },
    build: {
      chunkSizeWarningLimit: 1500,
    },
  }
})
