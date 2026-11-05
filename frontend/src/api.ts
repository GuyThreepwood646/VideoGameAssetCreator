// Typed client for the VGAC backend contract (docs/API.md).

export const STAGES = ['concept', 'turnaround', 'mesh', 'prep', 'rig', 'export'] as const
export type StageId = (typeof STAGES)[number]

export const STAGE_LABELS: Record<StageId, string> = {
  concept: 'Concept',
  turnaround: 'Turnaround',
  mesh: 'Mesh',
  prep: 'Prep',
  rig: 'Rig',
  export: 'Export',
}

export const TURNAROUND_VIEWS = ['front', 'left', 'back', 'right', 'quarter', 'top'] as const
export type ViewId = (typeof TURNAROUND_VIEWS)[number]

// Seconds each stage usually takes, used only until GET /api/stages (estimate_seconds) has loaded or when it is
// unreachable. The backend's guidance.STAGE_GUIDE is the source; these are its numbers (measured, STATUS.md).
export const STAGE_ESTIMATE_FALLBACK: Record<StageId, number> = { concept: 25, turnaround: 180, mesh: 200, prep: 20, rig: 90, export: 120 }

// Seconds a stage usually takes: estimate_seconds from GET /api/stages, else the fallback above.
export function stageEstimateSeconds(stage: StageId, info?: Pick<StageInfo, 'estimate_seconds'> | null): number {
  const s = info?.estimate_seconds
  return typeof s === 'number' && s > 0 ? s : STAGE_ESTIMATE_FALLBACK[stage]
}

// The concept models as of this build. The backend's data/models.yaml is the source and GET /api/models serves it
// (api.models()); this list is the fallback for the pickers and the mock.
export const STYLE_MODELS = ['zturbo', 'zbase', 'klein', 'anima', 'pixel', 'chroma'] as const
export type StyleModel = (typeof STYLE_MODELS)[number]

// One concept model (GET /api/models), from the backend's data/models.yaml.
export type ModelInfo = {
  id: string
  label: string
  description: string
  name?: string
  licence: string
  version?: string
  default_steps: number | null
  negative?: boolean
}

export const PLATFORMS = ['mobile_low', 'pc_mid', 'high'] as const
export type PlatformId = (typeof PLATFORMS)[number]

export type StyleLock = {
  prompt_block: string
  model: StyleModel
  loras: string[]
  palette: string[]
  negative: string
  // The ready-made look this lock was filled from (GET /api/style-presets id), if any.
  preset?: string | null
}

// A ready-made look (GET /api/style-presets): fills the project's style lock and
// names the asset style (style_preset) new assets in the project default to.
export type LookPreset = {
  id: string
  label: string
  description: string
  style_preset: string
  style_lock: StyleLock
  example_prompt?: string
}

// Autopilot: runs every stage once from the first stage without a kept result,
// keeps results that pass, and pauses only where something needs a person.
export type AutopilotStatus = 'idle' | 'running' | 'paused' | 'done' | 'error'
export type StopOn = 'block' | 'warn' | 'never'

export type AutopilotState = {
  status: AutopilotStatus
  stage: StageId | string | null
  job_id: string | null
  version_id: string | null
  reason: string | null
  stop_on: StopOn | string
  until_stage: StageId | string
  started_at?: string | null
  updated_at?: string | null
  stages_done: string[]
}

export type AutopilotBody = {
  until_stage?: StageId
  stop_on?: StopOn
  // Proposed addition (see README): run this stage once more with these params
  // (an adjust of its kept version) before continuing. Used by "Fix and continue".
  fix?: { stage: StageId; params: Record<string, unknown> }
}

export type Project = {
  id: string
  name: string
  created_at: string
  style_lock: StyleLock
  platform_target: PlatformId
  // Decision D12: the Unity project every export is imported into (absolute folder), or null.
  unity_project_path?: string | null
}

export type VersionStatus = 'queued' | 'running' | 'done' | 'error' | 'rejected'

// How the app can fix a failing check. A re-run fix names a stage and params; a tool fix (`ui`) opens an editor
// instead (2.5): the parts editor, or the front picker in prep. `label` is the button text (fix_label in the
// backend); without one the button reads "Fix automatically". `knob` is an optional Fine-tune setting to focus.
export type FixTool = 'parts_editor' | 'front_picker'
export type CheckFix = { stage?: string; params?: Record<string, unknown>; label?: string; ui?: FixTool | string; knob?: string }

