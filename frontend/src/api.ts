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
  // The person pressed Stop: the card shows Resume, which carries on from the same step.
  stopped_by_user?: boolean
}

export type AutopilotBody = {
  until_stage?: StageId
  stop_on?: StopOn
  // Proposed addition (see README): run this stage once more with these params
  // (an adjust of its kept version) before continuing. Used by "Fix and continue".
  fix?: { stage: StageId; params: Record<string, unknown>; op?: 'refine'; instruction?: string }
  // Go back to this step: it is made again (built on what is kept before it) and every step after it is
  // rebuilt. The old results stay in the history, set aside.
  from_stage?: StageId
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
export type FixTool = 'parts_editor' | 'front_picker' | 'hair_picker'
// op "refine": run the refine on the version the check is on with `instruction` ("Redraw it ready for 3D").
export type CheckFix = { stage?: string; params?: Record<string, unknown>; label?: string; ui?: FixTool | string; knob?: string; op?: 'refine'; instruction?: string }

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
  // Stopped by the person (the Stop button). Resume puts it back in the queue with the same settings.
  stopped?: boolean
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
  // Items: on an item, what it is and whose; on a character, the items set aside and attached.
  held_item?: HeldItem | null
  equipment?: Equipment | null
  // Hair from the library: the choice and the pick (absent or null: the hair is built with the 3D model, as before).
  hair?: HairChoice | null
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
  // What a job that makes no version hands back (style from a picture); null otherwise.
  result?: StyleResult | null
}

// Start from your own images (docs/API.md "your own pictures"). Pictures travel as data: URLs in JSON.
export type Swatch = { hex: string; share: number }
export type PaletteResult = { palette: string[]; swatches: Swatch[]; background: string | null; pixels: number }
export type StyleResult = { description: string; words: number; palette: string[]; swatches: Swatch[]; model: string; applied: boolean }
export type StyleFromImage = PaletteResult & { job: Job | null; needs_review_engine: boolean; message?: string }

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
  type: 'number' | 'integer' | 'boolean' | 'select' | 'multiselect' | 'string' | 'addons'
  // type 'addons' (concept): every concept add-on the app knows. The value is {id: off | subtle | normal | strong}.
  catalog?: AddonEntry[]
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
  // The recipe's plain sentence ("Pixel-art character: small crisp textures ..."), when the backend has recipes.
  recipe_plain?: string
  recipe_notes?: string[]
}

// Recipes (GET /api/recipe, backend reference.recipe_public): how the chosen look carries through every step.
export type StrengthName = 'subtle' | 'normal' | 'strong'
export type AddonChoice = 'off' | StrengthName
export type AddonEntry = {
  id: string
  label: string
  help: HelpText
  stage: 'concept' | 'turnaround'
  base_models: string[]
  strengths: Record<StrengthName, number>
  default_strength: StrengthName
  required: boolean
  required_for: string[]
  trigger: string
  licence: string
  source: string
  // Only on GET /api/recipe addon_entries: whether the recipe switches it on, and at which step.
  on?: boolean
  strength?: StrengthName
  // True for the person's own add-ons (GET /api/my-addons), which also carry their file name in VGAC/mine/.
  mine?: boolean
  file_name?: string
  added_at?: string
}

// Your own add-ons (backend user_addons.py, docs/API.md "your own LoRAs").
export type MyAddonsListing = {
  addons: AddonEntry[]
  // The one folder the app keeps add-on files in (VGAC/mine/ inside ComfyUI's loras folder).
  folder: string
  folder_exists: boolean
  strengths: Record<StrengthName, number>
  painters: { id: string; label: string }[]
}
// What the short form can start with, read from the file's header (never its data).
export type AddonPrefill = { label: string; base_models: string[]; trigger: string; family: string; painter_note: string }
export type UploadedAddon = { file_name: string; bytes: number; prefill: AddonPrefill }
export type UnregisteredAddon = { file_name: string; bytes: number; prefill?: AddonPrefill; error?: string }
export type MyAddonIn = { file_name: string; label: string; base_models: string[]; trigger?: string; default_strength?: StrengthName; source?: string }
export type Recipe = {
  id: string
  style: string
  category: string
  platform: string
  plain: string
  concept: { model: string; steps: number | null; prompt_block: string; negative: string }
  addons: { id: string; strength: StrengthName }[]
  turnaround: { lora_strength: number; prompt_suffix: string }
  mesh: {
    triangle_scale: number
    triangle_floor: number
    triangles: number | null
    budget_triangles: number | null
    texture_cap: number | null
    texture: number | null
    budget_texture: number | null
    normal_map: boolean
  }
  surface: { shading: 'smooth' | 'flat'; texture_filter: 'smooth' | 'crisp'; palette_colors: number | null; pixel_snap: boolean; delight: string; pbr_fix: boolean }
  engine: { filter_mode: 'bilinear' | 'point'; mipmaps: boolean; compress: boolean }
  addon_entries: AddonEntry[]
}

