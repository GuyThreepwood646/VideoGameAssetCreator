// Mock backend: a fetch interceptor for /api/* plus a fake WebSocket for /api/ws/events.
// Enabled when VITE_MOCK=1. Library files that the mock generates are data URLs
// registered through setLibraryResolver; everything else under /api/library is
// served by the Vite dev server from ../library (see vite.config.ts).
import { GPU_KINDS, setLibraryResolver, STAGES, TURNAROUND_VIEWS, type Asset, type Gpu, type GpuKind, type AutopilotState, type Check, type GalleryItem, type Job, type ServerEvent, type StageId, type Version } from '../api'
import { categories, fabricate, files, lookPresets, newVersion, nowIso, platforms, recipeFor, seedStore, setSelected, setupReport, stages, store, styles, uid } from './data'
import type { AddonEntry, AddonPrefill, StrengthName, UnregisteredAddon } from '../api'
import { mockAddItem, mockApplied, mockDetect, mockFit, mockItemsOnCreate, mockNudge, mockPanel, mockRemove, mockUpdate, PLACES } from './items'
import { addMyHair, changeMyHair, hairCatalogue, hairPanel, myHairListing, OwnHairError, removeMyHair } from './hair'
import { mockCredits } from './credits'
import { addedOf, changeMoves, characterMoves, moveLibrary, mockReadMoveFile, myMoveFiles, removedOf } from './moves'

type Handler = (m: RegExpMatchArray, body: Record<string, unknown>, query: URLSearchParams) => unknown
type Route = { method: string; re: RegExp; handler: Handler }

class HttpError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const sockets = new Set<MockSocket>()

function broadcast(ev: ServerEvent) {
  const data = JSON.stringify(ev)
  for (const s of sockets) s.push(data)
}

class MockSocket extends EventTarget {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  readyState = 0
  url: string
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null

  constructor(url: string) {
    super()
    this.url = url
    sockets.add(this)
    window.setTimeout(() => {
      if (this.readyState !== 0) return
      this.readyState = 1
      const e = new Event('open')
      this.onopen?.(e)
      this.dispatchEvent(e)
    }, 30)
  }

  push(data: string) {
    if (this.readyState !== 1) return
    const e = new MessageEvent('message', { data })
    this.onmessage?.(e)
    this.dispatchEvent(e)
  }

  send() {
    // the server ignores client messages
  }

  close() {
    sockets.delete(this)
    this.readyState = 3
    const e = new Event('close')
    this.onclose?.(e)
    this.dispatchEvent(e)
  }
}

const findProject = (id: string) => {
  const p = store.projects.find((x) => x.id === id)
  if (!p) throw new HttpError(404, 'project not found')
  return p
}
const findAsset = (id: string) => {
  const a = store.assets.find((x) => x.id === id)
  if (!a) throw new HttpError(404, 'asset not found')
  return a
}
const findVersion = (id: string): { asset: Asset; version: Version } => {
  for (const asset of store.assets) {
    for (const list of Object.values(asset.stages)) {
      const version = list?.find((v) => v.id === id)
      if (version) return { asset, version }
    }
  }
  throw new HttpError(404, 'version not found')
}
const findJob = (id: string) => {
  const j = store.jobs.find((x) => x.id === id)
  if (!j) throw new HttpError(404, 'job not found')
  return j
}

const INSTANCE: Record<StageId, Job['instance']> = { concept: 'b', turnaround: 'b', mesh: 'a', prep: 'host', rig: 'b', export: 'host' }
const DURATION: Record<StageId, number> = { concept: 4000, turnaround: 6000, mesh: 8000, prep: 5000, rig: 6000, export: 5000 }
const MESSAGES: Record<StageId, string[]> = {
  concept: ['loading model', 'sampling', 'cutting out subject', 'scoring readiness'],
  turnaround: ['loading Qwen-Image-Edit', 'rendering views', 'BiRefNet cutouts', 'consistency checks'],
  mesh: ['loading TRELLIS.2', 'sparse structure', 'structured latent', 'mesh extraction', 'texture voxel'],
  prep: ['remesh', 'part segmentation', 'quad remesh', 'LOD chain', 'UV unwrap', 'baking', 'quality gate'],
  rig: ['decimating for rigger', 'predicting joints', 'skinning', 'bend test', 'limb separation'],
  export: ['assembling prefab', 'FBX export', 'GLB export', 'manifest', 'Unity batch validation'],
}

const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T

let simulationSpeed = 1

// Graphics cards: on or off, what each may do, free after each step. Routing as the backend's jobs.route.
type MockEngine = 'running' | 'starting' | 'stopped' | 'failed'
type MockGpu = { enabled: boolean; allowed: GpuKind[]; free_after_job: boolean; engine: MockEngine; owned: boolean; idle: number; last_error: string }
const GPU_INFO: Record<'a' | 'b', { gpu: string; url: string; possible: GpuKind[]; vram_total_mb: number; note: string }> = {
  a: { gpu: 'RTX 3060', url: 'http://127.0.0.1:8188', possible: ['pictures', 'model', 'review'], vram_total_mb: 12288, note: 'The other card can take over everything this card does.' },
  b: { gpu: 'RTX 3080', url: 'http://127.0.0.1:8189', possible: [...GPU_KINDS], vram_total_mb: 10240, note: 'Rigging only runs on this card.' },
}
const gpuState: Record<'a' | 'b', MockGpu> = {
  // a is run by ComfyUI Desktop (running, not the app's); b is started by the app when a step needs it.
  a: { enabled: true, allowed: [...GPU_KINDS], free_after_job: false, engine: 'running', owned: false, idle: 0, last_error: '' },
  b: { enabled: true, allowed: [...GPU_KINDS], free_after_job: false, engine: 'stopped', owned: false, idle: 0, last_error: '' },
}
// Shared card settings (PUT /api/gpus): the default card (null: each step's usual card) and sharing when busy.
const gpuSharing: { default_instance: 'a' | 'b' | null; share_when_busy: boolean } = { default_instance: null, share_when_busy: false }

// On-demand start, as the backend's instances.py: "starting" for a few seconds, then running and owned by the app.
// A prompt containing "nostart" makes the start fail, to show the Try again path.
function startEngine(name: 'a' | 'b', fail = false) {
  const st = gpuState[name]
  if (!st.enabled || st.engine === 'starting' || st.engine === 'running') return
  st.engine = 'starting'
  st.owned = true
  st.last_error = ''
  broadcast({ type: 'gpus', gpus: gpuList() })
  window.setTimeout(() => {
    if (st.engine !== 'starting') return
    if (fail) {
      st.engine = 'failed'
      st.last_error = 'it did not answer within 180 seconds (see comfy-' + name + '.err.log in runtime/logs)'
    } else {
      st.engine = 'running'
    }
    broadcast({ type: 'gpus', gpus: gpuList() })
  }, 3000 / simulationSpeed)
}

function kindOfStage(stage: StageId, category: string): GpuKind | null {
  if (stage === 'prep' || stage === 'export') return null
  if (category === 'material') return stage === 'rig' ? null : 'pictures'
  return stage === 'mesh' ? 'model' : stage === 'rig' ? 'rigging' : 'pictures'
}

function mayRun(inst: 'a' | 'b', k: GpuKind): boolean {
  const g = gpuState[inst]
  return g.enabled && g.allowed.includes(k) && GPU_INFO[inst].possible.includes(k)
}

function mockRoute(stage: StageId, category: string): { instance: Job['instance']; message: string } {
  const preferred: Job['instance'] = category === 'material' && stage === 'mesh' ? 'b' : category === 'material' && stage === 'rig' ? 'host' : INSTANCE[stage]
  const k = kindOfStage(stage, category)
  if (preferred === 'host' || k === null) return { instance: preferred, message: 'queued' }
  if (mayRun(preferred, k)) return { instance: preferred, message: 'queued' }
  const other = preferred === 'a' ? 'b' : 'a'
  if (mayRun(other, k)) return { instance: other, message: 'queued' }
  const message = gpuState[preferred].enabled
    ? `Waiting for a graphics card that is allowed to do ${k === 'model' ? '3D models' : k}`
    : `Waiting for ${GPU_INFO[preferred].gpu} to be turned back on`
  return { instance: preferred, message }
}

function jobCanStart(job: Pick<Job, 'instance'>, stage: StageId, category: string): boolean {
  if (job.instance === 'host') return true
  const k = kindOfStage(stage, category)
  return k === null || mayRun(job.instance, k)
}

function gpuList(): Gpu[] {
  return (['a', 'b'] as const).map((name) => {
    const st = gpuState[name]
    const info = GPU_INFO[name]
    const running = store.jobs.find((j) => j.instance === name && j.status === 'running')
    const asset = running ? store.assets.find((a) => a.id === running.asset_id) : undefined
    const [stage, op] = running ? running.kind.split('.').slice(-2) : ['', '']
    const used = !st.enabled ? 600 : running ? Math.round(info.vram_total_mb * 0.8) : 1400
    const reachable = st.engine === 'running'
    const state = !st.enabled ? 'off' : st.engine
    return {
      instance: name,
      gpu: info.gpu,
      url: info.url,
      enabled: st.enabled,
      reachable,
      vram_total_mb: reachable ? info.vram_total_mb : null,
      vram_free_mb: reachable ? info.vram_total_mb - used : null,
      current_job_id: running?.id ?? null,
      current_job: running
        ? { id: running.id, kind: running.kind, stage, op, material: running.kind.startsWith('material.'), asset_id: running.asset_id, asset_name: asset?.name ?? null, progress: running.progress, message: running.message }
        : null,
      queued: store.jobs.filter((j) => j.instance === name && j.status === 'queued').length,
      can_run: [],
      note: reachable && !st.owned ? `${info.note} ${name === 'a' ? 'Started by ComfyUI Desktop' : 'Started outside the app'}; the app leaves it running.` : info.note,
      allowed_kinds: GPU_KINDS.filter((k) => st.allowed.includes(k)),
      possible_kinds: info.possible,
      effective_kinds: info.possible.filter((k) => st.allowed.includes(k)),
      kind_notes: name === 'a' ? { rigging: "Rigging only runs on the RTX 3080's instance." } : {},
      free_after_job: st.free_after_job,
      state,
      owned: st.owned && st.engine !== 'stopped',
      can_start: true,
      stop_when_idle_minutes: st.idle,
      last_error: state === 'failed' ? st.last_error : '',
      is_default: gpuSharing.default_instance === name,
      share_when_busy: gpuSharing.share_when_busy,
    }
  })
}