// A quality check. The backend sends {id, name, passed, ok, value, limit, message}
// and may add help (plain sentence), fix (how the app can fix it) and severity.
export type Check = {
  name: string
  id?: string
  passed?: boolean
  ok?: boolean
  value?: unknown
  limit?: unknown
  message?: string
  help?: string
  fix?: CheckFix | null
  // The Fix button's words when the backend puts them on the check rather than on fix.label.
  fix_label?: string
  severity?: 'block' | 'warn'
  // Extra values the sentence was filled from (axis_word, missing, effect, ...).
  vars?: Record<string, unknown>
}

// One line of the finish card's "What you get" list (metrics.finish_lines on the export version, 2.4).
// `check` names a check whose Fix button a warn line shows; without it the line's id is tried.
export type FinishLine = { id: string; text: string; state: 'ok' | 'warn'; term?: string; check?: string }

export type Version = {
  id: string
  asset_id: string
  stage: StageId
  parent_version_id: string | null
  status: VersionStatus
  params: Record<string, unknown>
  files: Record<string, string>
  metrics: Record<string, unknown>
  job_id: string | null
  error: string | null
  created_at: string
  selected: boolean
}

export type Asset = {
  id: string
  project_id: string
  name: string
  category: string
  style_preset: string
  platform_target: PlatformId
  prompt: string
  parts: string[]
  created_at: string
  // The painter chosen on the creation form; the default for concept runs.
  concept_model?: string | null
  // Variants: an asset derived from another with a change in the prompt.
  base_asset_id?: string | null
  variant_change?: string | null
  // Autopilot state, when the backend has the feature (null when idle).
  autopilot?: AutopilotState | null
  selected: Partial<Record<StageId, string>>
  stages: Partial<Record<StageId, Version[]>>
}