// metrics.unity_project_import on an export version. "sent": the project was open, so the files wait in the project
// for the open Editor to import them; "imported" or "failed" once it has. Older exports have only ok and error.
export type UnityHandoffState = 'sent' | 'imported' | 'failed'
export type UnityProjectImport = {
  ok?: boolean | null
  state?: UnityHandoffState
  project?: string
  request_id?: string
  folder?: string
  prefab?: string
  error?: string
  note?: string
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
  // True when the card's ComfyUI was found at another address than the configured one (config discover).
  found_by_card?: boolean
  // A plain setup problem, for example another ComfyUI on the other card holding this card's port.
  setup_problem?: string
}

// GET /api/setup: "Is everything ready?" (backend/vgac/preflight.py).
export type SetupState = 'ok' | 'warn' | 'fail' | 'unknown'
export type SetupGroup = 'tools' | 'engines' | 'models' | 'storage'
export type SetupItem = {
  id: string
  group: SetupGroup
  label: string
  state: SetupState
  detail: string
  fix: string
  needed_for: string[]
  technical: string
}
export type SetupReport = {
  summary: { ok: boolean; fails: number; warns: number; unknown: number; affected: string[] }
  items: SetupItem[]
  groups: { id: SetupGroup; label: string }[]
  checked_at: string
}
export type GpuEngineState = 'running' | 'starting' | 'stopped' | 'failed' | 'off'
export type GpuPatch = { enabled?: boolean; allowed_kinds?: GpuKind[]; free_after_job?: boolean; stop_when_idle_minutes?: number }