// Queued jobs on a card that may not run them now move to the other card when it may, else wait with a message.
function rebalanceGpus() {
  for (const job of store.jobs) {
    if (job.status !== 'queued' || job.instance === 'host') continue
    const asset = store.assets.find((a) => a.id === job.asset_id)
    const stage = job.kind.split('.').slice(-2)[0] as StageId
    if (!asset || !STAGES.includes(stage)) continue
    if (jobCanStart(job, stage, asset.category)) {
      if (job.message.startsWith('Waiting for ')) {
        job.message = 'queued'
        broadcast({ type: 'job', job: clone(job) })
      }
      continue
    }
    const r = mockRoute(stage, asset.category)
    if (r.instance !== job.instance && jobCanStart(r, stage, asset.category)) {
      job.message = `Moved to ${GPU_INFO[r.instance as 'a' | 'b'].gpu} while ${GPU_INFO[job.instance].gpu} is off`
      job.instance = r.instance
    } else {
      job.message = r.message
    }
    broadcast({ type: 'job', job: clone(job) })
  }
}

function startJob(asset: Asset, version: Version, kind: string): Job {
  const placed = mockRoute(version.stage, asset.category)
  const job: Job = {
    id: uid('j'),
    instance: placed.instance,
    kind,
    asset_id: asset.id,
    version_id: version.id,
    status: 'queued',
    progress: 0,
    message: placed.message,
    created_at: nowIso(),
    started_at: null,
    finished_at: null,
    error: null,
  }
  version.job_id = job.id
  store.jobs.push(job)
  broadcast({ type: 'job', job: clone(job) })

  const total = DURATION[version.stage] / simulationSpeed
  const steps = MESSAGES[version.stage]
  const stepMs = total / (steps.length + 1)
  let i = 0
  const queueDelay = 600 + Math.random() * 800
  const begin = () => {
    if (job.status === 'cancelled') return
    // A card that is off (or may not do this kind of work) holds the job until that changes.
    if (!jobCanStart(job, version.stage, asset.category)) {
      window.setTimeout(begin, 1000)
      return
    }
    // The card's engine is not running: start it and wait, the job stays queued with the starting message.
    if (job.instance !== 'host' && gpuState[job.instance].engine !== 'running') {
      const inst = job.instance
      const st = gpuState[inst]
      if (st.engine === 'stopped') {
        const fail = typeof version.params.prompt_override === 'string' && version.params.prompt_override.includes('nostart')
        startEngine(inst, fail)
      }
      const msg =
        st.engine === 'failed'
          ? `Could not start the graphics engine on ${GPU_INFO[inst].gpu}: ${st.last_error}. It will try again when you press Try again or turn the card off and on.`
          : `Starting the graphics engine on ${GPU_INFO[inst].gpu} (about a minute)`
      if (job.message !== msg) {
        job.message = msg
        broadcast({ type: 'job', job: clone(job) })
      }
      window.setTimeout(begin, 1000)
      return
    }
    job.status = 'running'
    job.started_at = nowIso()
    job.message = steps[0]
    version.status = 'running'
    broadcast({ type: 'job', job: clone(job) })
    const timer = window.setInterval(() => {
      if (job.status === 'cancelled') {
        window.clearInterval(timer)
        return
      }
      i += 1
      job.progress = Math.min(0.98, i / (steps.length + 1))
      job.message = steps[Math.min(i, steps.length - 1)]
      if (i <= steps.length) {
        broadcast({ type: 'job', job: clone(job) })
        return
      }
      window.clearInterval(timer)
      const fail = typeof version.params.prompt_override === 'string' && version.params.prompt_override.includes('fail')
      if (fail) {
        job.status = 'error'
        job.error = 'simulated failure (prompt_override contains "fail")'
        job.message = 'failed'
        version.status = 'error'
        version.error = job.error
      } else {
        fabricate(asset, version)
        version.status = 'done'
        job.status = 'done'
        job.progress = 1
        job.message = 'finished'
        // A bald-pictures-only redraw (Hair from the library turned on later) is kept at once.
        if (!asset.selected[version.stage] || version.params.bald_only === true) setSelected(asset, version)
      }
      job.finished_at = nowIso()
      broadcast({ type: 'job', job: clone(job) })
      broadcast({ type: 'version', version: clone(version) })
    }, stepMs)
  }
  window.setTimeout(begin, queueDelay)
  return job
}

function stageParamsDefault(stage: StageId): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of stages.find((s) => s.id === stage)?.adjust_schema ?? []) if (p.default !== undefined) out[p.name] = p.default
  return out
}

const routes: Route[] = []
const on = (method: string, pattern: string, handler: Handler) =>
  routes.push({ method, re: new RegExp('^' + pattern.replace(/:(\w+)/g, '(?<$1>[^/]+)') + '/?$'), handler })

on('GET', '/api/health', () => ({
  ok: true,
  version: '0.2.0-mock',
  instances: {
    a: { url: 'http://127.0.0.1:8188', gpu: 'RTX 3060', role: '3D', reachable: gpuState.a.engine === 'running', enabled: gpuState.a.enabled, comfyui_version: '0.3.60', devices: [{ name: 'NVIDIA GeForce RTX 3060', type: 'cuda', vram_total: 12_884_901_888, vram_free: 9_126_805_504 }] },
    b: { url: 'http://127.0.0.1:8189', gpu: 'RTX 3080', role: 'images, rigging', reachable: gpuState.b.engine === 'running', enabled: gpuState.b.enabled, comfyui_version: '0.3.60', devices: [{ name: 'NVIDIA GeForce RTX 3080', type: 'cuda', vram_total: 10_737_418_240, vram_free: 4_294_967_296 }] },
  },
  host: { ram_total_bytes: 68_719_476_736, ram_free_bytes: 30_064_771_072, ram_free_gb: 28.0, min_free_ram_gb: 8, cpu_percent: 17 },
  queue: {
    a: { paused: false, current_job_id: store.jobs.find((j) => j.instance === 'a' && j.status === 'running')?.id ?? null },
    b: { paused: false, current_job_id: store.jobs.find((j) => j.instance === 'b' && j.status === 'running')?.id ?? null },
  },
}))
on('GET', '/api/gpus', () => gpuList())
on('GET', '/api/setup', () => setupReport())
on('PUT', '/api/gpus', (_m, body) => {
  if ('default_instance' in body) {
    const d = body.default_instance
    if (d !== null && d !== 'a' && d !== 'b') throw new HttpError(404, `unknown graphics card '${String(d)}'`)
    gpuSharing.default_instance = d as 'a' | 'b' | null
  }
  if (typeof body.share_when_busy === 'boolean') gpuSharing.share_when_busy = body.share_when_busy
  const list = gpuList()
  broadcast({ type: 'gpus', gpus: list })
  return list
})
on('PUT', '/api/gpus/:instance', (m, body) => {
  const name = m.groups!.instance
  if (name !== 'a' && name !== 'b') throw new HttpError(404, `unknown graphics card '${name}'`)
  const st = gpuState[name]
  if (typeof body.enabled === 'boolean') st.enabled = body.enabled
  if (Array.isArray(body.allowed_kinds)) {
    const bad = body.allowed_kinds.filter((k) => !GPU_KINDS.includes(k as GpuKind))
    if (bad.length) throw new HttpError(400, `unknown kind(s) ${bad.join(', ')}`)
    st.allowed = body.allowed_kinds as GpuKind[]
  }
  if (typeof body.free_after_job === 'boolean') st.free_after_job = body.free_after_job
  if (typeof body.stop_when_idle_minutes === 'number') {
    if (body.stop_when_idle_minutes < 0 || body.stop_when_idle_minutes > 1440) throw new HttpError(422, 'stop_when_idle_minutes must be between 0 and 1440')
    st.idle = body.stop_when_idle_minutes
  }
  if (typeof body.enabled === 'boolean') {
    // Off and on again forgets a failed start; off also shuts down an engine the app started.
    if (st.engine === 'failed') st.engine = 'stopped'
    if (!body.enabled && st.owned) {
      st.engine = 'stopped'
      st.owned = false
    }
  }
  rebalanceGpus()
  const list = gpuList()
  broadcast({ type: 'gpus', gpus: list })
  return list
})
on('POST', '/api/gpus/:instance/start', (m) => {
  const name = m.groups!.instance
  if (name !== 'a' && name !== 'b') throw new HttpError(404, `unknown graphics card '${name}'`)
  const st = gpuState[name]
  if (!st.enabled) throw new HttpError(409, `${GPU_INFO[name].gpu} is turned off. Turn it on first; the app never starts a card that is off.`)
  if (st.engine === 'failed') st.engine = 'stopped'
  startEngine(name)
  return gpuList()
})
on('POST', '/api/gpus/:instance/stop', (m) => {
  const name = m.groups!.instance
  if (name !== 'a' && name !== 'b') throw new HttpError(404, `unknown graphics card '${name}'`)
  const st = gpuState[name]
  if (!st.owned || st.engine === 'stopped')
    throw new HttpError(409, `The graphics engine on ${GPU_INFO[name].gpu} was not started by the app, so the app will not stop it.`)
  if (store.jobs.some((j) => j.instance === name && j.status === 'running'))
    throw new HttpError(409, `${GPU_INFO[name].gpu} is busy with a step. Wait for it to finish, or turn the card off to hand the step to another card.`)
  st.engine = 'stopped'
  st.owned = false
  const list = gpuList()
  broadcast({ type: 'gpus', gpus: list })
  return list
})
on('GET', '/api/categories', () => categories)
on('GET', '/api/styles', () => styles)
on('GET', '/api/platforms', () => platforms)
on('GET', '/api/stages', () => stages)
on('GET', '/api/models', () => [
  { id: 'zturbo', label: 'Z-Image Turbo', description: 'fast painted look', default_steps: 8, licence: 'Apache-2.0' },
  { id: 'zbase', label: 'Z-Image Base', description: 'higher quality, slower', default_steps: 30, licence: 'Apache-2.0' },
  { id: 'klein', label: 'FLUX.2 Klein 4B', description: 'quick drafts', default_steps: 4, licence: 'Apache-2.0' },
  { id: 'anima', label: 'Anima Turbo', description: 'anime line art', default_steps: 10, licence: 'outputs commercial' },
  { id: 'pixel', label: 'Z-Image Pixel Art', description: 'pixel art', default_steps: 8, licence: 'Apache-2.0' },
  { id: 'chroma', label: 'Chroma1-HD', description: 'loose painterly', default_steps: 30, licence: 'Apache-2.0' },
])
on('GET', '/api/style-presets', () => lookPresets)
on('GET', '/api/credits', () => mockCredits)
on('GET', '/api/suggest', (_m, _b, query) => {
  const category = query.get('category') ?? 'humanoid'
  const style = query.get('style') ?? styles[0].id
  const platform = query.get('platform') ?? 'pc_mid'
  const preset = styles.find((s) => s.id === style) ?? styles[0]
  const model = preset.model
  const labels: Record<string, string> = { zturbo: 'Z-Image Turbo', zbase: 'Z-Image Base', klein: 'FLUX.2 Klein 4B', anima: 'Anima Turbo', pixel: 'Z-Image Pixel Art', chroma: 'Chroma1-HD' }
  const reasons: Record<string, string> = {
    zturbo: 'it gives the fastest painted look',
    zbase: 'it gives the highest quality for a realistic look',
    klein: 'it is quickest for drafts and toon looks',
    anima: 'it draws clean anime line art',
    pixel: 'it draws crisp pixel art',
    chroma: 'it gives a loose painterly look',
  }
  const cat = categories.find((c) => c.id === category)
  const budgets = cat?.budgets[platform] ?? { triangles: 20000, texture: 2048 }
  const slow = platform === 'high' ? 1.6 : platform === 'mobile_low' ? 0.8 : 1
  const estimates = { concept: model === 'zbase' ? 80 : 25, turnaround: 110, mesh: Math.round(200 * slow), prep: 70, rig: cat?.rig_engine === 'none' ? 10 : 95, export: 60 }
  const recipe_plain = recipeFor({ style_preset: style, category, platform_target: platform as Asset['platform_target'] }).plain
  return { model, model_label: labels[model] ?? model, reason: `${reasons[model] ?? 'it fits this style'} for ${preset.label.toLowerCase()} on ${platform.replace('_', ' ')}`, budgets, estimates, recipe_plain }
})