// The asset list may or may not carry the stage versions; the UI copes with both.
export type AssetSummary = Omit<Asset, 'stages'> & { stages?: Asset['stages'] }

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export type Job = {
  id: string
  instance: 'a' | 'b' | 'host'
  kind: string
  asset_id: string | null
  version_id: string | null
  status: JobStatus
  progress: number
  message: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export type Category = {
  id: string
  label: string
  rig_engine: string
  symmetric: boolean
  pose_block: string
  size_m: number
  parts_default: string[]
  budgets: Record<string, { triangles: number; texture: number }>
  // Null when the category file leaves them out.
  views?: string[] | null
  lod_ratios?: number[] | null
  topology?: 'quad' | 'triangle' | 'none' | string | null
  input_view_default?: 'concept' | 'front' | 'quarter' | string | null
  // Stages this category does not have (material: turnaround). Runs build on the stage before them.
  skips?: string[]
  // How size_m is measured (3.2): height is the Y extent, horizontal the larger of X and Z, longest the largest of
  // all three; grid means the grid sets it (environment). With the question and hint the size knob shows.
  size_axis?: 'height' | 'horizontal' | 'longest' | 'grid' | string | null
  size_label?: string | null
  size_hint?: string | null
  // "tall", "long" or "across".
  size_word?: string | null
}

export type StylePreset = { id: string; label: string; model: string; prompt_block: string }

export type Platform = {
  id: string
  label: string
  triangles_character: number
  triangles_prop: number
  texture: number
}

// One entry of a stage's adjust_schema. "JSON-schema-like list of params with
// type/min/max/default": the UI accepts a few aliases (key/id for name, enum for options).
export type AdjustParam = {
  name: string
  label?: string
  type: 'number' | 'integer' | 'boolean' | 'select' | 'multiselect' | 'string'
  min?: number
  max?: number
  step?: number
  default?: unknown
  options?: (string | number)[]
  description?: string
  help?: HelpText
  simple?: boolean
  // Plain words for select and multiselect values ({value: label}); help.ts OPTION_LABELS is the fallback.
  option_labels?: Record<string, string>
  // The categories this knob applies to; absent means every category (2.3).
  categories?: string[]
  // A one-line hint shown under the control, when the backend has one.
  hint?: string
  // Per-category settings of a knob (prep size_m, 3.2): the axis it measures, its default, the axis word and the
  // question and hint the control shows. Without a categories list the knob applies to the categories listed here.
  by_category?: Record<string, { axis?: 'height' | 'horizontal' | 'longest' | string; default?: number; word?: 'tall' | 'long' | 'across' | string; label?: string; hint?: string }>
}

export type HelpText = { meaning?: string; why?: string; technical?: string }

export type StageInfo = {
  id: StageId
  label: string
  adjust_schema: AdjustParam[]
  plain_name?: string
  plain_verb?: string
  help?: HelpText
  estimate_seconds?: number
}

export type Suggestion = {
  model: string
  model_label: string
  reason: string
  budgets: { triangles: number; texture: number }
  estimates: Record<string, number>
  total_minutes?: number
  platform_label?: string
  category_label?: string
}

// Thumbs up or down on a finished version (POST /api/versions/{vid}/feedback),
// stored in metrics.feedback. It only records which results the person liked.
export type Thumbs = 'up' | 'down' | null
export type Feedback = { thumbs: Thumbs; note?: string | null; at?: string | null }
export type FeedbackBody = { thumbs: Thumbs; note?: string }

export function versionFeedback(v: Pick<Version, 'metrics'>): Feedback | null {
  const f = v.metrics?.feedback
  if (!f || typeof f !== 'object') return null
  const fb = f as { thumbs?: unknown; note?: unknown; at?: unknown }
  const thumbs = fb.thumbs === 'up' || fb.thumbs === 'down' ? fb.thumbs : null
  return { thumbs, note: typeof fb.note === 'string' ? fb.note : null, at: typeof fb.at === 'string' ? fb.at : null }
}

// One finished asset in the gallery (GET /api/gallery): every asset with a
// finished export version, newest first. Paths are library-relative.
export type GalleryItem = {
  asset_id: string
  asset_name: string
  project_id: string
  project_name: string
  category: string
  style_preset: string
  platform_target: PlatformId | string
  created_at: string
  finished_at: string | null
  export_version_id?: string
  thumbnail: string | null
  preview: string | null
  delivered_to: string | null
  fbx: string | null
  glb: string | null
}

export type ServerEvent =
  | { type: 'job'; job: Job }
  | { type: 'version'; version: Version }
  | { type: 'autopilot'; asset_id: string; autopilot: AutopilotState }
  | { type: 'gpus'; gpus: Gpu[] }

// Graphics cards (GET /api/gpus). A card turned off hands its work to the other card; a step only it can do waits.
// Kinds of work a card may do (Advanced): pictures = concept, turnaround and material steps; model = mesh;
// rigging = rig; review = the local judge.
export const GPU_KINDS = ['pictures', 'model', 'rigging', 'review'] as const
export type GpuKind = (typeof GPU_KINDS)[number]
export type GpuCurrentJob = {
  id: string
  kind: string
  stage: string
  op: string
  material: boolean
  asset_id: string | null
  asset_name: string | null
  progress: number
  message: string
}
export type Gpu = {
  instance: string
  gpu: string
  url: string
  enabled: boolean
  reachable: boolean
  vram_total_mb?: number | null
  vram_free_mb?: number | null
  current_job_id: string | null
  current_job?: GpuCurrentJob | null
  queued: number
  can_run: string[]
  note: string
  allowed_kinds: GpuKind[]
  possible_kinds: GpuKind[]
  effective_kinds: GpuKind[]
  kind_notes: Partial<Record<GpuKind, string>>
  free_after_job: boolean
  // The card's ComfyUI: started by itself when a step needs it (when can_start), stopped only if the app started it
  // (owned). "off" when the card is turned off in the app.
  state?: GpuEngineState
  owned?: boolean
  can_start?: boolean
  // Shut down an engine the app started after this many minutes without work; 0 = never.
  stop_when_idle_minutes?: number
  last_error?: string
}
export type GpuEngineState = 'running' | 'starting' | 'stopped' | 'failed' | 'off'
export type GpuPatch = { enabled?: boolean; allowed_kinds?: GpuKind[]; free_after_job?: boolean; stop_when_idle_minutes?: number }

export type RunStageBody = { parent_version_id?: string; params?: Record<string, unknown>; count?: number }
export type RefineBody = { instruction: string; view?: string; mask_png?: string; params?: Record<string, unknown> }
export type DeleteResult = { ok: boolean; deleted: string }
export type RunResult = { job: Job; version: Version }
export type VariantResult = { asset: Asset; job: Job; version: Version }

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail: unknown = null
    let message = `${method} ${path} -> HTTP ${res.status}`
    try {
      detail = await res.json()
      const d = detail as { detail?: unknown; error?: unknown; message?: unknown }
      const text = d?.detail ?? d?.error ?? d?.message
      if (typeof text === 'string') message = text
      else if (text) message = JSON.stringify(text)
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, message, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const get = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {})
const put = <T>(path: string, body: unknown) => request<T>('PUT', path, body)
const del = <T>(path: string) => request<T>('DELETE', path)

const q = (params: Record<string, string | undefined>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) s.set(k, v)
  const str = s.toString()
  return str ? '?' + str : ''
}

