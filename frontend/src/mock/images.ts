// Placeholder images generated at runtime with canvas, returned as PNG data URLs.

export type Figure = 'biped' | 'box'
export type ViewName = 'front' | 'left' | 'back' | 'right' | 'quarter' | 'top'

type Opts = {
  w?: number
  h?: number
  label?: string
  hue?: number
  transparent?: boolean
  figure?: Figure
  view?: ViewName
  seed?: number
  // The head: a helmet (default, as before), hair of a family (hair from the library mock), or bald.
  head?: 'helmet' | 'bald' | HairShape
}

export type HairShape = { family: string; hue: number; light?: number }

function rng(seed: number) {
  let s = seed >>> 0 || 1
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 0xffffffff
  }
}

function canvas(w: number, h: number) {
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d')
  if (!ctx) throw new Error('no 2d context')
  return { c, ctx }
}

function drawBackground(ctx: CanvasRenderingContext2D, w: number, h: number, hue: number) {
  const g = ctx.createLinearGradient(0, 0, 0, h)
  g.addColorStop(0, `hsl(${hue} 18% 26%)`)
  g.addColorStop(1, `hsl(${hue} 14% 14%)`)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, w, h)
  ctx.fillStyle = 'rgba(0,0,0,0.25)'
  ctx.beginPath()
  ctx.ellipse(w / 2, h * 0.88, w * 0.28, h * 0.04, 0, 0, Math.PI * 2)
  ctx.fill()
}