on('GET', '/api/projects', () =>
  store.projects.map((p) => {
    const assets = store.assets.filter((a) => a.project_id === p.id).sort((a, b) => b.created_at.localeCompare(a.created_at))
    const covers: string[] = []
    const times: string[] = [p.created_at]
    for (const a of assets) {
      times.push(a.created_at)
      for (const list of Object.values(a.stages)) for (const v of list ?? []) times.push(v.created_at)
      const kept = a.stages.concept?.find((v) => v.id === a.selected.concept)
      const pic = kept?.files.cutout ?? kept?.files.image
      if (pic && covers.length < 4) covers.push(pic)
    }
    return { ...p, asset_count: assets.length, covers, last_activity: times.sort().at(-1) }
  }),
)
on('POST', '/api/projects', (_m, body) => {
  const name = String(body.name ?? '').trim()
  if (!name) throw new HttpError(422, 'name is required')
  const sl = (body.style_lock as Record<string, unknown> | undefined) ?? {}
  const p = {
    id: uid('p'),
    name,
    created_at: nowIso(),
    style_lock: {
      prompt_block: String(sl.prompt_block ?? ''),
      model: (sl.model as 'zturbo') ?? 'zturbo',
      loras: (sl.loras as string[]) ?? [],
      palette: (sl.palette as string[]) ?? [],
      negative: String(sl.negative ?? ''),
      preset: typeof sl.preset === 'string' ? sl.preset : null,
    },
    platform_target: (body.platform_target as 'pc_mid') ?? 'pc_mid',
  }
  store.projects.push(p)
  return p
})
on('GET', '/api/projects/:id', (m) => findProject(m.groups!.id))
on('PUT', '/api/projects/:id', (m, body) => {
  const p = findProject(m.groups!.id)
  if (typeof body.name === 'string') p.name = body.name
  if (body.platform_target) p.platform_target = body.platform_target as typeof p.platform_target
  if (body.style_lock && typeof body.style_lock === 'object') Object.assign(p.style_lock, body.style_lock)
  return p
})
on('DELETE', '/api/projects/:id', (m) => {
  const p = findProject(m.groups!.id)
  store.projects = store.projects.filter((x) => x !== p)
  store.assets = store.assets.filter((a) => a.project_id !== p.id)
  return { ok: true }
})

on('GET', '/api/projects/:pid/assets', (m) => {
  findProject(m.groups!.pid)
  return store.assets.filter((a) => a.project_id === m.groups!.pid)
})
on('POST', '/api/projects/:pid/assets', (m, body) => {
  const p = findProject(m.groups!.pid)
  const name = String(body.name ?? '').trim()
  if (!name) throw new HttpError(422, 'name is required')
  if (!body.category) throw new HttpError(422, 'category is required')
  // As the backend: a look other than the project's is refused unless it was chosen on purpose.
  const want = p.style_lock.preset
  if (want && body.style_preset !== want && body.different_look !== true) {
    throw new HttpError(400, `This project's look is ${want}, but this asset was sent with the look ${String(body.style_preset)}. Use ${want} for the asset, or choose "Use a different look for this asset" to make it on purpose.`)
  }
  const a: Asset = {
    id: uid('a'),
    project_id: p.id,
    name,
    category: String(body.category),
    style_preset: String(body.style_preset ?? styles[0].id),
    platform_target: (body.platform_target as Asset['platform_target']) ?? p.platform_target,
    prompt: String(body.prompt ?? ''),
    parts: (body.parts as string[]) ?? [],
    created_at: nowIso(),
    concept_model: typeof body.concept_model === 'string' && body.concept_model ? body.concept_model : null,
    ...(body.hair === 'library' ? { hair: { source: 'library' as const, pick: null, changed_by: 'form' } } : {}),
    selected: {},
    stages: {},
  }
  if (typeof body.image === 'string') checkPicture(body.image) // a bad picture makes no asset, like the backend
  store.assets.push(a)
  mockItemsOnCreate(a, body.items) // carried items set aside (items.ts)
  if (typeof body.image === 'string') return { ...a, upload: uploadConcept(a, body.image, null) }
  return a
})
on('GET', '/api/assets/:id', (m) => findAsset(m.groups!.id))
on('POST', '/api/assets/:id/variants', (m, body) => {
  const base = findAsset(m.groups!.id)
  const change = String(body.change ?? '').trim()
  if (!change) throw new HttpError(422, 'change is required')
  const concept = (base.stages.concept ?? []).find((v) => v.id === base.selected.concept || v.selected)
  if (!concept || concept.status !== 'done') throw new HttpError(409, 'the base asset needs a kept concept first')
  const a: Asset = {
    id: uid('a'),
    project_id: base.project_id,
    name: String(body.name ?? '').trim() || `${base.name} - ${change}`,
    category: base.category,
    style_preset: base.style_preset,
    platform_target: base.platform_target,
    prompt: `${base.prompt}, ${change}`,
    parts: [...base.parts],
    created_at: nowIso(),
    base_asset_id: base.id,
    variant_change: change,
    ...(base.hair ? { hair: { ...base.hair } } : {}),
    selected: {},
    stages: {},
  }
  store.assets.push(a)
  // Copy of the base concept, already selected, then a refine with the change.
  const copy = newVersion(a, 'concept', null, { ...concept.params, copied_from: concept.id })
  copy.status = 'done'
  copy.files = { ...concept.files }
  copy.metrics = { ...concept.metrics }
  setSelected(a, copy)
  const params: Record<string, unknown> = { ...concept.params, instruction: change, seed: Math.floor(Math.random() * 100000) }
  const v = newVersion(a, 'concept', copy.id, params)
  const job = startJob(a, v, 'concept.refine')
  return { asset: clone(a), job: clone(job), version: clone(v) }
})
on('PUT', '/api/assets/:id', (m, body) => {
  const a = findAsset(m.groups!.id)
  for (const k of ['name', 'prompt', 'category', 'platform_target'] as const) if (typeof body[k] === 'string') (a as Record<string, unknown>)[k] = body[k]
  if (Array.isArray(body.parts)) a.parts = body.parts as string[]
  return a
})
on('DELETE', '/api/assets/:id', (m) => {
  const a = findAsset(m.groups!.id)
  store.assets = store.assets.filter((x) => x !== a)
  return { ok: true }
})

