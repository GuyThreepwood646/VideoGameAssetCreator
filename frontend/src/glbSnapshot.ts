// Renders a small still of a GLB for candidate cards, using one shared offscreen renderer.
import { useEffect, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

const W = 320
const H = 240
let renderer: THREE.WebGLRenderer | null = null
const cache = new Map<string, Promise<string>>()

function getRenderer() {
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, alpha: false })
    renderer.setPixelRatio(1)
    renderer.setSize(W, H, false)
    renderer.outputColorSpace = THREE.SRGBColorSpace
  }
  return renderer
}

// yawDeg turns the model about its vertical axis before the picture (positive turns its front toward its own
// left, as three.js rotation.y does); front puts the camera straight in front (+Z) instead of three-quarter.
export type SnapshotOptions = { yawDeg?: number; front?: boolean }

function render(model: THREE.Object3D, opts: SnapshotOptions): string {
  const r = getRenderer()
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x2a2d33)
  scene.add(new THREE.HemisphereLight(0xffffff, 0x555555, 1.3))
  const key = new THREE.DirectionalLight(0xffffff, 1.5)
  key.position.set(4, 8, 5)
  scene.add(key)
  const pivot = new THREE.Group()
  pivot.add(model)
  pivot.rotation.y = ((opts.yawDeg ?? 0) * Math.PI) / 180
  scene.add(pivot)
  pivot.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(pivot)
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  const camera = new THREE.PerspectiveCamera(40, W / H, 0.01, 1000)
  const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1
  const dist = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.15
  if (opts.front) camera.position.set(center.x, center.y + dist * 0.15, center.z + dist)
  else camera.position.set(center.x + dist * 0.7, center.y + dist * 0.45, center.z + dist * 0.7)
  camera.lookAt(center)
  camera.updateProjectionMatrix()
  r.render(scene, camera)
  const out = r.domElement.toDataURL('image/png')
  pivot.remove(model)
  return out
}

function dispose(model: THREE.Object3D) {
  model.traverse((o) => {
    const m = o as THREE.Mesh
    if (m.isMesh) m.geometry?.dispose()
  })
}

const keyOf = (url: string, opts: SnapshotOptions) => `${url}|${opts.front ? 'front' : 'quarter'}|${opts.yawDeg ?? 0}`

// Several stills of one GLB in one load (the front picker's four turns). Each still is cached like snapshotGlb's.
export function snapshotGlbMany(url: string, list: SnapshotOptions[]): Promise<string>[] {
  const missing = list.filter((o) => !cache.has(keyOf(url, o)))
  if (missing.length) {
    const loaded = new Promise<THREE.Object3D>((resolve, reject) => new GLTFLoader().load(url, (g) => resolve(g.scene), undefined, (e) => reject(e)))
    let left = missing.length
    for (const o of missing) {
      const p = loaded.then((model) => {
        try {
          return render(model, o)
        } finally {
          left -= 1
          if (left === 0) dispose(model)
        }
      })
      cache.set(keyOf(url, o), p)
      p.catch(() => cache.delete(keyOf(url, o)))
    }
  }
  return list.map((o) => cache.get(keyOf(url, o))!)
}

export function snapshotGlb(url: string, opts: SnapshotOptions = {}): Promise<string> {
  return snapshotGlbMany(url, [opts])[0]
}

export function useGlbSnapshot(url: string | undefined, opts: SnapshotOptions = {}): { src: string | null; error: string | null } {
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { yawDeg, front } = opts
  useEffect(() => {
    let alive = true
    setSrc(null)
    setError(null)
    if (!url) return
    snapshotGlb(url, { yawDeg, front }).then(
      (s) => alive && setSrc(s),
      (e) => alive && setError(e instanceof Error ? e.message : String(e)),
    )
    return () => {
      alive = false
    }
  }, [url, yawDeg, front])
  return { src, error }
}

// Stills of one GLB at several turns, loaded once: {yaw: data URL or null while rendering}.
export function useGlbYawSnapshots(url: string | undefined, yaws: number[]): { srcs: Record<number, string | null>; error: string | null } {
  const [srcs, setSrcs] = useState<Record<number, string | null>>({})
  const [error, setError] = useState<string | null>(null)
  const key = yaws.join(',')
  useEffect(() => {
    let alive = true
    setSrcs({})
    setError(null)
    if (!url) return
    const list = key.split(',').map(Number)
    const ps = snapshotGlbMany(
      url,
      list.map((y) => ({ yawDeg: y, front: true })),
    )
    ps.forEach((p, i) =>
      p.then(
        (s) => alive && setSrcs((cur) => ({ ...cur, [list[i]]: s })),
        (e) => alive && setError(e instanceof Error ? e.message : String(e)),
      ),
    )
    return () => {
      alive = false
    }
  }, [url, key])
  return { srcs, error }
}