export type RunStageBody = { parent_version_id?: string; params?: Record<string, unknown>; count?: number }
// reference_image (experimental): a data: URL the concept refine passes to the edit model as a second picture.
export type RefineBody = { instruction: string; view?: string; mask_png?: string; params?: Record<string, unknown>; reference_image?: string }
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
  setup: (refresh = false) => get<SetupReport>('/api/setup' + (refresh ? '?refresh=1' : '')),
  categories: () => get<Category[]>('/api/categories').then((list) => { rememberSkips(list); return list }),
  styles: () => get<StylePreset[]>('/api/styles'),
  platforms: () => get<Platform[]>('/api/platforms'),
  stages: () => get<StageInfo[]>('/api/stages'),
  models: () => get<ModelInfo[]>('/api/models'),
  suggest: (params: { category?: string; style?: string; platform?: string }) => get<Suggestion>('/api/suggest' + q(params)),
  recipe: (params: { style: string; category: string; platform: string }) => get<Recipe>('/api/recipe' + q(params)),
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
    // image: start from a picture (a data: URL); the prompt may then be empty.
    body: { name: string; category: string; style_preset: string; platform_target?: PlatformId; prompt: string; parts?: string[]; concept_model?: string; image?: string; items?: 'separate' | 'keep'; hair?: HairSource },
  ) => post<Asset & { upload?: RunResult }>(`/api/projects/${pid}/assets`, body),

  // Your own pictures
  uploadConcept: (aid: string, body: { image: string; note?: string }) => post<RunResult>(`/api/assets/${aid}/stages/concept/upload`, body),
  uploadView: (vid: string, view: string, image: string) => post<RunResult>(`/api/versions/${vid}/views/${view}/upload`, { image }),
  paletteFromImage: (image: string, colors: 8 | 16 | 32 = 8) => post<PaletteResult>('/api/palette-from-image', { image, colors }),
  styleFromImage: (pid: string, image: string) => post<StyleFromImage>(`/api/projects/${pid}/style-from-image`, { image }),
  getAsset: (id: string) => get<Asset>(`/api/assets/${id}`),
  createVariant: (aid: string, body: { change: string; name?: string }) => post<VariantResult>(`/api/assets/${aid}/variants`, body),
  updateAsset: (id: string, body: Partial<Pick<Asset, 'name' | 'prompt' | 'category' | 'parts' | 'platform_target'>>) =>
    put<Asset>(`/api/assets/${id}`, body),
  deleteAsset: (id: string) => del<DeleteResult>(`/api/assets/${id}`),

  // Autopilot
  startAutopilot: (aid: string, body: AutopilotBody = {}) => post<AutopilotState>(`/api/assets/${aid}/autopilot`, body),
  getAutopilot: (aid: string) => get<AutopilotState>(`/api/assets/${aid}/autopilot`),
  stopAutopilot: (aid: string) => post<AutopilotState>(`/api/assets/${aid}/autopilot/stop`),
  // Stop and Resume (the buttons on every step). Stop acts now: one step when `stage` is given and the autopilot
  // is not running, else everything for the asset. Resume runs the stopped step again with the same settings.
  stopAsset: (aid: string, stage?: StageId) => post<{ stopped: string[]; autopilot: AutopilotState }>(`/api/assets/${aid}/stop`, stage ? { stage } : {}),
  resumeAsset: (aid: string, stage?: StageId) => post<{ resumed: Version[]; autopilot: AutopilotState }>(`/api/assets/${aid}/resume`, stage ? { stage } : {}),
  dismissStopped: (aid: string, stage: StageId) => post<{ dismissed: number }>(`/api/assets/${aid}/stopped/dismiss`, { stage }),

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

  // Your own add-ons
  myAddons: () => get<MyAddonsListing>('/api/my-addons'),
  addMyAddon: (body: MyAddonIn) => post<AddonEntry>('/api/my-addons', body),
  removeMyAddon: (id: string) => del<{ removed: string; file_name: string; file_kept: boolean }>(`/api/my-addons/${encodeURIComponent(id)}`),
  unregisteredAddons: () => get<UnregisteredAddon[]>('/api/my-addons/unregistered'),
  openAddonFolder: () => post<{ opened: boolean; folder: string }>('/api/my-addons/open-folder'),
  uploadAddon: (file: File, onProgress?: (fraction: number) => void) => uploadAddonFile(file, onProgress),
}

// POST /api/my-addons/upload: the file itself is the body, streamed from disk by the browser (never read into the
// page). XMLHttpRequest reports upload progress; the mock backend only intercepts fetch, so mock mode uses fetch.
function uploadAddonFile(file: File, onProgress?: (fraction: number) => void): Promise<UploadedAddon> {
  const path = '/api/my-addons/upload?name=' + encodeURIComponent(file.name)
  if (isMock()) return request<UploadedAddon>('POST', path + '&bytes=' + file.size)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.setRequestHeader('content-type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
    }
    xhr.onerror = () => reject(new ApiError(0, 'The file could not be sent to the app. Is the app still running?'))
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // not JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as UploadedAddon)
      else {
        const d = (body as { detail?: unknown } | null)?.detail
        reject(new ApiError(xhr.status, typeof d === 'string' ? d : `The upload failed (HTTP ${xhr.status}).`, body))
      }
    }
    xhr.send(file)
  })
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

// The first step whose kept result was built from an older result of the step before it (an earlier step was
// changed afterwards). null when every kept result lines up.
export function firstStaleStage(asset: AssetSummary): StageId | null {
  const byId = new Map<string, Version>()
  for (const s of STAGES) for (const v of versionsOf(asset, s)) byId.set(v.id, v)
  for (const s of STAGES) {
    const kept = selectedVersion(asset, s)
    if (!kept || kept.status !== 'done') continue
    // Walk up through changes made inside the same step to the result it was first built from.
    let v: Version = kept
    for (let guard = 0; guard < 200 && v.parent_version_id; guard++) {
      const parent = byId.get(v.parent_version_id)
      if (!parent) break // built from another asset (a variant) or unknown
      if (parent.stage !== s) {
        const keptBefore = selectedVersion(asset, parent.stage)
        if (keptBefore && keptBefore.id !== parent.id) return s
        break
      }
      v = parent
    }
  }
  return null
}

