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

export function snapshotGlb(url: string): Promise<string> {
  const hit = cache.get(url)
  if (hit) return hit
  const p = new Promise<string>((resolve, reject) => {
    new GLTFLoader().load(
      url,
      (gltf) => {
        try {
          const r = getRenderer()
          const scene = new THREE.Scene()
          scene.background = new THREE.Color(0x2a2d33)
          scene.add(new THREE.HemisphereLight(0xffffff, 0x555555, 1.3))
          const key = new THREE.DirectionalLight(0xffffff, 1.5)
          key.position.set(4, 8, 5)
          scene.add(key)
          const model = gltf.scene
          scene.add(model)
          const box = new THREE.Box3().setFromObject(model)
          const size = box.getSize(new THREE.Vector3())
          const center = box.getCenter(new THREE.Vector3())
          const camera = new THREE.PerspectiveCamera(40, W / H, 0.01, 1000)
          const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1
          const dist = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.15
          camera.position.set(center.x + dist * 0.7, center.y + dist * 0.45, center.z + dist * 0.7)
          camera.lookAt(center)
          camera.updateProjectionMatrix()
          r.render(scene, camera)
          resolve(r.domElement.toDataURL('image/png'))
          model.traverse((o) => {
            const m = o as THREE.Mesh
            if (m.isMesh) m.geometry?.dispose()
          })
        } catch (e) {
          reject(e)
        }
      },
      undefined,
      (err) => reject(err),
    )
  })
  cache.set(url, p)
  p.catch(() => cache.delete(url))
  return p
}

export function useGlbSnapshot(url: string | undefined): { src: string | null; error: string | null } {
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    setSrc(null)
    setError(null)
    if (!url) return
    snapshotGlb(url).then(
      (s) => alive && setSrc(s),
      (e) => alive && setError(e instanceof Error ? e.message : String(e)),
    )
    return () => {
      alive = false
    }
  }, [url])
  return { src, error }
}
