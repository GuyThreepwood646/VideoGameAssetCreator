// Mock backend: a fetch interceptor for /api/* plus a fake WebSocket for /api/ws/events.
// Enabled when VITE_MOCK=1. Library files that the mock generates are data URLs
// registered through setLibraryResolver; everything else under /api/library is
// served by the Vite dev server from ../library (see vite.config.ts).
import { setLibraryResolver, STAGES, type Asset, type AutopilotState, type Check, type GalleryItem, type Job, type ServerEvent, type StageId, type Version } from '../api'
import { categories, fabricate, files, lookPresets, newVersion, nowIso, platforms, seedStore, setSelected, stages, store, styles, uid } from './data'

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

function startJob(asset: Asset, version: Version, kind: string): Job {
  const job: Job = {
    id: uid('j'),
    instance: INSTANCE[version.stage],
    kind,
    asset_id: asset.id,
    version_id: version.id,
    status: 'queued',
    progress: 0,
    message: 'queued',
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
  window.setTimeout(() => {
    if (job.status === 'cancelled') return
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
        if (!asset.selected[version.stage]) setSelected(asset, version)
      }
      job.finished_at = nowIso()
      broadcast({ type: 'job', job: clone(job) })
      broadcast({ type: 'version', version: clone(version) })
    }, stepMs)
  }, queueDelay)
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
    a: { url: 'http://127.0.0.1:8188', gpu: 'RTX 3060', role: '3D', reachable: true, comfyui_version: '0.3.60', devices: [{ name: 'NVIDIA GeForce RTX 3060', type: 'cuda', vram_total: 12_884_901_888, vram_free: 9_126_805_504 }] },
    b: { url: 'http://127.0.0.1:8189', gpu: 'RTX 3080', role: 'images, rigging', reachable: true, comfyui_version: '0.3.60', devices: [{ name: 'NVIDIA GeForce RTX 3080', type: 'cuda', vram_total: 10_737_418_240, vram_free: 4_294_967_296 }] },
  },
  host: { ram_total_bytes: 68_719_476_736, ram_free_bytes: 30_064_771_072, ram_free_gb: 28.0, min_free_ram_gb: 8, cpu_percent: 17 },
  queue: {
    a: { paused: false, current_job_id: store.jobs.find((j) => j.instance === 'a' && j.status === 'running')?.id ?? null },
    b: { paused: false, current_job_id: store.jobs.find((j) => j.instance === 'b' && j.status === 'running')?.id ?? null },
  },
}))
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
  return { model, model_label: labels[model] ?? model, reason: `${reasons[model] ?? 'it fits this style'} for ${preset.label.toLowerCase()} on ${platform.replace('_', ' ')}`, budgets, estimates }
})

on('GET', '/api/projects', () => store.projects)
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
    selected: {},
    stages: {},
  }
  store.assets.push(a)
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
    const v = newVersion(a, stage, parent, params)
    const job = startJob(a, v, `${stage}.run`)
    results.push({ job: clone(job), version: clone(v) })
  }
  return count === 1 ? results[0] : results
})
on('POST', '/api/versions/:vid/refine', (m, body) => {
  const { asset, version } = findVersion(m.groups!.vid)
  const instruction = String(body.instruction ?? '').trim()
  if (!instruction) throw new HttpError(422, 'instruction is required')
  const params: Record<string, unknown> = { ...version.params, ...((body.params as Record<string, unknown>) ?? {}), instruction, seed: Math.floor(Math.random() * 100000) }
  if (body.view) params.view = body.view
  const v = newVersion(asset, version.stage, version.id, params)
  const job = startJob(asset, v, `${version.stage}.refine`)
  return { job: clone(job), version: clone(v) }
})
on('POST', '/api/versions/:vid/adjust', (m, body) => {
  const { asset, version } = findVersion(m.groups!.vid)
  const params = { ...version.params, ...((body.params as Record<string, unknown>) ?? {}) }
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
  const { version } = findVersion(m.groups!.vid)
  version.metrics = { ...version.metrics, judge: body.mode ?? 'metrics', judged_at: nowIso() }
  return version
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
      apStop(asset, 'paused', 'The run was cancelled.')
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
  st.stages_done = STAGES.filter((s) => !!a.selected[s])
  const fix = body.fix as { stage?: string; params?: Record<string, unknown> } | undefined
  if (fix && STAGES.includes(fix.stage as StageId)) {
    apRunStage(a, fix.stage as StageId, { stage: fix.stage as StageId, params: fix.params ?? {} })
  } else {
    apStep(a)
  }
  return apState(a)
})
on('POST', '/api/assets/:id/autopilot/stop', (m) => {
  const a = findAsset(m.groups!.id)
  const st = apState(a)
  if (st.status === 'running') {
    if (st.job_id) {
      const job = store.jobs.find((j) => j.id === st.job_id)
      if (job && (job.status === 'queued' || job.status === 'running')) {
        job.status = 'cancelled'
        job.message = 'cancelled'
        job.finished_at = nowIso()
        if (job.version_id) {
          try {
            const { version } = findVersion(job.version_id)
            version.status = 'error'
            version.error = 'cancelled'
            broadcast({ type: 'version', version: clone(version) })
          } catch {
            // no version
          }
        }
        broadcast({ type: 'job', job: clone(job) })
      }
    }
    apStop(a, 'paused', 'You stopped the autopilot.')
  }
  return apState(a)
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
  if (job.status === 'queued' || job.status === 'running') {
    job.status = 'cancelled'
    job.message = 'cancelled'
    job.finished_at = nowIso()
    if (job.version_id) {
      try {
        const { version } = findVersion(job.version_id)
        version.status = 'error'
        version.error = 'cancelled'
        broadcast({ type: 'version', version: clone(version) })
      } catch {
        // no version
      }
    }
    broadcast({ type: 'job', job: clone(job) })
  }
  return job
})

function dispatch(method: string, pathname: string, body: Record<string, unknown>, query: URLSearchParams): { status: number; body: unknown } {
  for (const r of routes) {
    if (r.method !== method) continue
    const m = pathname.match(r.re)
    if (!m) continue
    try {
      return { status: 200, body: clone(r.handler(m, body, query)) }
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
    const res = dispatch(method, u.pathname, body, u.searchParams)
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