on('POST', '/api/assets/:id/stages/:stage/run', (m, body) => {
  const a = findAsset(m.groups!.id)
  const stage = m.groups!.stage as StageId
  if (!STAGES.includes(stage)) throw new HttpError(404, 'unknown stage')
  const idx = STAGES.indexOf(stage)
  let parent: string | null = null
  if (idx > 0) {
    parent = (body.parent_version_id as string | undefined) ?? a.selected[STAGES[idx - 1]] ?? null
    if (!parent) throw new HttpError(409, `select a ${STAGES[idx - 1]} version first`)
  }
  const count = Math.max(1, Math.min(stage === 'concept' ? 4 : 1, Number(body.count ?? 1)))
  const results = []
  for (let i = 0; i < count; i++) {
    const params = { ...stageParamsDefault(stage), ...((body.params as Record<string, unknown>) ?? {}) }
    // Like the backend: the painter chosen on the creation form is the default for concept runs.
    if (stage === 'concept' && a.concept_model && !((body.params as Record<string, unknown> | undefined)?.model)) params.model = a.concept_model
    if (!('seed' in ((body.params as Record<string, unknown>) ?? {}))) params.seed = Math.floor(Math.random() * 100000)
    // Your own side and back views: checked, taken out of the params, stored on the version before it runs.
    const provided = stage === 'turnaround' ? (params.provided_views as Record<string, string> | undefined) : undefined
    delete params.provided_views
    if (provided) {
      for (const [view, img] of Object.entries(provided)) checkPicture(img, `${view} view picture`)
      params.supplied_views = Object.keys(provided).sort()
    }
    const v = newVersion(a, stage, parent, params)
    if (provided) for (const [view, img] of Object.entries(provided)) v.files[`supplied_${view}`] = putUpload(v, `supplied_${view}`, img)
    const job = startJob(a, v, `${stage}.run`)
    results.push({ job: clone(job), version: clone(v) })
  }
  return count === 1 ? results[0] : results
})
on('DELETE', '/api/versions/:vid', (m) => {
  const { asset, version } = findVersion(m.groups!.vid)
  if (version.status !== 'error' && version.status !== 'rejected') throw new HttpError(409, 'only a version that failed, was stopped or was rejected can be removed')
  if (Object.values(asset.stages).some((list) => list?.some((v) => v.parent_version_id === version.id))) throw new HttpError(409, 'other versions were made from this one, so it stays')
  for (const [stage, list] of Object.entries(asset.stages)) {
    if (list?.includes(version)) asset.stages[stage as StageId] = list.filter((v) => v !== version)
  }
  store.jobs = store.jobs.filter((j) => j.version_id !== version.id)
  return { ok: true, deleted: version.id }
})
on('POST', '/api/versions/:vid/refine', (m, body) => {
  const { asset, version } = findVersion(m.groups!.vid)
  const instruction = String(body.instruction ?? '').trim()
  if (!instruction) throw new HttpError(422, 'instruction is required')
  const params: Record<string, unknown> = { ...version.params, ...((body.params as Record<string, unknown>) ?? {}), instruction, seed: Math.floor(Math.random() * 100000) }
  if (body.view) params.view = body.view
  if (Array.isArray(body.views)) {
    // Several views of a turnaround redrawn together (the mock draws every view again).
    const views = [...new Set(body.views.map(String))]
    if (version.stage !== 'turnaround') throw new HttpError(400, 'only the turnaround step redraws views')
    if (!views.length) throw new HttpError(400, 'no views were chosen to redraw')
    if (version.status !== 'done') throw new HttpError(409, `version is ${version.status}, not done`)
    params.refined_views = views
    params.refined_view = views[0]
  }
  delete params.reference_experimental
  if (params.source === 'upload') params.source = 'upload_edit'
  const reference = typeof body.reference_image === 'string' ? body.reference_image : null
  if (reference) {
    if (version.stage !== 'concept' || asset.category === 'material') throw new HttpError(400, 'A reference picture works only when changing the first picture of a character, creature or prop.')
    checkPicture(reference, 'reference picture')
    params.reference_experimental = true
  }
  const v = newVersion(asset, version.stage, version.id, params)
  if (reference) v.files.reference = putUpload(v, 'reference', reference)
  const job = startJob(asset, v, `${version.stage}.refine`)
  return { job: clone(job), version: clone(v) }
})

// ---------------------------------------------------------------------------
// Start from your own images. The picture (a data: URL) becomes the version's file as it is; the mock "cutout" is
// the same picture, and the readiness checks are faked in data.ts (a person's arms always warn, with the fix).
// ---------------------------------------------------------------------------

const PICTURE_URL = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+$/
function checkPicture(img: unknown, field = 'picture') {
  if (typeof img !== 'string' || !img) throw new HttpError(400, `No ${field} was sent. Try adding it again.`)
  if (img.startsWith('data:image/gif')) throw new HttpError(400, `That ${field} is a GIF. Use a PNG, JPEG or WebP picture.`)
  if (!PICTURE_URL.test(img.slice(0, 200)) && !PICTURE_URL.test(img)) throw new HttpError(400, `That ${field} could not be read. Try adding it again.`)
  if (img.length > (25 * 1024 * 1024 * 4) / 3 + 1024) throw new HttpError(400, `That ${field} is larger than 25 MB. Save a smaller copy and try again.`)
}
function putUpload(v: Version, key: string, img: string): string {
  const path = `projects/mock/assets/${v.asset_id}/${v.stage}/${v.id}/${key}.png`
  files.set(path, img)
  return path
}
function uploadConcept(a: Asset, img: string, note: string | null) {
  checkPicture(img)
  if (note && !a.prompt.trim()) a.prompt = note
  const v = newVersion(a, 'concept', null, { source: 'upload', note })
  v.files.image = putUpload(v, 'image', img)
  const job = startJob(a, v, a.category === 'material' ? 'material.concept.upload' : 'concept.upload')
  return { job: clone(job), version: clone(v) }
}
on('POST', '/api/assets/:id/stages/concept/upload', (m, body) =>
  uploadConcept(findAsset(m.groups!.id), String(body.image ?? ''), typeof body.note === 'string' && body.note.trim() ? body.note.trim() : null),
)
// Your own 3D model on step 3 (backend vgac/model_import.py). The file is not read in the demo: the model is the sample.
const MODEL_EXT = ['.glb', '.gltf', '.fbx', '.obj', '.zip']
on('POST', '/api/assets/:id/stages/mesh/upload', (m, _b, query) => {
  const asset = findAsset(m.groups!.id)
  if (asset.category === 'material') throw new HttpError(400, 'A tileable material has no 3D model, so a model cannot be added to it.')
  const name = query.get('name') ?? ''
  const ext = name.slice(name.lastIndexOf('.')).toLowerCase()
  if (!MODEL_EXT.includes(ext)) throw new HttpError(400, 'Use a GLB, glTF, FBX or OBJ file, or a zip with the model and its pictures.')
  if (Number(query.get('bytes') ?? 0) > 400 * 1024 * 1024) throw new HttpError(413, 'This file is larger than 400 MB.')
  const v = newVersion(asset, 'mesh', null, { source: 'upload', engine: 'upload', file_name: name, turn: 0, hair_source: 'model' })
  const job = startJob(asset, v, 'mesh.upload')
  job.instance = 'host' // Blender on this computer, as in the real app
  return { job: clone(job), version: clone(v) }
})
on('POST', '/api/versions/:vid/turn-model', (m, body) => {
  const { asset, version } = findVersion(m.groups!.vid)
  const turn = Number(body.turn)
  if (![90, 180, 270].includes(turn)) throw new HttpError(400, 'turn must be 90, 180 or 270')
  if (version.stage !== 'mesh' || version.params.engine !== 'upload') throw new HttpError(400, 'only a model you added can be turned this way')
  const v = newVersion(asset, 'mesh', null, { ...version.params, turn: (Number(version.params.turn ?? 0) + turn) % 360 })
  const job = startJob(asset, v, 'mesh.upload')
  job.instance = 'host' // Blender on this computer, as in the real app
  return { job: clone(job), version: clone(v) }
})
on('POST', '/api/versions/:vid/views/:view/upload', (m, body) => {
  const { asset, version } = findVersion(m.groups!.vid)
  const view = m.groups!.view
  if (version.stage !== 'turnaround') throw new HttpError(400, 'your own views go on a turnaround version')
  if (version.status !== 'done') throw new HttpError(409, `version is ${version.status}, not done`)
  if (!TURNAROUND_VIEWS.includes(view as (typeof TURNAROUND_VIEWS)[number])) throw new HttpError(400, `unknown view '${view}'`)
  checkPicture(body.image, `${view} view picture`)
  const views = [...((version.params.views as string[] | undefined) ?? ['front', 'left', 'back', 'right', 'quarter'])]
  if (!views.includes(view)) views.push(view)
  const supplied = [...new Set([...((version.params.supplied_views as string[] | undefined) ?? []), view])].sort()
  const v = newVersion(asset, 'turnaround', version.id, { ...version.params, views, supplied_views: supplied })
  for (const s of supplied) if (s !== view && version.files[`supplied_${s}`]) v.files[`supplied_${s}`] = version.files[`supplied_${s}`]
  v.files[`supplied_${view}`] = putUpload(v, `supplied_${view}`, String(body.image))
  const job = startJob(asset, v, 'turnaround.upload')
  return { job: clone(job), version: clone(v) }
})