function drawBiped(ctx: CanvasRenderingContext2D, w: number, h: number, hue: number, view: ViewName, rand: () => number, head: Opts['head'] = 'helmet') {
  const cx = w / 2
  const scale = h / 512
  const widthFactor = view === 'left' || view === 'right' ? 0.55 : view === 'quarter' ? 0.8 : 1
  const armour = `hsl(${hue} 35% 48%)`
  const armourDark = `hsl(${hue} 35% 32%)`
  const cloth = `hsl(${(hue + 340) % 360} 60% 42%)`
  const skin = 'hsl(28 50% 70%)'
  const jitter = () => (rand() - 0.5) * 6 * scale

  ctx.lineJoin = 'round'
  // legs
  ctx.fillStyle = armourDark
  const legW = 34 * scale * widthFactor
  ctx.fillRect(cx - legW - 6 * scale * widthFactor, h * 0.56, legW, h * 0.3)
  ctx.fillRect(cx + 6 * scale * widthFactor, h * 0.56, legW, h * 0.3)
  // boots
  ctx.fillStyle = `hsl(${hue} 20% 22%)`
  ctx.fillRect(cx - legW - 10 * scale * widthFactor, h * 0.82, legW + 8 * scale, h * 0.05)
  ctx.fillRect(cx + 2 * scale * widthFactor, h * 0.82, legW + 8 * scale, h * 0.05)
  // torso
  ctx.fillStyle = armour
  const torsoW = 120 * scale * widthFactor
  ctx.beginPath()
  ctx.moveTo(cx - torsoW / 2 + jitter(), h * 0.3)
  ctx.lineTo(cx + torsoW / 2 + jitter(), h * 0.3)
  ctx.lineTo(cx + torsoW / 2.4, h * 0.58)
  ctx.lineTo(cx - torsoW / 2.4, h * 0.58)
  ctx.closePath()
  ctx.fill()
  // tabard
  ctx.fillStyle = cloth
  ctx.fillRect(cx - torsoW * 0.18, h * 0.34, torsoW * 0.36, h * 0.26)
  // arms (A-pose)
  ctx.strokeStyle = armour
  ctx.lineWidth = 26 * scale * Math.max(widthFactor, 0.7)
  ctx.lineCap = 'round'
  if (view !== 'left' && view !== 'right') {
    ctx.beginPath()
    ctx.moveTo(cx - torsoW / 2 + 8 * scale, h * 0.33)
    ctx.lineTo(cx - torsoW / 2 - 40 * scale, h * 0.52)
    ctx.moveTo(cx + torsoW / 2 - 8 * scale, h * 0.33)
    ctx.lineTo(cx + torsoW / 2 + 40 * scale, h * 0.52)
    ctx.stroke()
  } else {
    ctx.beginPath()
    ctx.moveTo(cx, h * 0.33)
    ctx.lineTo(cx + (view === 'left' ? -1 : 1) * 10 * scale, h * 0.52)
    ctx.stroke()
  }
  // shield (front/quarter/left)
  if (view === 'front' || view === 'quarter' || view === 'left') {
    ctx.fillStyle = `hsl(${(hue + 40) % 360} 45% 40%)`
    ctx.beginPath()
    ctx.ellipse(cx - torsoW / 2 - 44 * scale * widthFactor, h * 0.5, 34 * scale * widthFactor + 4, 46 * scale, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.strokeStyle = 'hsl(45 60% 55%)'
    ctx.lineWidth = 4 * scale
    ctx.stroke()
  }
  // sword (front/quarter/right)
  if (view === 'front' || view === 'quarter' || view === 'right') {
    ctx.strokeStyle = 'hsl(210 10% 80%)'
    ctx.lineWidth = 8 * scale
    ctx.beginPath()
    ctx.moveTo(cx + torsoW / 2 + 44 * scale * widthFactor, h * 0.5)
    ctx.lineTo(cx + torsoW / 2 + 44 * scale * widthFactor, h * 0.2)
    ctx.stroke()
    ctx.strokeStyle = 'hsl(45 60% 50%)'
    ctx.lineWidth = 6 * scale
    ctx.beginPath()
    ctx.moveTo(cx + torsoW / 2 + 30 * scale * widthFactor, h * 0.5)
    ctx.lineTo(cx + torsoW / 2 + 58 * scale * widthFactor, h * 0.5)
    ctx.stroke()
  }
  // head
  ctx.fillStyle = view === 'back' ? armour : skin
  ctx.beginPath()
  ctx.arc(cx, h * 0.2, 44 * scale * Math.max(widthFactor, 0.85), 0, Math.PI * 2)
  ctx.fill()
  if (head !== 'helmet') {
    if (head !== 'bald') drawHair(ctx, cx, h * 0.2, 44 * scale * Math.max(widthFactor, 0.85), head, view)
    if (view !== 'back') {
      ctx.fillStyle = 'hsl(0 0% 12%)'
      ctx.fillRect(cx - 18 * scale, h * 0.2 - 4 * scale, 8 * scale, 6 * scale)
      ctx.fillRect(cx + 10 * scale, h * 0.2 - 4 * scale, 8 * scale, 6 * scale)
    }
    return
  }
  // helmet
  ctx.fillStyle = armour
  ctx.beginPath()
  ctx.arc(cx, h * 0.19, 48 * scale * Math.max(widthFactor, 0.85), Math.PI, Math.PI * 2)
  ctx.fill()
  if (view === 'front' || view === 'quarter') {
    ctx.fillStyle = 'hsl(0 0% 10%)'
    ctx.fillRect(cx - 24 * scale, h * 0.2 - 6 * scale, 48 * scale, 8 * scale)
  }
}

// A hairstyle of a family on a head of radius r centred at (cx, cy).
export function drawHair(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number, hair: HairShape, view: ViewName = 'front') {
  ctx.fillStyle = `hsl(${hair.hue} 45% ${hair.light ?? 28}%)`
  const cap = () => {
    ctx.beginPath()
    ctx.arc(cx, cy - r * 0.05, r * 1.08, Math.PI * 1.02, Math.PI * 1.98)
    ctx.closePath()
    ctx.fill()
  }
  const side = view === 'left' ? -1 : view === 'right' ? 1 : 0
  switch (hair.family) {
    case 'bob':
      cap()
      ctx.fillRect(cx - r * 1.08, cy - r * 0.2, r * 0.35, r * 1.05)
      ctx.fillRect(cx + r * 0.73, cy - r * 0.2, r * 0.35, r * 1.05)
      break
    case 'long':
      cap()
      ctx.fillRect(cx - r * 1.1, cy - r * 0.2, r * 0.4, r * 2.6)
      ctx.fillRect(cx + r * 0.7, cy - r * 0.2, r * 0.4, r * 2.6)
      break
    case 'ponytail':
      cap()
      ctx.beginPath()
      ctx.ellipse(cx - side * r * 1.1, cy - r * 0.7, r * 0.28, r * 1.1, side * 0.6, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillRect(cx + (side ? -side * r * 1.2 : r * 0.55) - r * 0.15, cy - r * 0.6, r * 0.3, r * 1.9)
      break
    case 'bun':
      cap()
      ctx.beginPath()
      ctx.arc(cx, cy - r * 1.15, r * 0.42, 0, Math.PI * 2)
      ctx.fill()
      break
    case 'braid':
      cap()
      for (let i = 0; i < 5; i++) {
        ctx.beginPath()
        ctx.arc(cx + r * 0.85, cy + r * (0.2 + i * 0.38), r * 0.2, 0, Math.PI * 2)
        ctx.fill()
      }
      break
    case 'afro':
      ctx.beginPath()
      ctx.arc(cx, cy - r * 0.35, r * 1.45, Math.PI * 0.9, Math.PI * 2.1)
      ctx.closePath()
      ctx.fill()
      break
    default:
      // short
      ctx.beginPath()
      ctx.arc(cx, cy - r * 0.1, r * 1.03, Math.PI * 1.05, Math.PI * 1.95)
      ctx.closePath()
      ctx.fill()
  }
}

// A hairstyle thumbnail: the style on a plain grey head (the catalogue's thumbs).
export function makeHairThumb(hair: HairShape, size = 160): string {
  const { c, ctx } = canvas(size, size)
  ctx.fillStyle = 'hsl(220 6% 30%)'
  ctx.fillRect(0, 0, size, size)
  ctx.fillStyle = 'hsl(0 0% 62%)'
  ctx.beginPath()
  ctx.arc(size / 2, size * 0.5, size * 0.24, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillRect(size * 0.42, size * 0.7, size * 0.16, size * 0.2)
  drawHair(ctx, size / 2, size * 0.5, size * 0.24, hair)
  return c.toDataURL('image/png')
}

function drawBox(ctx: CanvasRenderingContext2D, w: number, h: number, hue: number, view: ViewName) {
  const cx = w / 2
  const cy = h * 0.6
  const bw = (view === 'left' || view === 'right' ? 0.34 : 0.56) * w
  const bh = 0.32 * h
  ctx.fillStyle = `hsl(${hue} 40% 38%)`
  ctx.fillRect(cx - bw / 2, cy - bh / 2, bw, bh)
  ctx.fillStyle = `hsl(${hue} 40% 28%)`
  ctx.beginPath()
  ctx.moveTo(cx - bw / 2, cy - bh / 2)
  ctx.lineTo(cx + bw / 2, cy - bh / 2)
  ctx.lineTo(cx + bw / 2 - 10, cy - bh / 2 - h * 0.1)
  ctx.lineTo(cx - bw / 2 + 10, cy - bh / 2 - h * 0.1)
  ctx.closePath()
  ctx.fill()
  ctx.strokeStyle = 'hsl(45 60% 55%)'
  ctx.lineWidth = 6
  ctx.strokeRect(cx - bw / 2 + 8, cy - bh / 2 + 8, bw - 16, bh - 16)
  ctx.fillStyle = 'hsl(45 60% 55%)'
  ctx.fillRect(cx - 12, cy - bh / 2 - 4, 24, 28)
}

export function makeImage(opts: Opts = {}): string {
  const w = opts.w ?? 512
  const h = opts.h ?? 512
  const hue = opts.hue ?? 210
  const view = opts.view ?? 'front'
  const rand = rng(opts.seed ?? 1)
  const { c, ctx } = canvas(w, h)
  if (!opts.transparent) drawBackground(ctx, w, h, hue)
  if ((opts.figure ?? 'biped') === 'biped') drawBiped(ctx, w, h, hue, view, rand, opts.head)
  else drawBox(ctx, w, h, hue, view)
  if (opts.label) {
    ctx.font = `${Math.round(12 * (h / 256))}px Inter, system-ui, sans-serif`
    ctx.fillStyle = 'rgba(255,255,255,0.75)'
    ctx.textAlign = 'left'
    ctx.fillText(opts.label, 10, h - 10)
  }
  return c.toDataURL('image/png')
}

// Three wall sections side by side on a floor grid, as prep's tiling preview shows scenery. With gapPx the
// sections stand apart, as a piece between grid steps would.
export function makeTiling(hue: number, gapPx = 0, w = 640, h = 240): string {
  const { c, ctx } = canvas(w, h)
  drawBackground(ctx, w, h, hue)
  const floorY = h * 0.82
  const cell = w / 12
  ctx.strokeStyle = 'rgba(255,255,255,0.12)'
  ctx.lineWidth = 1
  for (let x = 0; x <= w; x += cell) {
    ctx.beginPath()
    ctx.moveTo(x, floorY)
    ctx.lineTo(x, h)
    ctx.stroke()
  }
  ctx.beginPath()
  ctx.moveTo(0, floorY)
  ctx.lineTo(w, floorY)
  ctx.stroke()
  const pieceW = cell * 3
  const total = pieceW * 3 + gapPx * 2
  const x0 = (w - total) / 2
  const top = floorY - cell * 3
  for (let i = 0; i < 3; i++) {
    const x = x0 + i * (pieceW + gapPx)
    ctx.fillStyle = `hsl(${(hue + i * 6) % 360} 20% ${40 + i * 4}%)`
    ctx.fillRect(x, top, pieceW, floorY - top)
    ctx.strokeStyle = 'rgba(0,0,0,0.35)'
    for (let r = top + cell / 2; r < floorY; r += cell / 2) {
      ctx.beginPath()
      ctx.moveTo(x, r)
      ctx.lineTo(x + pieceW, r)
      ctx.stroke()
    }
    ctx.strokeStyle = 'rgba(255,255,255,0.5)'
    ctx.strokeRect(x + 0.5, top + 0.5, pieceW - 1, floorY - top - 1)
    ctx.fillStyle = 'hsl(45 80% 60%)'
    ctx.beginPath()
    ctx.arc(x, floorY, 4, 0, Math.PI * 2)
    ctx.fill()
  }
  return c.toDataURL('image/png')
}

export function makeTexture(kind: 'base_color' | 'normal' | 'orm', hue: number, size = 256): string {
  const { c, ctx } = canvas(size, size)
  const rand = rng(hue * 7 + size)
  if (kind === 'base_color') {
    ctx.fillStyle = `hsl(${hue} 35% 45%)`
    ctx.fillRect(0, 0, size, size)
    for (let i = 0; i < 40; i++) {
      ctx.fillStyle = `hsl(${(hue + rand() * 60) % 360} 40% ${30 + rand() * 30}%)`
      ctx.fillRect(rand() * size, rand() * size, rand() * 60, rand() * 60)
    }
  } else if (kind === 'normal') {
    ctx.fillStyle = 'rgb(128,128,255)'
    ctx.fillRect(0, 0, size, size)
    for (let i = 0; i < 40; i++) {
      ctx.fillStyle = `rgb(${100 + rand() * 56},${100 + rand() * 56},${220 + rand() * 35})`
      ctx.fillRect(rand() * size, rand() * size, rand() * 60, rand() * 60)
    }
  } else {
    ctx.fillStyle = 'rgb(255,160,20)'
    ctx.fillRect(0, 0, size, size)
    for (let i = 0; i < 40; i++) {
      ctx.fillStyle = `rgb(${200 + rand() * 55},${100 + rand() * 120},${rand() * 60})`
      ctx.fillRect(rand() * size, rand() * size, rand() * 60, rand() * 60)
    }
  }
  return c.toDataURL('image/png')
}

export function makeBendStrip(hue: number, joints: string[]): string {
  const panel = 200
  const w = panel * joints.length * 2
  const h = 240
  const { c, ctx } = canvas(w, h)
  ctx.fillStyle = 'hsl(0 0% 12%)'
  ctx.fillRect(0, 0, w, h)
  let i = 0
  for (const joint of joints) {
    for (const angle of [35, 90]) {
      const x0 = i * panel
      ctx.strokeStyle = 'hsl(0 0% 25%)'
      ctx.strokeRect(x0 + 0.5, 0.5, panel - 1, h - 1)
      // a limb: upper segment fixed, lower segment bent by angle
      const jx = x0 + panel / 2
      const jy = h * 0.55
      ctx.strokeStyle = `hsl(${hue} 35% 55%)`
      ctx.lineCap = 'round'
      ctx.lineWidth = 26
      ctx.beginPath()
      ctx.moveTo(jx, jy - 80)
      ctx.lineTo(jx, jy)
      const rad = (angle * Math.PI) / 180
      ctx.lineTo(jx + Math.sin(rad) * 80, jy + Math.cos(rad) * 80)
      ctx.stroke()
      ctx.strokeStyle = 'hsl(140 60% 55%)'
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(jx, jy - 80)
      ctx.lineTo(jx, jy)
      ctx.lineTo(jx + Math.sin(rad) * 80, jy + Math.cos(rad) * 80)
      ctx.stroke()
      ctx.fillStyle = 'rgba(255,255,255,0.8)'
      ctx.font = '13px Inter, system-ui, sans-serif'
      ctx.fillText(`${joint} ${angle} deg`, x0 + 10, h - 12)
      i++
    }
  }
  return c.toDataURL('image/png')
}

export function makeSpriteSheet(hue: number, clips: string[], directions = 8, size = 64): string {
  const w = directions * size
  const h = clips.length * size
  const { c, ctx } = canvas(w, h)
  const views: ViewName[] = ['front', 'quarter', 'right', 'quarter', 'back', 'quarter', 'left', 'quarter']
  for (let row = 0; row < clips.length; row++) {
    for (let col = 0; col < directions; col++) {
      ctx.save()
      ctx.translate(col * size, row * size)
      ctx.strokeStyle = 'rgba(255,255,255,0.08)'
      ctx.strokeRect(0.5, 0.5, size - 1, size - 1)
      drawBiped(ctx, size, size, hue, views[col % views.length], rng(row * 31 + col))
      ctx.restore()
    }
  }
  return c.toDataURL('image/png')
}

export function makeSnapshotPlaceholder(label: string, hue: number): string {
  return makeImage({ w: 320, h: 240, label, hue, seed: 9, figure: 'biped', view: 'quarter' })
}