export function isStopped(v: Version): boolean {
  return v.stopped === true || (v.status === 'rejected' && v.error === 'cancelled')
}

// The versions a Resume would bring back: the stopped ones at the end of the step's list. A stop that was
// followed by a fresh run is history.
export function stoppedVersions(asset: AssetSummary, stage: StageId): Version[] {
  const list = versionsOf(asset, stage)
  const out: Version[] = []
  for (let i = list.length - 1; i >= 0; i--) {
    if (!isStopped(list[i])) break
    out.unshift(list[i])
  }
  return out
}

export function isMock(): boolean {
  return import.meta.env.VITE_MOCK === '1'
}

// ---------------------------------------------------------------------------
// Add a move (rig step): the hand-made move library, a character's moves, your own animation files
// ---------------------------------------------------------------------------

export type MovePack = { id: string; label: string; licence: string; count: number }
export type MoveGroup = { id: string; label: string }
// One clip in the picker. preview: a PNG strip with `frames` square cells side by side; `still` is the cell shown
// when it is not playing.
export type MoveClip = {
  id: string
  pack: string
  action: string
  label: string
  group: string
  loop: boolean
  seconds: number
  name: string
  moves_character?: boolean
  preview: string | null
  frames: number
  still: number
}
export type MoveLibrary = { packs: MovePack[]; groups: MoveGroup[]; clips: MoveClip[]; previews: { ready: number; total: number; making: boolean } }
export type CharacterMove = { name: string; label: string; clip: string | null; source: 'library' | 'mine' | 'built-in'; loop: boolean; added: boolean; default: boolean; made: boolean }
export type CharacterMoves = {
  can_add: boolean
  reason: string
  instead: string[]
  rig_version: string | null
  busy: boolean
  moves: CharacterMove[]
  added: { clip: string; name: string; loop: boolean }[]
  removed: string[]
  others: { asset_id: string; name: string; added: number; removed: number }[]
}
export type MyMoveClip = { id: string; action: string; label: string; loop: boolean; seconds: number; preview: string | null; frames: number; still: number }
export type MyMoveFile = {
  id: string
  label: string
  file_name: string
  kind: 'fbx' | 'glb' | 'bvh'
  source: string
  recognised: number
  total: number
  sentence: string
  missing: string[]
  clips: MyMoveClip[]
  added_at: string
}
export type MyMovesListing = { files: MyMoveFile[]; responsibility: string; max_mb: number; kinds: string[] }
export type MyMoveUpdate = { label?: string; source?: string; clips?: { action: string; label: string; loop: boolean }[] }

export const movesApi = {
  library: () => get<MoveLibrary>('/api/moves/library'),
  ofAsset: (aid: string) => get<CharacterMoves>(`/api/assets/${aid}/moves`),
  change: (aid: string, body: { add?: string[]; remove?: string[]; copy_from?: string }) => post<RunResult>(`/api/assets/${aid}/moves`, body),
  mine: () => get<MyMovesListing>('/api/my-moves'),
  updateMine: (id: string, body: MyMoveUpdate) => put<MyMoveFile>(`/api/my-moves/${encodeURIComponent(id)}`, body),
  removeMine: (id: string) => del<{ removed: string }>(`/api/my-moves/${encodeURIComponent(id)}`),
  uploadMine: (file: File, onProgress?: (fraction: number) => void) => uploadMoveFile(file, onProgress),
}

// POST /api/my-moves/upload: the file is the body (as for your own add-ons). The backend then reads it in Blender,
// so the answer comes some seconds after the last byte is sent.
function uploadMoveFile(file: File, onProgress?: (fraction: number) => void): Promise<MyMoveFile> {
  const path = '/api/my-moves/upload?name=' + encodeURIComponent(file.name)
  if (isMock()) return request<MyMoveFile>('POST', path + '&bytes=' + file.size)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.setRequestHeader('content-type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
    }
    xhr.onerror = () => reject(new ApiError(0, 'The file could not be sent to the app. Is the app still running?'))
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // not JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as MyMoveFile)
      else {
        const d = (body as { detail?: unknown } | null)?.detail
        reject(new ApiError(xhr.status, typeof d === 'string' ? d : `The file could not be added (HTTP ${xhr.status}).`, body))
      }
    }
    xhr.send(file)
  })
}