// The main colours of a picture, in the browser: a 64 px copy, see-through pixels and a plain border colour left out,
// colours grouped on a coarse grid and the largest groups kept, most used first.
async function mockPalette(img: string, colors: number) {
  checkPicture(img)
  if (![8, 16, 32].includes(colors)) throw new HttpError(400, 'colors must be one of [8, 16, 32]')
  const el = new Image()
  el.src = img
  await el.decode()
  const size = 64
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.drawImage(el, 0, 0, size, size)
  const px = ctx.getImageData(0, 0, size, size).data
  const at = (x: number, y: number) => (y * size + x) * 4
  const border: number[][] = []
  for (let i = 0; i < size; i++) for (const [x, y] of [[i, 0], [i, size - 1], [0, i], [size - 1, i]]) if (px[at(x, y) + 3] >= 128) border.push([px[at(x, y)], px[at(x, y) + 1], px[at(x, y) + 2]])
  const median = (k: number) => border.map((c) => c[k]).sort((a, b) => a - b)[Math.floor(border.length / 2)] ?? 0
  const bg = border.length ? [median(0), median(1), median(2)] : null
  const near = (r: number, g: number, b: number) => !!bg && Math.abs(r - bg[0]) + Math.abs(g - bg[1]) + Math.abs(b - bg[2]) <= 40
  const plain = !!bg && border.filter((c) => near(c[0], c[1], c[2])).length >= border.length * 0.8
  const groups = new Map<number, { n: number; r: number; g: number; b: number }>()
  let total = 0
  for (let i = 0; i < px.length; i += 4) {
    const [r, g, b, a] = [px[i], px[i + 1], px[i + 2], px[i + 3]]
    if (a < 128 || (plain && near(r, g, b))) continue
    const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5)
    const e = groups.get(key) ?? { n: 0, r: 0, g: 0, b: 0 }
    e.n += 1
    e.r += r
    e.g += g
    e.b += b
    groups.set(key, e)
    total += 1
  }
  if (!total) throw new HttpError(400, 'The picture has no visible colours to take.')
  const hex = (n: number) => Math.round(n).toString(16).padStart(2, '0')
  const top = [...groups.values()].sort((x, y) => y.n - x.n).slice(0, colors)
  const kept = top.reduce((s, e) => s + e.n, 0)
  const swatches = top.map((e) => ({ hex: `#${hex(e.r / e.n)}${hex(e.g / e.n)}${hex(e.b / e.n)}`, share: Number((e.n / kept).toFixed(4)) }))
  return { palette: swatches.map((s) => s.hex), swatches, background: plain && bg ? `#${hex(bg[0])}${hex(bg[1])}${hex(bg[2])}` : null, pixels: total }
}
on('POST', '/api/palette-from-image', (_m, body) => mockPalette(String(body.image ?? ''), Number(body.colors ?? 8)))
on('POST', '/api/projects/:id/style-from-image', (m, body) => {
  findProject(m.groups!.id)
  return mockPalette(String(body.image ?? ''), 8).then((pal) => {
    const job: Job = {
      id: uid('j'),
      instance: 'b',
      kind: 'concept.judge',
      asset_id: null,
      version_id: null,
      status: 'queued',
      progress: 0,
      message: 'Waiting for the vision model',
      created_at: nowIso(),
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    }
    store.jobs.push(job)
    broadcast({ type: 'job', job: clone(job) })
    window.setTimeout(() => {
      job.status = 'running'
      job.started_at = nowIso()
      job.message = 'describing the style'
      broadcast({ type: 'job', job: clone(job) })
    }, 800 / simulationSpeed)
    window.setTimeout(() => {
      job.status = 'done'
      job.progress = 1
      job.message = 'described'
      job.finished_at = nowIso()
      job.result = {
        description: 'Gouache on rough paper, soft two-tone shading with a warm rim light, thin dark brown outlines that fade on lit edges, chunky rounded shapes with large heads and small feet, a warm muted palette of ochre, brick red and slate blue.',
        words: 38,
        palette: pal.palette,
        swatches: pal.swatches,
        model: 'gemma-3-12b-it',
        applied: false,
      }
      broadcast({ type: 'job', job: clone(job) })
    }, 3500 / simulationSpeed)
    return { ...pal, job: clone(job), needs_review_engine: false, message: 'The vision model is describing the style. It takes about a minute.' }
  })
})
on('POST', '/api/versions/:vid/adjust', (m, body) => {
  const { asset, version } = findVersion(m.groups!.vid)
  const params = { ...version.params, ...((body.params as Record<string, unknown>) ?? {}) }
  // "Try a different face" (backend stages/prep.py FACE_FRESH): new face seeds, the marker is not kept.
  if (params.face_fresh) {
    for (const k of ['face_fresh', 'face_detail_seed', 'face_seed', 'face_closeup_seed', 'face_closeup_seed_2']) delete params[k]
    params.face_seed = Math.floor(Math.random() * 1e9)
  }
  const v = newVersion(asset, version.stage, version.id, params)
  const job = startJob(asset, v, `${version.stage}.adjust`)
  return { job: clone(job), version: clone(v) }
})
on('POST', '/api/versions/:vid/select', (m) => {
  const { asset, version } = findVersion(m.groups!.vid)
  if (version.status !== 'done') throw new HttpError(409, 'only finished versions can be selected')
  setSelected(asset, version)
  return version
})
on('POST', '/api/versions/:vid/reject', (m) => {
  const { asset, version } = findVersion(m.groups!.vid)
  version.status = 'rejected'
  if (version.selected) {
    version.selected = false
    delete asset.selected[version.stage]
  }
  return version
})
on('GET', '/api/versions/:vid', (m) => findVersion(m.groups!.vid).version)
on('POST', '/api/versions/:vid/open-folder', () => ({ opened: true, folder: 'F:\\VGAC\\exports\\Hollow Keep\\Archer', front: true }))
on('POST', '/api/versions/:vid/feedback', (m, body) => {
  const { version } = findVersion(m.groups!.vid)
  const thumbs = body.thumbs === 'up' || body.thumbs === 'down' ? body.thumbs : body.thumbs === null || body.thumbs === undefined ? null : undefined
  if (thumbs === undefined) throw new HttpError(422, 'thumbs must be "up", "down" or null')
  const note = typeof body.note === 'string' ? body.note.trim().slice(0, 500) : ''
  version.metrics = { ...version.metrics, feedback: { thumbs, note: thumbs === 'down' && note ? note : null, at: nowIso() } }
  broadcast({ type: 'version', version: clone(version) })
  return version
})

// Gallery: every asset with a finished export version, newest first.
const folderName = (s: string) => s.trim().replace(/[^A-Za-z0-9_-]+/g, '_') || 'asset'
on('GET', '/api/gallery', () => {
  const out: { item: GalleryItem; t: number }[] = []
  for (const a of store.assets) {
    const exports = (a.stages.export ?? []).filter((v) => v.status === 'done')
    if (!exports.length) continue
    const ex = exports.find((v) => v.id === a.selected.export) ?? exports[exports.length - 1]
    const project = store.projects.find((p) => p.id === a.project_id)
    const concepts = a.stages.concept ?? []
    const concept = concepts.find((v) => v.id === a.selected.concept) ?? concepts.find((v) => v.status === 'done')
    const job = store.jobs.find((j) => j.id === ex.job_id)
    const finished = job?.finished_at ?? ex.created_at
    const item: GalleryItem = {
      asset_id: a.id,
      asset_name: a.name,
      project_id: a.project_id,
      project_name: project?.name ?? '',
      category: a.category,
      style_preset: a.style_preset,
      platform_target: a.platform_target,
      created_at: a.created_at,
      finished_at: finished,
      thumbnail: concept?.files.cutout ?? concept?.files.image ?? null,
      preview: ex.files.glb ?? null,
      delivered_to: `F:/Projects/VideoGameAssetCreator/exports/${folderName(project?.name ?? 'project')}/${folderName(a.name)}`,
      fbx: ex.files.fbx ?? null,
      glb: ex.files.glb ?? null,
    }
    out.push({ item, t: Date.parse(finished) || 0 })
  }
  return out.sort((x, y) => y.t - x.t).map((x) => x.item)
})
on('POST', '/api/versions/:vid/judge', (m, body) => {
  const { asset, version } = findVersion(m.groups!.vid)
  version.metrics = { ...version.metrics, judge: body.mode ?? 'metrics', judged_at: nowIso() }
  // Like the backend's collect_unity_import: a hand-off to the open Unity project that Unity has picked up since.
  const hand = version.metrics.unity_project_import as Record<string, unknown> | undefined
  if (hand && hand.state === 'sent') {
    const name = String(hand.folder ?? '').split(/[\\/]/).slice(-2, -1)[0] || asset.name
    const rest = { ...hand }
    delete rest.note
    version.metrics.unity_project_import = { ...rest, ok: true, state: 'imported', prefab: `Assets/VGAC/${name}/${name}.prefab`, error: '' }
    broadcast({ type: 'version', version: clone(version) })
  }
  return version
})
on('GET', '/api/recipe', (_m, _b, query) => {
  const style = query.get('style') ?? ''
  const category = query.get('category') ?? 'humanoid'
  const platform = query.get('platform') ?? 'pc_mid'
  if (!categories.some((c) => c.id === category)) throw new HttpError(404, `unknown category: ${category}`)
  return recipeFor({ style_preset: style, category, platform_target: platform as Asset['platform_target'] })
})

// ---------------------------------------------------------------------------
// Autopilot: a simulated runner. From the first stage without a kept result it
// runs each stage once, keeps the result when it passes, and pauses when a job
// fails or a check fails that stop_on says to stop for. The first prep run of
// every asset is forced to fail its checks (seed 3) so the paused state shows;
// "Fix and continue" sends {fix: {stage, params}} and the runner re-runs that
// stage as an adjust of its kept version, then carries on.
// ---------------------------------------------------------------------------

const WARN_CHECK = /^(texture_.+_budget|lod\d+_ratio|height_ratio|hist_dist|coverage|mirror_iou)$/
const pausedOnce = new Set<string>()
const runners = new Map<string, number>() // asset id -> interval handle

function idleState(): AutopilotState {
  return { status: 'idle', stage: null, job_id: null, version_id: null, reason: null, stop_on: 'block', until_stage: 'export', started_at: null, updated_at: null, stages_done: [] }
}

function apState(asset: Asset): AutopilotState {
  if (!asset.autopilot) asset.autopilot = idleState()
  return asset.autopilot
}

function apBroadcast(asset: Asset) {
  const st = apState(asset)
  st.updated_at = nowIso()
  broadcast({ type: 'autopilot', asset_id: asset.id, autopilot: clone(st) })
}

function failingChecks(version: Version, stopOn: string): { blocking: Check[]; failing: Check[] } {
  const checks = Array.isArray(version.metrics.checks) ? (version.metrics.checks as Check[]) : []
  const failing = checks.filter((c) => !(c.passed ?? c.ok ?? false))
  const sev = (c: Check) => c.severity ?? (WARN_CHECK.test(c.id ?? c.name) ? 'warn' : 'block')
  const blocking = stopOn === 'never' ? [] : stopOn === 'warn' ? failing.slice() : failing.filter((c) => sev(c) === 'block')
  // Turnaround: a view that failed consistency counts as a warning.
  if (stopOn === 'warn' && version.metrics.consistency_ok === false && !blocking.length) {
    const flagged = Array.isArray(version.metrics.flagged_views) ? (version.metrics.flagged_views as string[]) : []
    blocking.push({ id: 'consistency', name: 'consistency', passed: false, ok: false, value: flagged, limit: [], message: `${flagged.join(', ')} do not match the front view`, severity: 'warn' })
  }
  return { blocking, failing }
}

