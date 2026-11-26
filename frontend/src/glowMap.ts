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

// What the viewer shows while a person paints glow: the parent's glow picture, with the Glow brush's edits laid over it.
// `edits` is the brush picture (0 where untouched, 1 + 254 x strength where the brush went; see paintSession.glowByte) and
// `colour` the colour picture; a touched texel glows in the colour under it at the painted strength, as a save does.
// Where the colour was repainted (it differs from `colourOriginal`) over parent glow, the glow keeps its strength and takes
// the new colour, as a save does (paintsave.carry_glow). `parent` is the parent's glow picture bytes (RGBA, same size) or
// null. Writes RGBA into `out`.
const GLOW_FLOOR = 0.02 * 255

export function composeGlow(out: Uint8Array, parent: Uint8Array | null, edits: Uint8Array, colour: Uint8Array, colourOriginal?: Uint8Array): void {
  const n = out.length / 4
  for (let i = 0; i < n; i++) {
    const o = i * 4
    const e = edits[o]
    if (e > 0) {
      const s = (e - 1) / 254
      out[o] = Math.round(colour[o] * s)
      out[o + 1] = Math.round(colour[o + 1] * s)
      out[o + 2] = Math.round(colour[o + 2] * s)
    } else if (parent) {
      const r = parent[o]
      const g = parent[o + 1]
      const b = parent[o + 2]
      const peak = Math.max(r, g, b)
      if (peak > 0 && colourOriginal && (colour[o] !== colourOriginal[o] || colour[o + 1] !== colourOriginal[o + 1] || colour[o + 2] !== colourOriginal[o + 2])) {
        const oldPeak = Math.max(colourOriginal[o], colourOriginal[o + 1], colourOriginal[o + 2])
        if (oldPeak >= GLOW_FLOOR) {
          const gain = Math.min(1, peak / oldPeak)
          out[o] = Math.round(colour[o] * gain)
          out[o + 1] = Math.round(colour[o + 1] * gain)
          out[o + 2] = Math.round(colour[o + 2] * gain)
          out[o + 3] = 255
          continue
        }
      }
      out[o] = r
      out[o + 1] = g
      out[o + 2] = b
    } else {
      out[o] = out[o + 1] = out[o + 2] = 0
    }
    out[o + 3] = 255
  }
}
