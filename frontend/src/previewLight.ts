// Preview light (PHOTOREAL-PLAN phase 1): image-based light and tone mapping for the 3D viewer.
//
// Each choice is an environment the model reflects and is lit by. Studio is three.js RoomEnvironment, built in
// the browser (no file). Outdoors, Overcast and Night are CC0 Poly Haven pictures at 1k in public/hdri (see
// LICENCES.txt there). Flat is the viewer's old three lights with no reflections and no tone mapping, for checking
// the shape.
//
// Colour: glTF colour textures are sRGB and GLTFLoader marks them so; the light pictures are linear; the
// renderer writes sRGB. Tone mapping is Khronos PBR Neutral, which leaves base colours close to how they were
// painted (so a painted look is not washed out) and only rolls off the brightest highlights.
//
// Cost: one environment per viewer per choice, built the first time that choice is shown and kept until the
// viewer closes (dispose()). The downloaded picture is fetched and decoded once per page and shared.
import { createContext } from 'react'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { HDRLoader } from 'three/examples/jsm/loaders/HDRLoader.js'

export type LightId = 'studio' | 'outdoors' | 'overcast' | 'night' | 'flat'

export type LightPreset = {
  id: LightId
  label: string
  // One plain sentence for the tile's tooltip and the help.
  sentence: string
  // The light picture in public/hdri, if any.
  file?: string
  // Picture tile: an image in public/hdri, or a CSS background when there is none.
  tile?: string
  tileCss?: string
  exposure: number
  envIntensity: number
  // Turns the light picture so its sun comes from the front left of the starting camera (radians about up).
  rotationY: number
  background: number
  // A soft directional light added on top (the moon at night), as [colour, intensity].
  extra?: [number, number]
}

const BASE = (import.meta.env.BASE_URL || '/').replace(/\/?$/, '/')
const hdri = (name: string) => `${BASE}hdri/${name}`

export const LIGHTS: LightPreset[] = [
  {
    id: 'studio',
    label: 'Studio',
    sentence: 'Soft light from all round, like a photo studio. Shows every material clearly.',
    tileCss: 'radial-gradient(ellipse at 30% 25%, #ffffff 0 18%, transparent 40%), radial-gradient(ellipse at 78% 30%, #e9edf2 0 12%, transparent 32%), linear-gradient(#9da3ab, #5d636b 70%, #474c53)',
    exposure: 0.85,
    envIntensity: 1,
    rotationY: 0,
    background: 0x24272d,
  },
  {
    id: 'outdoors',
    label: 'Outdoors',
    sentence: 'Sun and blue sky over a field. Metal reflects the sky; how most games light a scene by day.',
    file: hdri('kloofendal_48d_partly_cloudy_1k.hdr'),
    tile: hdri('tile_kloofendal_48d_partly_cloudy.jpg'),
    exposure: 1,
    envIntensity: 1,
    rotationY: 0,
    background: 0x262a31,
  },
  {
    id: 'overcast',
    label: 'Overcast',
    sentence: 'A cloudy day: soft, even light with no hard sun.',
    file: hdri('overcast_soil_1k.hdr'),
    tile: hdri('tile_overcast_soil.jpg'),
    exposure: 1,
    envIntensity: 1,
    rotationY: 0,
    background: 0x26282c,
  },
  {
    id: 'night',
    label: 'Night',
    sentence: 'Starlight and a far-off glow, with a cool moon light so the shape still reads.',
    file: hdri('dikhololo_night_1k.hdr'),
    tile: hdri('tile_dikhololo_night.jpg'),
    exposure: 1,
    envIntensity: 3,
    rotationY: 0,
    background: 0x12151c,
    extra: [0xb4c4ff, 1.6],
  },
  {
    id: 'flat',
    label: 'Flat',
    sentence: 'Plain lights with no reflections, as before. Good for checking the shape.',
    tileCss: 'radial-gradient(circle at 42% 40%, #c9ccd1 0 10%, #8d9199 34%, #5b5f66 36%, transparent 37%), #2b2e34',
    exposure: 1,
    envIntensity: 1,
    rotationY: 0,
    background: 0x24272d,
  },
]

export const lightById = (id: string | null | undefined): LightPreset | undefined => LIGHTS.find((l) => l.id === id)

// The look's id as stored on the asset (style_preset). Photoreal is realistic_pbr (the mock data says realistic).
export function isPhotorealLook(look: string | null | undefined): boolean {
  return look === 'realistic_pbr' || look === 'realistic'
}

export function defaultLightFor(look: string | null | undefined): LightId {
  return isPhotorealLook(look) ? 'outdoors' : 'studio'
}

// The person's last choice is remembered for Photoreal and for the other looks separately, so the Photoreal
// default (Outdoors) and the others' (Studio) keep meaning something.
const STORE_KEY = 'vgac.previewLight'
const family = (look: string | null | undefined) => (isPhotorealLook(look) ? 'photoreal' : 'other')

export function rememberedLight(look: string | null | undefined): LightId {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as Record<string, string>
    const hit = lightById(saved[family(look)])
    if (hit) return hit.id
  } catch {
    // no storage: use the default
  }
  return defaultLightFor(look)
}

export const LIGHT_EVENT = 'vgac-preview-light'

export function rememberLight(look: string | null | undefined, id: LightId) {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as Record<string, string>
    saved[family(look)] = id
    localStorage.setItem(STORE_KEY, JSON.stringify(saved))
  } catch {
    // no storage: the choice lasts for this page only
  }
  // Other viewers on the page showing the same family follow.
  window.dispatchEvent(new CustomEvent(LIGHT_EVENT, { detail: { family: family(look), id } }))
}