function apStop(asset: Asset, status: AutopilotState['status'], reason: string | null) {
  const st = apState(asset)
  const h = runners.get(asset.id)
  if (h !== undefined) {
    window.clearInterval(h)
    runners.delete(asset.id)
  }
  st.status = status
  st.reason = reason
  if (status === 'done' || status === 'idle') {
    st.job_id = null
    if (status === 'idle') st.stage = null
  }
  apBroadcast(asset)
}

function apNextStage(asset: Asset): StageId | null {
  const st = apState(asset)
  const until = STAGES.includes(st.until_stage as StageId) ? (st.until_stage as StageId) : 'export'
  for (const s of STAGES) {
    if (!asset.selected[s]) return s
    if (s === until) return null
  }
  return null
}

function apRunStage(asset: Asset, stage: StageId, fix?: { stage: StageId; params: Record<string, unknown> }) {
  const st = apState(asset)
  const idx = STAGES.indexOf(stage)
  const parent = idx > 0 ? (asset.selected[STAGES[idx - 1]] ?? null) : null
  let version: Version
  let kind: string
  if (fix) {
    const kept = (asset.stages[stage] ?? []).find((v) => v.id === asset.selected[stage])
    const base = kept?.params ?? stageParamsDefault(stage)
    version = newVersion(asset, stage, kept ? kept.id : parent, { ...base, ...fix.params, autopilot: true })
    kind = `${stage}.adjust`
  } else {
    const params: Record<string, unknown> = { ...stageParamsDefault(stage), seed: Math.floor(Math.random() * 100000), autopilot: true }
    // Prep: fail its checks once (seed 3: triangle_budget and manifold), then pass (seed % 3 !== 0).
    if (stage === 'prep') params.seed = pausedOnce.has(asset.id) ? 7 + 3 * Math.floor(Math.random() * 1000) : 3
    if (stage === 'concept') {
      if (asset.concept_model) params.model = asset.concept_model
      try {
        const pref = localStorage.getItem('vgac.conceptModel.' + asset.id)
        if (pref) params.model = pref
      } catch {
        // ignore
      }
    }
    version = newVersion(asset, stage, parent, params)
    kind = `${stage}.run`
  }
  const job = startJob(asset, version, kind)
  st.status = 'running'
  st.stage = stage
  st.job_id = job.id
  st.version_id = version.id
  st.reason = null
  apBroadcast(asset)

  const h = window.setInterval(() => {
    if (job.status === 'queued' || job.status === 'running') return
    window.clearInterval(h)
    runners.delete(asset.id)
    if (st.status !== 'running' || st.job_id !== job.id) return // stopped meanwhile
    if (job.status === 'cancelled') {
      st.stopped_by_user = true
      apStop(asset, 'paused', 'You stopped it. Everything made so far is kept. Press Resume to carry on from this step.')
      return
    }
    if (job.status === 'error') {
      apStop(asset, 'paused', `The "${stage}" step failed and needs another try.`)
      return
    }
    const { blocking } = failingChecks(version, String(st.stop_on))
    if (blocking.length) {
      // Not kept: the person decides. (startJob auto-selects the first result; undo that.)
      if (asset.selected[stage] === version.id) {
        version.selected = false
        delete asset.selected[stage]
      }
      pausedOnce.add(asset.id)
      const n = blocking.length
      apStop(asset, 'paused', `${n} check${n > 1 ? 's' : ''} failed on "${stage}" and the autopilot could not fix ${n > 1 ? 'them' : 'it'} on its own.`)
      return
    }
    setSelected(asset, version)
    if (!st.stages_done.includes(stage)) st.stages_done.push(stage)
    broadcast({ type: 'version', version: clone(version) })
    apStep(asset)
  }, 250)
  runners.set(asset.id, h)
}

function apStep(asset: Asset) {
  const st = apState(asset)
  const next = apNextStage(asset)
  if (!next) {
    st.stage = STAGES.includes(st.until_stage as StageId) ? st.until_stage : 'export'
    apStop(asset, 'done', null)
    return
  }
  const running = (asset.stages[next] ?? []).some((v) => v.status === 'queued' || v.status === 'running')
  if (running) {
    apStop(asset, 'error', `"${next}" is already running; wait for it, then start the autopilot again.`)
    return
  }
  apRunStage(asset, next)
}

on('GET', '/api/assets/:id/autopilot', (m) => apState(findAsset(m.groups!.id)))
on('POST', '/api/assets/:id/autopilot', (m, body) => {
  const a = findAsset(m.groups!.id)
  const st = apState(a)
  if (st.status === 'running') throw new HttpError(409, 'the autopilot is already running')
  const stopOn = String(body.stop_on ?? (st.status === 'paused' ? st.stop_on : 'block'))
  if (!['block', 'warn', 'never'].includes(stopOn)) throw new HttpError(422, 'stop_on must be block, warn or never')
  const until = String(body.until_stage ?? 'export')
  if (!STAGES.includes(until as StageId)) throw new HttpError(422, 'unknown until_stage')
  st.stop_on = stopOn
  st.until_stage = until
  st.started_at = nowIso()
  st.stopped_by_user = false
  const from = body.from_stage
  if (typeof from === 'string') {
    if (!STAGES.includes(from as StageId)) throw new HttpError(400, 'unknown from_stage')
    // Go back: that step and every later one are set aside and made again.
    for (const s of STAGES.slice(STAGES.indexOf(from as StageId))) {
      delete a.selected[s]
      for (const v of a.stages[s] ?? []) {
        if (!v.selected && v.status !== 'done' && !v.stopped) continue
        v.selected = false
        if (v.status === 'done') v.status = 'rejected'
        if (v.stopped) {
          v.stopped = false
          v.error = 'stopped and set aside'
        }
        broadcast({ type: 'version', version: clone(v) })
      }
    }
  }
  st.stages_done = STAGES.filter((s) => !!a.selected[s])
  const fix = body.fix as { stage?: string; params?: Record<string, unknown> } | undefined
  if (fix && STAGES.includes(fix.stage as StageId)) {
    apRunStage(a, fix.stage as StageId, { stage: fix.stage as StageId, params: fix.params ?? {} })
  } else {
    apStep(a)
  }
  return apState(a)
})
// Stop and Resume (the buttons on every step). A stopped version is "rejected" with error "cancelled" and
// stopped: true; Resume starts it again with the same settings.
function stopJob(job: Job): string | null {
  if (job.status !== 'queued' && job.status !== 'running') return null
  job.status = 'cancelled'
  job.message = 'cancelled'
  job.finished_at = nowIso()
  let stoppedId: string | null = null
  if (job.version_id) {
    try {
      const { version } = findVersion(job.version_id)
      if (version.status === 'queued' || version.status === 'running') {
        version.status = 'rejected'
        version.error = 'cancelled'
        version.stopped = true
        stoppedId = version.id
        broadcast({ type: 'version', version: clone(version) })
      }
    } catch {
      // no version
    }
  }
  broadcast({ type: 'job', job: clone(job) })
  return stoppedId
}

function trailingStopped(asset: Asset, stage: StageId): Version[] {
  const list = asset.stages[stage] ?? []
  const out: Version[] = []
  for (let i = list.length - 1; i >= 0; i--) {
    if (!list[i].stopped) break
    out.unshift(list[i])
  }
  return out
}

function stopAsset(a: Asset, stage: StageId | null): { stopped: string[]; autopilot: AutopilotState } {
  const st = apState(a)
  const apRunning = st.status === 'running'
  const stopped: string[] = []
  for (const job of store.jobs) {
    if (job.asset_id !== a.id) continue
    if (!apRunning && stage) {
      const v = (a.stages[stage] ?? []).find((x) => x.id === job.version_id)
      if (!v) continue
    }
    const vid = stopJob(job)
    if (vid) stopped.push(vid)
  }
  if (apRunning) {
    st.stopped_by_user = true
    st.job_id = null
    apStop(a, 'paused', 'You stopped it. Everything made so far is kept. Press Resume to carry on from this step.')
  }
  return { stopped, autopilot: apState(a) }
}

function resumeStopped(a: Asset, stage: StageId | null): Version[] {
  const resumed: Version[] = []
  for (const s of stage ? [stage] : STAGES) {
    for (const v of trailingStopped(a, s)) {
      const old = store.jobs.find((j) => j.id === v.job_id)
      v.status = 'queued'
      v.error = null
      v.stopped = false
      startJob(a, v, old?.kind ?? `${s}.generate`)
      broadcast({ type: 'version', version: clone(v) })
      resumed.push(v)
    }
  }
  return resumed
}

on('POST', '/api/assets/:id/autopilot/stop', (m) => stopAsset(findAsset(m.groups!.id), null).autopilot)
on('POST', '/api/assets/:id/stop', (m, body) => {
  const stage = typeof body.stage === 'string' && STAGES.includes(body.stage as StageId) ? (body.stage as StageId) : null
  return stopAsset(findAsset(m.groups!.id), stage)
})
on('POST', '/api/assets/:id/resume', (m, body) => {
  const a = findAsset(m.groups!.id)
  const stage = typeof body.stage === 'string' && STAGES.includes(body.stage as StageId) ? (body.stage as StageId) : null
  const st = apState(a)
  if (st.stopped_by_user && st.status === 'paused') {
    // The autopilot carries on from the stopped step: that step runs again, then the rest follow.
    const target = apNextStage(a)
    if (target) for (const v of trailingStopped(a, target)) v.stopped = false
    st.stopped_by_user = false
    st.status = 'idle'
    st.started_at = nowIso()
    apStep(a)
    return { resumed: [], autopilot: apState(a) }
  }
  return { resumed: clone(resumeStopped(a, stage)), autopilot: st }
})
on('POST', '/api/assets/:id/stopped/dismiss', (m, body) => {
  const a = findAsset(m.groups!.id)
  const vs = trailingStopped(a, String(body.stage) as StageId)
  for (const v of vs) {
    v.stopped = false
    v.error = 'stopped and set aside'
    broadcast({ type: 'version', version: clone(v) })
  }
  return { dismissed: vs.length }
})