// Health (shape defined by the phase 0 backend)
export type HealthDevice = { name: string | null; type: string | null; vram_total: number | null; vram_free: number | null }
export type HealthInstance = {
  url: string
  gpu: string
  role: string
  reachable: boolean
  error?: string
  comfyui_version?: string | null
  devices: HealthDevice[]
  // False when the card is turned off in the app (Graphics cards).
  enabled?: boolean
}
export type Health = {
  ok: boolean
  version: string
  instances: Record<string, HealthInstance>
  host: { ram_total_bytes: number; ram_free_bytes: number; ram_free_gb: number; min_free_ram_gb: number; cpu_percent: number }
  queue: Record<string, { paused: boolean; enabled?: boolean; current_job_id: string | null; queued?: number; next_job_id?: string | null }>
}

export const api = {
  // Reference data
  health: () => get<Health>('/api/health'),
  gpus: () => get<Gpu[]>('/api/gpus'),
  setGpu: (instance: string, body: GpuPatch) => put<Gpu[]>(`/api/gpus/${instance}`, body),
  startGpu: (instance: string) => post<Gpu[]>(`/api/gpus/${instance}/start`),
  stopGpu: (instance: string) => post<Gpu[]>(`/api/gpus/${instance}/stop`),
  categories: () => get<Category[]>('/api/categories').then((list) => { rememberSkips(list); return list }),
  styles: () => get<StylePreset[]>('/api/styles'),
  platforms: () => get<Platform[]>('/api/platforms'),
  stages: () => get<StageInfo[]>('/api/stages'),
  models: () => get<ModelInfo[]>('/api/models'),
  suggest: (params: { category?: string; style?: string; platform?: string }) => get<Suggestion>('/api/suggest' + q(params)),
  stylePresets: () => get<LookPreset[]>('/api/style-presets'),

  // Projects
  listProjects: () => get<Project[]>('/api/projects'),
  createProject: (body: { name: string; style_lock?: Partial<StyleLock>; platform_target?: PlatformId; unity_project_path?: string | null }) =>
    post<Project>('/api/projects', body),
  getProject: (id: string) => get<Project>(`/api/projects/${id}`),
  updateProject: (id: string, body: Partial<Pick<Project, 'name' | 'style_lock' | 'platform_target' | 'unity_project_path'>>) =>
    put<Project>(`/api/projects/${id}`, body),
  deleteProject: (id: string) => del<DeleteResult>(`/api/projects/${id}`),

  // Assets
  listAssets: (pid: string) => get<AssetSummary[]>(`/api/projects/${pid}/assets`),
  createAsset: (
    pid: string,
    body: { name: string; category: string; style_preset: string; platform_target?: PlatformId; prompt: string; parts?: string[]; concept_model?: string },
  ) => post<Asset>(`/api/projects/${pid}/assets`, body),
  getAsset: (id: string) => get<Asset>(`/api/assets/${id}`),
  createVariant: (aid: string, body: { change: string; name?: string }) => post<VariantResult>(`/api/assets/${aid}/variants`, body),
  updateAsset: (id: string, body: Partial<Pick<Asset, 'name' | 'prompt' | 'category' | 'parts' | 'platform_target'>>) =>
    put<Asset>(`/api/assets/${id}`, body),
  deleteAsset: (id: string) => del<DeleteResult>(`/api/assets/${id}`),

  // Autopilot
  startAutopilot: (aid: string, body: AutopilotBody = {}) => post<AutopilotState>(`/api/assets/${aid}/autopilot`, body),
  getAutopilot: (aid: string) => get<AutopilotState>(`/api/assets/${aid}/autopilot`),
  stopAutopilot: (aid: string) => post<AutopilotState>(`/api/assets/${aid}/autopilot/stop`),

  // Stages and versions
  runStage: (assetId: string, stage: StageId, body: RunStageBody = {}) =>
    post<RunResult | RunResult[]>(`/api/assets/${assetId}/stages/${stage}/run`, body),
  refine: (vid: string, body: RefineBody) => post<RunResult>(`/api/versions/${vid}/refine`, body),
  adjust: (vid: string, params: Record<string, unknown>) => post<RunResult>(`/api/versions/${vid}/adjust`, { params }),
  select: (vid: string) => post<Version>(`/api/versions/${vid}/select`),
  reject: (vid: string) => post<Version>(`/api/versions/${vid}/reject`),
  getVersion: (vid: string) => get<Version>(`/api/versions/${vid}`),
  // In local mode the judging runs as a queued job, returned as judge_job.
  judge: (vid: string, mode: 'metrics' | 'local') => post<Version & { judge_job?: Job }>(`/api/versions/${vid}/judge`, { mode }),
  feedback: (vid: string, body: FeedbackBody) => post<Version>(`/api/versions/${vid}/feedback`, body),

  // Gallery: every finished asset across projects
  gallery: () => get<GalleryItem[]>('/api/gallery'),

  // Jobs
  listJobs: (params: { asset_id?: string; status?: string } = {}) => get<Job[]>('/api/jobs' + q(params)),
  getJob: (id: string) => get<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
}

