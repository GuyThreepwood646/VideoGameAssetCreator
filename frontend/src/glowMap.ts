// The glow map (emission.png) on the viewer's model. The clean-up step's preview model carries no emissive of its own (only the
// export file does), so the viewer lays the version's glow picture on the model's materials: the lit parts then show in every
// light and stand out in the dark one.
import * as THREE from 'three'

// A glow picture is laid out like the colour picture (same texture coordinates), so it is set up the way glTF textures are:
// not flipped, sRGB.
export function prepareGlowTexture(tex: THREE.Texture, like?: THREE.Texture | null): THREE.Texture {
  tex.flipY = false
  tex.colorSpace = THREE.SRGBColorSpace
  tex.wrapS = like?.wrapS ?? THREE.RepeatWrapping
  tex.wrapT = like?.wrapT ?? THREE.RepeatWrapping
  if (like) tex.channel = like.channel
  tex.needsUpdate = true
  return tex
}

// Puts the glow picture on every material that can take it; returns how many. A material with no colour picture of its own
// (nothing to line the glow up with) is left alone.
export function applyGlow(materials: Iterable<THREE.Material | THREE.Material[]>, tex: THREE.Texture): number {
  let n = 0
  for (const entry of materials) {
    for (const m of Array.isArray(entry) ? entry : [entry]) {
      const std = m as THREE.MeshStandardMaterial
      if (!std.isMeshStandardMaterial || !std.map) continue
      std.emissiveMap = prepareGlowTexture(tex, std.map)
      std.emissive.set(0xffffff)
      std.emissiveIntensity = 1
      std.needsUpdate = true
      n++
    }
  }
  return n
}
