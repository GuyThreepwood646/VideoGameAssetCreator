// The stiff region drawn over the model on the rig page (WP-69): the triangles of the region, as a translucent
// skinned copy that follows the same skeleton as the model it covers. Pure geometry; the viewer adds and removes it.
import * as THREE from 'three'

export type RegionHighlight = { mesh: THREE.Mesh; triangles: Uint32Array }

export function buildRegionOverlay(mesh: THREE.Mesh, triangles: Uint32Array): THREE.Object3D | null {
  if (!triangles.length) return null
  const src = mesh.geometry
  const pos = src.getAttribute('position') as THREE.BufferAttribute | undefined
  if (!pos) return null
  // the vertices the triangles use, renumbered from 0
  const remap = new Map<number, number>()
  const used: number[] = []
  for (const v of triangles) {
    if (!remap.has(v)) {
      remap.set(v, used.length)
      used.push(v)
    }
  }
  const geo = new THREE.BufferGeometry()
  const copy = (name: string, size: number) => {
    const a = src.getAttribute(name) as THREE.BufferAttribute | undefined
    if (!a) return
    const out = new Float32Array(used.length * size)
    used.forEach((v, i) => {
      for (let k = 0; k < size; k++) out[i * size + k] = a.getComponent(v, k)
    })
    geo.setAttribute(name, new THREE.BufferAttribute(out, size))
  }
  copy('position', 3)
  copy('normal', 3)
  copy('skinIndex', 4)
  copy('skinWeight', 4)
  geo.setIndex(Array.from(triangles, (v) => remap.get(v) as number))
  const material = new THREE.MeshBasicMaterial({ color: 0xe0443a, transparent: true, opacity: 0.55, depthWrite: false, side: THREE.DoubleSide })
  const sk = mesh as THREE.SkinnedMesh
  if (sk.isSkinnedMesh && sk.skeleton) {
    const over = new THREE.SkinnedMesh(geo, material)
    over.bind(sk.skeleton, sk.bindMatrix)
    over.name = 'stiff-region'
    over.renderOrder = 2
    return over
  }
  const over = new THREE.Mesh(geo, material)
  over.matrixAutoUpdate = false
  over.matrix.copy(mesh.matrix)
  over.name = 'stiff-region'
  over.renderOrder = 2
  return over
}

export function disposeRegionOverlay(o: THREE.Object3D) {
  o.parent?.remove(o)
  const m = o as THREE.Mesh
  m.geometry?.dispose()
  if (Array.isArray(m.material)) m.material.forEach((x) => x.dispose())
  else (m.material as THREE.Material | undefined)?.dispose()
}