export function lightEventApplies(e: Event, look: string | null | undefined): LightId | null {
  const d = (e as CustomEvent<{ family: string; id: LightId }>).detail
  return d && d.family === family(look) && lightById(d.id) ? d.id : null
}

// The asset's look, provided by the asset page so every viewer on it picks the right default light.
export const ViewerLookContext = createContext<string | null>(null)

// ---------------------------------------------------------------------------
// Light pictures: fetched and decoded once per page, uploaded per viewer.
// ---------------------------------------------------------------------------

type Decoded = { width: number; height: number; data: Uint16Array | Float32Array; type: THREE.TextureDataType }
const decoded = new Map<string, Promise<Decoded>>()

function decode(url: string): Promise<Decoded> {
  let p = decoded.get(url)
  if (!p) {
    p = fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.arrayBuffer()
      })
      .then((buf) => {
        const t = new HDRLoader().parse(buf) as unknown as Decoded
        if (!t || !t.data) throw new Error('not a light picture')
        return t
      })
    p.catch(() => decoded.delete(url))
    decoded.set(url, p)
  }
  return p
}

function equirectTexture(d: Decoded): THREE.DataTexture {
  const tex = new THREE.DataTexture(d.data, d.width, d.height, THREE.RGBAFormat, d.type)
  tex.colorSpace = THREE.LinearSRGBColorSpace
  tex.minFilter = THREE.LinearFilter
  tex.magFilter = THREE.LinearFilter
  tex.generateMipmaps = false
  tex.flipY = true
  tex.mapping = THREE.EquirectangularReflectionMapping
  tex.needsUpdate = true
  return tex
}

// ---------------------------------------------------------------------------
// Per-viewer light rig
// ---------------------------------------------------------------------------

export class PreviewLight {
  private renderer: THREE.WebGLRenderer
  private scene: THREE.Scene
  private flatLights: THREE.Object3D[]
  private envs = new Map<LightId, THREE.Texture>()
  private pmrem: THREE.PMREMGenerator | null = null
  private extra: THREE.DirectionalLight
  private token = 0
  private disposed = false
  current: LightId = 'flat'
  // Set when a light picture could not be loaded and Studio light stands in for it.
  fallback: string | null = null

  constructor(renderer: THREE.WebGLRenderer, scene: THREE.Scene, flatLights: THREE.Object3D[]) {
    this.renderer = renderer
    this.scene = scene
    this.flatLights = flatLights
    this.extra = new THREE.DirectionalLight(0xffffff, 0)
    this.extra.position.set(-3, 6, 4)
    this.extra.visible = false
    scene.add(this.extra)
  }

  private gen(): THREE.PMREMGenerator {
    if (!this.pmrem) this.pmrem = new THREE.PMREMGenerator(this.renderer)
    return this.pmrem
  }

  private studio(): THREE.Texture {
    let t = this.envs.get('studio')
    if (!t) {
      const room = new RoomEnvironment()
      t = this.gen().fromScene(room, 0.04).texture
      room.dispose()
      this.envs.set('studio', t)
    }
    return t
  }

  private async envFor(p: LightPreset): Promise<THREE.Texture> {
    if (!p.file) return this.studio()
    const have = this.envs.get(p.id)
    if (have) return have
    const d = await decode(p.file)
    if (this.disposed) throw new Error('closed')
    const have2 = this.envs.get(p.id)
    if (have2) return have2
    const src = equirectTexture(d)
    const t = this.gen().fromEquirectangular(src).texture
    src.dispose()
    this.envs.set(p.id, t)
    return t
  }

  // Shows a choice. Resolves with the fallback note (or null) once the light is in place.
  async apply(id: LightId): Promise<string | null> {
    const p = lightById(id) ?? LIGHTS[0]
    const token = ++this.token
    if (p.id === 'flat') {
      this.set(p, null)
      return null
    }
    let env: THREE.Texture
    let note: string | null = null
    try {
      env = await this.envFor(p)
    } catch (e) {
      if (this.disposed) return null
      note = `Could not load the ${p.label} light picture, so Studio light is shown instead.`
      if (import.meta.env.DEV) console.warn('[viewer] light picture failed', p.file, e)
      env = this.studio()
    }
    if (this.disposed || token !== this.token) return note
    this.set(p, env)
    this.fallback = note
    return note
  }

  private set(p: LightPreset, env: THREE.Texture | null) {
    this.current = p.id
    const flat = !env
    for (const l of this.flatLights) l.visible = flat
    this.scene.environment = env
    this.scene.environmentIntensity = p.envIntensity
    this.scene.environmentRotation.set(0, p.rotationY, 0)
    this.scene.background = new THREE.Color(p.background)
    this.renderer.toneMapping = flat ? THREE.NoToneMapping : THREE.NeutralToneMapping
    this.renderer.toneMappingExposure = flat ? 1 : p.exposure
    this.extra.visible = !flat && !!p.extra
    if (p.extra) {
      this.extra.color.setHex(p.extra[0])
      this.extra.intensity = p.extra[1]
    }
    if (flat) this.fallback = null
  }

  dispose() {
    this.disposed = true
    this.token++
    this.scene.environment = null
    for (const t of this.envs.values()) t.dispose()
    this.envs.clear()
    this.pmrem?.dispose()
    this.pmrem = null
    this.extra.removeFromParent()
    this.extra.dispose()
  }
}

// Overlays (bones, hit boxes, attachment points, grid) keep their exact colours under any light.
export function keepOverlayColours(root: THREE.Object3D) {
  root.traverse((o) => {
    const mat = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined
    if (!mat) return
    for (const m of Array.isArray(mat) ? mat : [mat]) m.toneMapped = false
  })
}