// --- Items (backend vgac/items.py): characters made empty-handed and "Add an item" ---------------------------------

export type ItemFound = { type: string; label: string; word: string; phrase: string; place: string; socket: string | null }
export type ItemDetect = { checked: boolean; items: ItemFound[]; worn: string[]; sentence?: string }
export type HeldItem = { type: string; label: string; kind?: string; phrase?: string; character_id?: string; character_name?: string }
export type Attachment = {
  id: string
  item_asset_id: string
  item_name: string
  type: string
  label: string
  socket: string
  stow_socket: string | null
  position: [number, number, number]
  rotation: [number, number, number, number]
  rotation_deg: [number, number, number]
  scale: number
  // Set by hand (nudges, numbers, size): "Fit it for me" then runs only when asked.
  touched?: boolean
  fit_request?: boolean
  // What the last fit to the moves changed (backend vgac/items.py equip), in plain words in `sentence`.
  fit?: AttachmentFit | null
  // The shield was moved to the forearm for the person (a one-time note), or a better place is offered.
  notice?: string
  offer?: { socket: string; place: string; why: string }
  offer_declined?: boolean
}
export type AttachmentFit = {
  outward_cm: number
  turn_deg: number
  tilt_deg: number[]
  before_m: number
  before_clip: string
  after_m: number
  after_clip: string
  cleared: boolean
  changed: boolean
  auto: boolean
  sentence: string
}
export type SetAsideItem = { type: string; label: string; phrase: string; item_asset_id: string }
export type Equipment = {
  choice: 'separate' | 'keep' | null
  found?: { type: string; label: string; phrase: string }[]
  set_aside: SetAsideItem[]
  attachments: Attachment[]
  build?: ItemsBuild | null
}
export type ItemsBuild = { status: 'items' | 'equipping' | 'done' | 'error' | 'stopped'; item?: string; reason?: string; version_id?: string; fitting?: string }
export type GripInfo = { found: boolean; confidence?: number; sure?: boolean; method?: string; reasons?: string[]; label?: string; error?: string }
export type ItemStatus = {
  id: string
  name: string
  status: 'done' | 'making' | 'paused' | 'error' | 'started' | 'not_started'
  stage: string | null
  reason: string | null
  held_item: HeldItem | null
  glb: string | null
  fbx: string | null
  export_version_id: string | null
  grip: GripInfo | null
  thumbnail: string | null
  autopilot: string | null
}
export type ItemPlace = { socket: string; label: string }
export type ItemsPanel = {
  asset_id: string
  can_add: boolean
  reason: string | null
  category: string
  places: ItemPlace[]
  equipment: Equipment
  items: Record<string, ItemStatus>
  character_glb: string | null
  saved: boolean
  needs_update: boolean
  build: ItemsBuild | null
  others: ItemStatus[]
  types: { id: string; label: string; place: string }[]
  limit: string
  step_cm: number
  step_deg: number
  fit?: { clear_limit_cm: number; push_max_cm: number; tilt_max_deg: number; help: string }
}
export type NudgeBody = { move?: [number, number, number]; turn?: [number, number, number]; slide?: number; scale_steps?: number; big?: boolean; reset?: boolean }