// Your own add-ons (backend user_addons.py). The demo folder holds one file that is not set up yet.
const MOCK_PAINTERS = [
  { id: 'zturbo', label: 'Z-Image Turbo' },
  { id: 'zbase', label: 'Z-Image Base' },
  { id: 'klein', label: 'FLUX.2 Klein 4B' },
  { id: 'anima', label: 'Anima Turbo' },
  { id: 'pixel', label: 'Z-Image Pixel Art' },
  { id: 'chroma', label: 'Chroma1-HD' },
]
const MOCK_ADDON_STRENGTHS: Record<StrengthName, number> = { subtle: 0.6, normal: 0.8, strong: 1.0 }
const MOCK_ADDON_FOLDER = 'F:\\Projects\\ComfyUI\\models\\loras\\VGAC\\mine'
const mockAddonFiles: { file_name: string; bytes: number; prefill: AddonPrefill }[] = [
  { file_name: 'ink_wash_style.safetensors', bytes: 170_000_000, prefill: { label: 'Ink wash style', base_models: ['zbase'], trigger: 'ink wash painting', family: 'Z-Image', painter_note: '' } },
]
const myAddons: AddonEntry[] = []

function mockPrefill(name: string): AddonPrefill {
  const lower = name.toLowerCase()
  const label = name.replace(/\.safetensors$/i, '').replace(/[_-]+/g, ' ').trim()
  const turbo = /z.?image|zit/.test(lower) && lower.includes('turbo')
  return {
    label: label.charAt(0).toUpperCase() + label.slice(1),
    base_models: turbo ? ['zturbo'] : [],
    trigger: '',
    family: turbo ? 'Z-Image' : '',
    painter_note: turbo ? '' : 'We could not tell which model this was made for.',
  }
}

on('GET', '/api/my-addons', () => ({ addons: myAddons, folder: MOCK_ADDON_FOLDER, folder_exists: true, strengths: MOCK_ADDON_STRENGTHS, painters: MOCK_PAINTERS }))
on('GET', '/api/my-addons/unregistered', (): UnregisteredAddon[] =>
  mockAddonFiles.filter((f) => !myAddons.some((a) => a.file_name === f.file_name)).map((f) => ({ ...f })),
)
on('POST', '/api/my-addons/open-folder', () => ({ opened: true, folder: MOCK_ADDON_FOLDER }))
on('POST', '/api/my-addons/upload', (_m, _b, query) => {
  const name = String(query.get('name') ?? '')
  if (/[\\/:]|\.\./.test(name)) throw new HttpError(400, 'The file name must be a plain file name, without folders.')
  if (!name.toLowerCase().endsWith('.safetensors')) throw new HttpError(400, 'LoRAs must be .safetensors files. Other kinds of model file are not accepted.')
  const safe = name.replace(/[^A-Za-z0-9._ -]+/g, '_')
  if (mockAddonFiles.some((f) => f.file_name === safe))
    throw new HttpError(409, 'A file with this name is already in the LoRA folder. Use "Look again" to set it up, or rename your file and choose it again.')
  const file = { file_name: safe, bytes: Number(query.get('bytes') ?? 0), prefill: mockPrefill(safe) }
  mockAddonFiles.push(file)
  return file
})
on('POST', '/api/my-addons', (_m, body) => {
  const fileName = String(body.file_name ?? '')
  const file = mockAddonFiles.find((f) => f.file_name === fileName)
  if (!file) throw new HttpError(404, 'That file is not in the LoRA folder.')
  if (myAddons.some((a) => a.file_name === fileName)) throw new HttpError(409, 'This file is already set up as one of your LoRAs.')
  const label = String(body.label ?? '').trim()
  if (!label) throw new HttpError(400, 'Give the LoRA a name.')
  const models = Array.isArray(body.base_models) ? body.base_models.map(String) : []
  if (!models.length) throw new HttpError(400, 'Choose at least one model the LoRA was made for.')
  const strength = (['subtle', 'normal', 'strong'].includes(String(body.default_strength)) ? body.default_strength : 'normal') as StrengthName
  let id = 'mine_' + (label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'addon')
  for (let i = 2; myAddons.some((a) => a.id === id); i++) id = id.replace(/_\d+$/, '') + '_' + i
  const entry: AddonEntry = {
    id,
    label,
    help: {
      meaning: `Your own LoRA, from the file ${fileName}.`,
      why: 'You added it yourself and the app has not tested it. If the picture looks wrong, try Subtle or switch it off.',
      technical: `LoRA VGAC/mine/${fileName}, model only, for ${models.join(', ')}`,
    },
    stage: 'concept',
    base_models: models,
    strengths: { ...MOCK_ADDON_STRENGTHS },
    default_strength: strength,
    required: false,
    required_for: [],
    trigger: String(body.trigger ?? ''),
    licence: 'Your own file: not checked by the app. You are responsible for being allowed to use it in your game.',
    source: String(body.source ?? ''),
    mine: true,
    file_name: fileName,
    added_at: nowIso(),
  }
  myAddons.push(entry)
  return entry
})
on('DELETE', '/api/my-addons/:id', (m) => {
  const i = myAddons.findIndex((a) => a.id === m.groups!.id)
  if (i < 0) throw new HttpError(404, 'That LoRA is not on your list.')
  const [gone] = myAddons.splice(i, 1)
  return { removed: gone.id, file_name: gone.file_name, file_kept: true }
})

// Add a move (mock/moves.ts): the library, a character's moves, and your own animation files.
function keptRig(a: Asset): Version | null {
  const id = a.selected.rig
  const v = id ? a.stages.rig?.find((x) => x.id === id) : undefined
  return v && v.status === 'done' ? v : null
}
function rigBusy(a: Asset): boolean {
  return store.jobs.some((j) => j.asset_id === a.id && j.kind.startsWith('rig.') && (j.status === 'queued' || j.status === 'running'))
}
function movesOthers(a: Asset): { asset_id: string; name: string; added: number; removed: number }[] {
  return store.assets
    .filter((o) => o.id !== a.id && o.project_id === a.project_id && o.category === 'humanoid')
    .map((o) => ({ o, v: keptRig(o) }))
    .filter(({ v }) => v && (addedOf(v.params).length || removedOf(v.params).length))
    .map(({ o, v }) => ({ asset_id: o.id, name: o.name, added: addedOf(v!.params).length, removed: removedOf(v!.params).length }))
}
on('GET', '/api/moves/library', () => moveLibrary())
on('GET', '/api/assets/:id/moves', (m) => {
  const a = findAsset(m.groups!.id)
  const v = keptRig(a)
  return characterMoves(a.category, v ? { id: v.id, params: v.params } : null, rigBusy(a), a.category === 'humanoid' ? movesOthers(a) : [])
})
on('POST', '/api/assets/:id/moves', (m, body) => {
  const a = findAsset(m.groups!.id)
  if (a.category !== 'humanoid') throw new HttpError(400, 'The hand-made moves only fit human-shaped characters, so they cannot be added to this creature.')
  const v = keptRig(a)
  if (!v) throw new HttpError(409, 'Make the skeleton first: the moves are added to the kept version of the Make it move step.')
  if (rigBusy(a)) throw new HttpError(409, 'The moves are already being made again. Wait for that to finish.')
  let next: { added_moves: unknown[]; removed_moves: string[] }
  try {
    if (typeof body.copy_from === 'string' && body.copy_from) {
      const src = keptRig(findAsset(body.copy_from))
      if (!src) throw new Error('That character has no finished moves to copy.')
      next = { added_moves: addedOf(src.params), removed_moves: removedOf(src.params) }
    } else {
      const add = Array.isArray(body.add) ? body.add.map(String) : []
      const remove = Array.isArray(body.remove) ? body.remove.map(String) : []
      if (!add.length && !remove.length) throw new Error('Choose at least one move to add or take off.')
      next = changeMoves(v.params, add, remove)
    }
  } catch (e) {
    if (e instanceof HttpError) throw e
    throw new HttpError(400, e instanceof Error ? e.message : String(e))
  }
  const nv = newVersion(a, 'rig', v.id, { ...v.params, ...next })
  const job = startJob(a, nv, 'rig.adjust')
  return { job: clone(job), version: clone(nv) }
})
on('GET', '/api/my-moves', () => ({ files: myMoveFiles, responsibility: 'You are responsible for being allowed to use these files in your game.', max_mb: 200, kinds: ['.fbx', '.glb', '.bvh'] }))
on('POST', '/api/my-moves/upload', (_m, _b, query) => {
  const name = String(query.get('name') ?? '')
  if (/[\\/:]|\.\./.test(name)) throw new HttpError(400, 'The file name must be a plain file name, without folders.')
  if (Number(query.get('bytes') ?? 0) > 200 * 1024 * 1024) throw new HttpError(413, 'This file is larger than 200 MB. Animation files are usually much smaller.')
  try {
    return mockReadMoveFile(name)
  } catch (e) {
    throw new HttpError(400, e instanceof Error ? e.message : String(e))
  }
})
on('PUT', '/api/my-moves/:id', (m, body) => {
  const f = myMoveFiles.find((x) => x.id === m.groups!.id)
  if (!f) throw new HttpError(404, 'That file is not on your list.')
  if (typeof body.label === 'string' && body.label.trim()) f.label = body.label.trim().slice(0, 80)
  if (typeof body.source === 'string') f.source = body.source.trim().slice(0, 500)
  if (Array.isArray(body.clips)) {
    for (const c of body.clips as { action?: unknown; label?: unknown; loop?: unknown }[]) {
      const clip = f.clips.find((x) => x.action === c.action)
      if (!clip) throw new HttpError(400, `This file has no move called ${String(c.action)}.`)
      const label = String(c.label ?? '').trim()
      if (!label) throw new HttpError(400, 'Give every move a name.')
      clip.label = label.slice(0, 60)
      clip.loop = c.loop === true
    }
  }
  return f
})
on('DELETE', '/api/my-moves/:id', (m) => {
  const i = myMoveFiles.findIndex((x) => x.id === m.groups!.id)
  if (i < 0) throw new HttpError(404, 'That file is not on your list.')
  const [gone] = myMoveFiles.splice(i, 1)
  return { removed: gone.id }
})