export function eventsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/ws/events`
}

// Library files. The mock registers a resolver so generated placeholder files
// (data URLs) can stand in for library paths.
let libraryResolver: ((path: string) => string | null) | null = null
export function setLibraryResolver(fn: ((path: string) => string | null) | null) {
  libraryResolver = fn
}

export function libraryUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined
  if (/^(data:|blob:|https?:\/\/|\/)/.test(path)) return path
  const resolved = libraryResolver?.(path)
  if (resolved) return resolved
  return '/api/library/' + path.split('/').map(encodeURIComponent).join('/')
}

// Helpers shared by the screens
export function versionsOf(asset: Pick<Asset, 'stages'> | AssetSummary, stage: StageId): Version[] {
  return asset.stages?.[stage] ?? []
}

export function selectedVersion(asset: AssetSummary, stage: StageId): Version | undefined {
  const id = asset.selected?.[stage]
  const list = versionsOf(asset, stage)
  return list.find((v) => v.id === id) ?? list.find((v) => v.selected)
}

// The stage a run of `stage` builds on: the one before it, walking back over stages the category skips
// (material skips turnaround), the same rule as the backend. Without a category it is strictly the one before.
// Stages each category skips, filled from the categories call; the material default covers mock mode and the
// moment before the first fetch.
const CATEGORY_SKIPS: Record<string, string[]> = { material: ['turnaround'] }
export function rememberSkips(list: Category[]): void {
  for (const c of list) CATEGORY_SKIPS[c.id] = c.skips ?? []
}

export function previousStage(stage: StageId, category?: Pick<Category, 'skips'> | string | null): StageId | null {
  const skips = new Set(typeof category === 'string' ? CATEGORY_SKIPS[category] ?? [] : category?.skips ?? [])
  let i = STAGES.indexOf(stage) - 1
  while (i > 0 && skips.has(STAGES[i])) i -= 1
  return i >= 0 ? STAGES[i] : null
}

export type StageState = 'none' | 'running' | 'done' | 'error'

export function stageState(asset: AssetSummary, stage: StageId): StageState {
  const list = versionsOf(asset, stage)
  if (list.some((v) => v.status === 'running' || v.status === 'queued')) return 'running'
  if (selectedVersion(asset, stage)?.status === 'done') return 'done'
  if (list.some((v) => v.status === 'done')) return 'done'
  if (list.length && list[list.length - 1].status === 'error') return 'error'
  return 'none'
}

export function isMock(): boolean {
  return import.meta.env.VITE_MOCK === '1'
}