export const itemsApi = {
  detect: (prompt: string, category: string) => post<ItemDetect>('/api/items/detect', { prompt, category }),
  panel: (aid: string) => get<ItemsPanel>(`/api/assets/${aid}/items`),
  add: (aid: string, body: { type?: string; description?: string; item_asset_id?: string; socket?: string; build?: boolean }) =>
    post<{ attachment: Attachment; item: Asset; panel: ItemsPanel }>(`/api/assets/${aid}/items`, body),
  update: (aid: string, attId: string, body: { socket?: string; stow_socket?: string; position?: number[]; rotation_deg?: number[]; scale?: number; keep_place?: boolean; dismiss_notice?: boolean }) =>
    put<{ attachment: Attachment; panel: ItemsPanel }>(`/api/assets/${aid}/attachments/${attId}`, body),
  nudge: (aid: string, attId: string, body: NudgeBody) => post<{ attachment: Attachment }>(`/api/assets/${aid}/attachments/${attId}/nudge`, body),
  remove: (aid: string, attId: string) => del<{ removed: Attachment; panel: ItemsPanel }>(`/api/assets/${aid}/attachments/${attId}`),
  apply: (aid: string) => post<RunResult>(`/api/assets/${aid}/items/apply`),
  fit: (aid: string, attId: string) => post<RunResult & { attachment: Attachment }>(`/api/assets/${aid}/attachments/${attId}/fit`),
  build: (aid: string) => post<ItemsPanel>(`/api/assets/${aid}/items/build`),
}

// --- Hair from the library (docs/HAIR-LIBRARY-PLAN.md sections 1 and 5; backend vgac/hairlib.py) -------------------

export type HairSource = 'model' | 'library'
// Asset.hair (absent or null means today's hair, built with the 3D model).
export type HairChoice = { source: HairSource; pick?: string | null; picked_at?: string | null; changed_by?: 'form' | 'page' | 'fix' | string }
export type HairKind = 'shell' | 'cards'
// One hairstyle tried on the character at the 3D step: its picture on the character and its plain fit words.
export type HairCandidate = { style: string; name: string; score: number; fit_words?: string | null; picture_url?: string | null; triangles: number; kind: HairKind | string; credit?: string | null }
export type HairPanel = {
  source: HairSource
  available: boolean
  unavailable_reason?: string | null
  family?: string | null
  family_words?: string[] | null
  pick?: string | null
  candidates: HairCandidate[]
  mesh_version_id?: string | null
  best?: string | null
  nothing_fits?: boolean
  // The style the kept Clean up result was made with.
  applied?: { style: string; score?: number | null; prep_version_id?: string | null } | null
  // Offered after the setting changed: the step to rebuild from and about how long it takes.
  rebuild?: { from_stage: StageId | string; minutes?: number | null } | null
  // Triangles kept for hair on the kept body (the backend's hair allowance), when it says.
  allowance?: number | null
  sentence?: string | null
}
// One catalogue entry (backend/vgac/data/hairlib/catalogue.json), as GET /api/hair-library lists it.
export type HairStyleEntry = {
  id: string
  name: string
  family: string
  kind: HairKind | string
  triangles: number
  author?: string | null
  licence?: string | null
  source_url?: string | null
  pack?: string | null
  phone_ok?: boolean
  // Thumbnail paths inside the library (served by GET /api/hair-library/files/{path}): an object by view or a list.
  thumbs?: Record<string, string> | string[] | null
}
export type HairSetResult = { panel: HairPanel; started?: RunResult[]; rebuild_from?: StageId | string | null }
export type HairPickResult = { panel: HairPanel; autopilot?: AutopilotState | null }

// A hair library file (a thumbnail); data: and absolute URLs pass through, as libraryUrl does.
export function hairLibraryUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined
  if (/^(data:|blob:|https?:\/\/|\/)/.test(path)) return path
  return '/api/hair-library/files/' + path.split('/').map(encodeURIComponent).join('/')
}

// GET /api/hair-library may answer with the list itself or with {styles: [...]}.
function hairStyles(x: unknown): HairStyleEntry[] {
  const list = Array.isArray(x) ? x : x && typeof x === 'object' ? (x as { styles?: unknown }).styles : null
  return Array.isArray(list) ? (list as HairStyleEntry[]).filter((s) => s && typeof s.id === 'string') : []
}

export const hairApi = {
  panel: (aid: string) => get<HairPanel>(`/api/assets/${aid}/hair`),
  setSource: (aid: string, source: HairSource) => put<HairSetResult>(`/api/assets/${aid}/hair`, { source }),
  pick: (aid: string, style: string, rebuild: boolean) => post<HairPickResult>(`/api/assets/${aid}/hair/pick`, { style, rebuild }),
  library: () => get<unknown>('/api/hair-library').then(hairStyles),
}