// Your own hairstyles (mock/hair.ts; backend vgac/my_hair.py)
const ownHairErrors = <T,>(fn: () => T): T => {
  try {
    return fn()
  } catch (e) {
    if (e instanceof OwnHairError) throw new HttpError(e.status, e.message)
    throw e
  }
}
on('GET', '/api/my-hair', () => myHairListing())
on('POST', '/api/my-hair/upload', (_m, _b, query) => ownHairErrors(() => addMyHair(query)))
on('PUT', '/api/my-hair/:id', (m, body) => ownHairErrors(() => changeMyHair(m.groups!.id, body)))
on('DELETE', '/api/my-hair/:id', (m) =>
  ownHairErrors(() =>
    removeMyHair(
      m.groups!.id,
      store.assets.filter((a) => a.hair?.pick === m.groups!.id).map((a) => a.name),
    ),
  ),
)

// Hair from the library (mock/hair.ts; backend vgac/hairlib.py)
on('GET', '/api/hair-library', (_m, _b, query) => {
  const aid = query.get('asset_id')
  const platform = query.get('platform') ?? (aid ? findAsset(aid).platform_target : null)
  return hairCatalogue(platform)
})
on('GET', '/api/assets/:id/hair', (m) => hairPanel(findAsset(m.groups!.id)))
on('PUT', '/api/assets/:id/hair', (m, body) => {
  const a = findAsset(m.groups!.id)
  const source = body.source
  if (source !== 'model' && source !== 'library') throw new HttpError(422, 'source must be "model" or "library"')
  const panel = hairPanel(a)
  if (source === 'library' && !panel.available) throw new HttpError(409, panel.unavailable_reason || 'Hair from the library is not available for this character.')
  a.hair = { source, pick: a.hair?.pick ?? null, picked_at: a.hair?.picked_at ?? null, changed_by: 'page' }
  const started: { job: Job; version: Version }[] = []
  const turn = (a.stages.turnaround ?? []).find((v) => v.id === a.selected.turnaround)
  if (source === 'library' && turn && turn.status === 'done' && !('bald_front' in turn.files)) {
    const v = newVersion(a, 'turnaround', turn.id, { ...turn.params, bald_views: true, bald_only: true, bald_seed: Math.floor(Math.random() * 100000) })
    started.push({ job: clone(startJob(a, v, 'turnaround.adjust')), version: clone(v) })
  }
  return { panel: hairPanel(a), started, rebuild_from: a.selected.mesh ? 'mesh' : null }
})
on('POST', '/api/assets/:id/hair/pick', (m, body) => {
  const a = findAsset(m.groups!.id)
  const style = String(body.style ?? '')
  if (!hairCatalogue().styles.some((s) => s.id === style)) throw new HttpError(404, `unknown hairstyle '${style}'`)
  a.hair = { source: 'library', pick: style, picked_at: nowIso(), changed_by: 'page' }
  const panel = hairPanel(a)
  if (body.rebuild && panel.applied && panel.applied.style !== style) {
    // Like autopilot.start(fix={stage: prep, params: {hair_style}}): the later steps are set aside and made again.
    for (const s of ['rig', 'export'] as StageId[]) {
      delete a.selected[s]
      for (const v of a.stages[s] ?? []) v.selected = false
    }
    const st = apState(a)
    st.stop_on = 'block'
    st.until_stage = 'export'
    st.started_at = nowIso()
    apRunStage(a, 'prep', { stage: 'prep', params: { hair_style: style, hair_style_source: 'chosen' } })
    return { panel: hairPanel(a), autopilot: clone(apState(a)) }
  }
  return { panel, autopilot: null }
})

// Items (mock/items.ts; backend vgac/items.py)
const itemsErr = (f: () => unknown) => {
  try {
    return f()
  } catch (e) {
    if (e instanceof HttpError) throw e
    throw new HttpError(e instanceof Error && e.message === 'attachment not found' ? 404 : 400, e instanceof Error ? e.message : String(e))
  }
}
on('GET', '/api/item-words', () => ({ version: 1, places: PLACES, help: 'Clothes that bend with the body (a cloak, armour, boots, a belt) stay part of the body.' }))
on('POST', '/api/items/detect', (_m, body) => mockDetect(String(body.prompt ?? ''), String(body.category ?? 'humanoid')))
on('GET', '/api/assets/:id/items', (m) => mockPanel(findAsset(m.groups!.id)))
on('POST', '/api/assets/:id/items', (m, body) => itemsErr(() => mockAddItem(findAsset(m.groups!.id), body)))
on('PUT', '/api/assets/:id/attachments/:att', (m, body) => itemsErr(() => mockUpdate(findAsset(m.groups!.id), m.groups!.att, body)))
on('POST', '/api/assets/:id/attachments/:att/nudge', (m, body) => itemsErr(() => mockNudge(findAsset(m.groups!.id), m.groups!.att, body)))
on('DELETE', '/api/assets/:id/attachments/:att', (m) => itemsErr(() => mockRemove(findAsset(m.groups!.id), m.groups!.att)))
on('POST', '/api/assets/:id/items/apply', (m) => {
  const a = findAsset(m.groups!.id)
  const ex = a.stages.export?.find((v) => v.id === a.selected.export && v.status === 'done')
  if (!ex) throw new HttpError(409, 'finish the character first')
  const nv = newVersion(a, 'export', ex.id, { ...ex.params })
  const job = startJob(a, nv, 'export.adjust')
  mockApplied(a)
  return { job: clone(job), version: clone(nv) }
})
on('POST', '/api/assets/:id/items/build', (m) => mockPanel(findAsset(m.groups!.id)))
on('POST', '/api/assets/:id/attachments/:att/fit', (m) => {
  const a = findAsset(m.groups!.id)
  const ex = a.stages.export?.find((v) => v.id === a.selected.export && v.status === 'done')
  if (!ex) throw new HttpError(409, 'finish the character first')
  const at = itemsErr(() => mockFit(a, m.groups!.att))
  const nv = newVersion(a, 'export', ex.id, { ...ex.params })
  const job = startJob(a, nv, 'export.adjust')
  mockApplied(a)
  return { job: clone(job), version: clone(nv), attachment: clone(at) }
})

on('GET', '/api/jobs', (_m, _b, query) => {
  let list = store.jobs
  const asset = query.get('asset_id')
  const status = query.get('status')
  if (asset) list = list.filter((j) => j.asset_id === asset)
  if (status) list = list.filter((j) => j.status === status)
  return list.slice(-200)
})
on('GET', '/api/jobs/:id', (m) => findJob(m.groups!.id))
on('POST', '/api/jobs/:id/cancel', (m) => {
  const job = findJob(m.groups!.id)
  stopJob(job)
  return job
})

function dispatch(method: string, pathname: string, body: Record<string, unknown>, query: URLSearchParams): { status: number; body: unknown } {
  for (const r of routes) {
    if (r.method !== method) continue
    const m = pathname.match(r.re)
    if (!m) continue
    try {
      const out = r.handler(m, body, query)
      // A handler that needs the browser to decode a picture (palette) answers with a promise.
      return { status: 200, body: out instanceof Promise ? out : clone(out) }
    } catch (e) {
      if (e instanceof HttpError) return { status: e.status, body: { detail: e.message } }
      console.error('mock handler failed', e)
      return { status: 500, body: { detail: e instanceof Error ? e.message : String(e) } }
    }
  }
  return { status: 404, body: { detail: `no mock route for ${method} ${pathname}` } }
}

export function installMock() {
  seedStore()
  setLibraryResolver((path) => files.get(path) ?? null)

  const realFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const u = new URL(url, location.origin)
    if (u.origin !== location.origin || !u.pathname.startsWith('/api/') || u.pathname.startsWith('/api/library/')) {
      return realFetch(input, init)
    }
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
    let body: Record<string, unknown> = {}
    if (init?.body && typeof init.body === 'string') {
      try {
        body = JSON.parse(init.body) as Record<string, unknown>
      } catch {
        body = {}
      }
    }
    await new Promise((r) => window.setTimeout(r, 60 + Math.random() * 120))
    let res = dispatch(method, u.pathname, body, u.searchParams)
    if (res.body instanceof Promise)
      res = await res.body.then(
        (b: unknown) => ({ status: 200, body: clone(b) }),
        (e: unknown) => (e instanceof HttpError ? { status: e.status, body: { detail: e.message } } : { status: 400, body: { detail: 'That picture could not be read. Try adding it again.' } }),
      )
    return new Response(JSON.stringify(res.body), { status: res.status, headers: { 'content-type': 'application/json' } })
  }

  const RealWebSocket = window.WebSocket
  const Patched = new Proxy(RealWebSocket, {
    construct(target, args: [string | URL, (string | string[])?]) {
      if (String(args[0]).includes('/api/ws/events')) return new MockSocket(String(args[0]))
      return new target(args[0], args[1])
    },
  })
  window.WebSocket = Patched as typeof WebSocket

  const w = window as unknown as { vgacMock?: unknown }
  w.vgacMock = {
    store,
    setSpeed(x: number) {
      simulationSpeed = x
    },
  }
  console.info('[vgac] mock backend installed; window.vgacMock exposes the store')
}
