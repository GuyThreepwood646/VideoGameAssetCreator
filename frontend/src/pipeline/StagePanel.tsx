import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import {
  api,
  libraryUrl,
  previousStage,
  selectedVersion,
  TURNAROUND_VIEWS,
  versionsOf,
  type AdjustParam,
  type Asset,
  type Check,
  type Job,
  type StageId,
  type Suggestion,
  type Version,
} from '../api'
import { Info } from '../components/Info'
import { ChecksList, Disclosure, Field, isCheckList, MetricChips, PlainChecks, StatusBadge, SubmitTextarea, timeAgo, TrafficDot, type FixRequest } from '../components/ui'
import { useGlbSnapshot } from '../glbSnapshot'
import { preferredConceptModel } from '../guide'
import { firstFailing, fmtDuration, humanError, isSimpleParam, looksGood, modelLabel, paramPlain, platformPlain, stagePlain, totalEstimate } from '../help'
import { useRefData, type RefData } from '../refdata'
import { GuidePanel, guideState } from './GuidePanel'
import { ConceptView } from './views/ConceptView'
import { ExportView } from './views/ExportView'
import { MeshView } from './views/MeshView'
import { RigView } from './views/RigView'
import { TurnaroundView } from './views/TurnaroundView'

type Props = {
  asset: Asset
  stage: StageId
  jobs: Job[]
  suggestion: Suggestion | null
  baseName?: string | null
  onChanged: () => void
  onError: (e: unknown) => void
  onCancelJob: (id: string) => void
}

export function StagePanel({ asset, stage, jobs, suggestion, baseName, onChanged, onError, onCancelJob }: Props) {
  const navigate = useNavigate()
  const versions = versionsOf(asset, stage)
  const selected = selectedVersion(asset, stage)
  const [activeId, setActiveId] = useState<string | null>(null)
  const active = useMemo(() => versions.find((v) => v.id === activeId) ?? selected ?? versions[versions.length - 1], [versions, activeId, selected])
  const prev = previousStage(stage)
  const prevSelected = prev ? selectedVersion(asset, prev) : undefined
  const canRun = !prev || !!prevSelected
  const [count, setCount] = useState(2)
  const [busy, setBusy] = useState(false)
  const [changeOpen, setChangeOpen] = useState(false)
  const { ref } = useRefData()
  const stageInfo = ref?.stages.find((s) => s.id === stage) ?? null
  const schema = stageInfo?.adjust_schema ?? []
  const state = guideState(asset, stage, jobs, selected)

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      onChanged()
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }

  const runParams = (s: StageId): Record<string, unknown> | undefined => {
    if (s !== 'concept') return undefined
    const model = preferredConceptModel(asset.id)
    return model ? { model } : undefined
  }

  const run = () => act(() => api.runStage(asset.id, stage, { ...(stage === 'concept' ? { count } : {}), params: runParams(stage) }))
  const runNext = (next: StageId) =>
    act(async () => {
      await api.runStage(asset.id, next, { params: runParams(next) })
      navigate(`/assets/${asset.id}/${next}`)
    })
  const retry = (failed: Version) =>
    act(() => {
      const params = { ...failed.params }
      delete params.seed
      return api.runStage(asset.id, stage, { params, ...(failed.parent_version_id ? { parent_version_id: failed.parent_version_id } : {}) })
    })

  const fix = (f: FixRequest) =>
    act(async () => {
      if (f.stage === stage && active) {
        await api.adjust(active.id, f.params)
      } else {
        await api.runStage(asset.id, f.stage, { params: f.params })
        navigate(`/assets/${asset.id}/${f.stage}`)
      }
    })

  const checks = active && isCheckList(active.metrics.checks) ? (active.metrics.checks as Check[]) : null
  const ctx = { platform: asset.platform_target }

  return (
    <div className="stage-panel">
      <GuidePanel
        asset={asset}
        stage={stage}
        jobs={jobs}
        stages={ref?.stages ?? null}
        estimates={suggestion?.estimates ?? null}
        active={active}
        selected={selected}
        busy={busy}
        baseName={baseName}
        onRun={run}
        onRunNext={runNext}
        onRetry={retry}
        onCancel={onCancelJob}
      />

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">
            {stagePlain(stage, stageInfo)}
            <Info term={'stage.' + stage} />
          </h2>
          <span className="muted small">
            {versions.length ? `${versions.length} version${versions.length > 1 ? 's' : ''}` : 'no versions yet'}
            {canRun && prevSelected ? ` / built from "${stagePlain(prev!, ref?.stages.find((s) => s.id === prev) ?? null)}" version ${shortId(prevSelected.id)}` : ''}
          </span>
        </div>
        {versions.length > 0 && (
          <div className="candidates">
            {versions.map((v, i) => (
              <CandidateCard
                key={v.id}
                version={v}
                index={i + 1}
                stage={stage}
                job={jobs.find((j) => j.id === v.job_id || j.version_id === v.id)}
                isActive={active?.id === v.id}
                isSelected={selected?.id === v.id}
                onClick={() => setActiveId(v.id)}
              />
            ))}
          </div>
        )}
        {versions.length === 0 && <div className="placeholder-box">Nothing here yet. Use the button above to start.</div>}
        {active && (
          <div className="active-version">
            <div className="row-between">
              <div className="row-gap">
                <strong>Version {versions.indexOf(active) + 1}</strong>
                <span className="muted small mono">{shortId(active.id)}</span>
                <StatusBadge status={active.status} />
                {selected?.id === active.id && <span className="chip chip-ok">kept</span>}
                {active.parent_version_id && <span className="muted small">changed from {shortId(active.parent_version_id)}</span>}
                <span className="muted small">{timeAgo(active.created_at)}</span>
              </div>
            </div>
            {active.status === 'error' && (
              <div className="error-box">
                <div className="error-sentence">{humanError(active.error)}</div>
                <div className="row-gap">
                  {state !== 'error' && (
                    <button type="button" disabled={busy} onClick={() => retry(active)}>
                      Try again
                    </button>
                  )}
                  {active.error && (
                    <Disclosure label="Show details">
                      <pre className="raw-error">{active.error}</pre>
                    </Disclosure>
                  )}
                </div>
              </div>
            )}
            <StageViewer stage={stage} version={active} onRefineView={(view) => act(() => api.refine(active.id, { instruction: `Regenerate the ${view} view`, view }))} />
            {checks && active.status === 'done' && (
              <div className="checks-block">
                <div className="field-label">
                  Quality checks
                  <Info term="term.gate_ok" />
                </div>
                <PlainChecks checks={checks} ctx={ctx} onFix={fix} busy={busy} compact />
              </div>
            )}
            {active.status === 'done' && (
              <HappyBlock
                stage={stage}
                active={active}
                isSelected={selected?.id === active.id}
                isPrimary={state === 'review'}
                busy={busy}
                count={count}
                setCount={setCount}
                changeOpen={changeOpen}
                setChangeOpen={setChangeOpen}
                onKeep={() => act(() => api.select(active.id))}
                onTryAgain={run}
                onReject={() => act(() => api.reject(active.id))}
              />
            )}
            {changeOpen && active.status === 'done' && (
              <ChangePanel
                stage={stage}
                target={active}
                schema={schema}
                asset={asset}
                refData={ref}
                suggestion={suggestion}
                disabled={busy}
                onRefine={(instruction, view) => act(() => api.refine(active.id, { instruction, view }))}
                onAdjust={(params) => act(() => api.adjust(active.id, params))}
              />
            )}
            <Disclosure label="Details" className="details">
              <div className="details-grid">
                <div className="muted small">
                  Technical name: <span className="mono">{stage}</span> / version <span className="mono">{active.id}</span>
                  {active.job_id && (
                    <>
                      {' '}
                      / job <span className="mono">{active.job_id}</span>
                    </>
                  )}
                </div>
                <MetricChips metrics={active.metrics} max={24} />
                {checks && <ChecksList checks={checks} />}
                <FilesRow version={active} />
                <ParamsRow version={active} />
                <div className="row-gap">
                  <button type="button" className="small-btn" disabled={busy || active.status !== 'done'} onClick={() => act(() => api.judge(active.id, 'metrics'))}>
                    Recompute checks
                  </button>
                  <Info term="term.judge" />
                </div>
              </div>
            </Disclosure>
          </div>
        )}
      </section>
    </div>
  )
}

function shortId(id: string) {
  return id.slice(0, 8)
}

function CandidateCard({
  version,
  index,
  stage,
  job,
  isActive,
  isSelected,
  onClick,
}: {
  version: Version
  index: number
  stage: StageId
  job?: Job
  isActive: boolean
  isSelected: boolean
  onClick: () => void
}) {
  const is3d = stage === 'mesh' || stage === 'prep' || stage === 'rig' || stage === 'export'
  const imgPath = stage === 'concept' ? (version.files.cutout ?? version.files.image) : stage === 'turnaround' ? (version.files.front_cutout ?? version.files.front ?? Object.values(version.files)[0]) : version.files.preview
  const glbUrl = is3d && !version.files.preview ? libraryUrl(version.files.glb) : undefined
  const { src: snap, error: snapError } = useGlbSnapshot(version.status === 'done' ? glbUrl : undefined)
  const img = is3d ? (libraryUrl(imgPath) ?? snap) : libraryUrl(imgPath)
  const cls = ['candidate', isActive ? 'active' : '', isSelected ? 'selected' : '', version.status === 'rejected' ? 'rejected' : ''].join(' ')
  const good = version.status === 'done' ? looksGood(version.metrics) : null
  const reason = version.status === 'done' ? firstFailing(version.metrics) : null
  return (
    <button type="button" className={cls} onClick={onClick} aria-pressed={isActive} aria-label={`version ${index}`}>
      <div className="candidate-thumb checker">
        {img ? (
          <img src={img} alt={`version ${index}`} />
        ) : version.status === 'running' || version.status === 'queued' ? (
          <div className="thumb-progress">
            <div className="progress">
              <div className="progress-bar" style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} />
            </div>
            <span className="small muted">{version.status === 'queued' ? 'waiting' : 'working'}</span>
          </div>
        ) : (
          <span className="small muted">{snapError ? 'no preview' : version.status === 'error' ? 'failed' : 'no preview'}</span>
        )}
      </div>
      <div className="candidate-meta">
        <span className="row-between">
          <strong>v{index}</strong>
          <span className="muted mono small">{shortId(version.id)}</span>
        </span>
        {version.status === 'done' ? (
          <span className={'traffic-line ' + (good === false ? 'bad' : good ? 'ok' : 'none')} title={reason ?? (good ? 'Every check passed' : 'No checks for this version')}>
            <TrafficDot state={good === false ? 'bad' : good ? 'ok' : 'none'} />
            {good === false ? 'needs attention' : good ? 'looks good' : 'no checks'}
          </span>
        ) : (
          <StatusBadge status={version.status} />
        )}
        {isSelected && <span className="chip chip-ok">kept</span>}
      </div>
    </button>
  )
}

function StageViewer({ stage, version, onRefineView }: { stage: StageId; version: Version; onRefineView: (view: string) => void }) {
  if (version.status !== 'done' && version.status !== 'rejected') {
    return <div className="placeholder-box">{version.status === 'error' ? 'This version has no result.' : 'Waiting for the result...'}</div>
  }
  switch (stage) {
    case 'concept':
      return <ConceptView version={version} />
    case 'turnaround':
      return <TurnaroundView version={version} onRegenerateView={onRefineView} />
    case 'mesh':
    case 'prep':
      return <MeshView version={version} />
    case 'rig':
      return <RigView version={version} />
    case 'export':
      return <ExportView version={version} />
  }
}

function FilesRow({ version }: { version: Version }) {
  const entries = Object.entries(version.files)
  if (!entries.length) return null
  return (
    <div className="files-row small">
      <span className="muted">files:</span>
      {entries.map(([k, p]) => (
        <a key={k} href={libraryUrl(p)} target="_blank" rel="noreferrer" download={p.split('/').pop()} title={p}>
          {k}
        </a>
      ))}
    </div>
  )
}

function ParamsRow({ version }: { version: Version }) {
  const entries = Object.entries(version.params).filter(([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object')
  if (!entries.length) return null
  return (
    <div className="chips">
      {entries.map(([k, v]) => (
        <span key={k} className="chip" title={k}>
          <span className="chip-key">{paramPlain(k)}</span> {k === 'model' ? modelLabel(String(v)) : String(v).length > 40 ? String(v).slice(0, 40) + '...' : String(v)}
        </span>
      ))}
    </div>
  )
}

// "Happy with this?" with three answers. The Keep answer is the page's primary
// button only while nothing is kept yet (the guide panel holds it otherwise).
function HappyBlock({
  stage,
  active,
  isSelected,
  isPrimary,
  busy,
  count,
  setCount,
  changeOpen,
  setChangeOpen,
  onKeep,
  onTryAgain,
  onReject,
}: {
  stage: StageId
  active: Version
  isSelected: boolean
  isPrimary: boolean
  busy: boolean
  count: number
  setCount: (n: number) => void
  changeOpen: boolean
  setChangeOpen: (b: boolean) => void
  onKeep: () => void
  onTryAgain: () => void
  onReject: () => void
}) {
  return (
    <div className="happy">
      <div className="happy-q">Happy with this?</div>
      <div className="happy-answers">
        <button type="button" className={isPrimary && !isSelected ? 'primary' : ''} disabled={busy || isSelected} onClick={onKeep} title={isSelected ? 'This version is kept; later steps build on it' : 'Keep this version; later steps build on it'}>
          {isSelected ? 'Kept' : 'Yes, keep it'}
        </button>
        <span className="answer-group">
          <button type="button" disabled={busy} onClick={onTryAgain} title="Make a fresh attempt with the same settings">
            Try again
          </button>
          {stage === 'concept' && (
            <select value={count} onChange={(e) => setCount(Number(e.target.value))} aria-label="How many new pictures" disabled={busy}>
              {[1, 2, 3, 4].map((n) => (
                <option key={n} value={n}>
                  {n} new picture{n > 1 ? 's' : ''}
                </option>
              ))}
            </select>
          )}
        </span>
        <button type="button" aria-pressed={changeOpen} onClick={() => setChangeOpen(!changeOpen)} title="Describe a change or fine-tune the settings">
          Change something
        </button>
        <span className="grow" />
        <button type="button" className="link small" disabled={busy || active.status !== 'done'} onClick={onReject} title="Hide this version; it stays in the history">
          Reject
        </button>
      </div>
    </div>
  )
}

// The "Change something" panel: first "Describe the change", then "Fine-tune".
function ChangePanel({
  stage,
  target,
  schema,
  asset,
  refData,
  suggestion,
  disabled,
  onRefine,
  onAdjust,
}: {
  stage: StageId
  target: Version
  schema: AdjustParam[]
  asset: Asset
  refData: RefData | null
  suggestion: Suggestion | null
  disabled: boolean
  onRefine: (instruction: string, view?: string) => void
  onAdjust: (params: Record<string, unknown>) => void
}) {
  const [text, setText] = useState('')
  const [view, setView] = useState('')
  const ok = text.trim().length > 0
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ok) return
    onRefine(text.trim(), view || undefined)
    setText('')
  }
  const availableViews = TURNAROUND_VIEWS.filter((v) => v in target.files)
  return (
    <div className="change-panel">
      <form className="change-describe" onSubmit={submit}>
        <div className="field-label">
          Describe the change
          <Info term="term.instruction" />
        </div>
        <div className="refine-row">
          <SubmitTextarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholderFor(stage)} aria-label="Describe the change" />
          {stage === 'turnaround' && (
            <select value={view} onChange={(e) => setView(e.target.value)} aria-label="Which view to change">
              <option value="">all views</option>
              {availableViews.map((v) => (
                <option key={v} value={v}>
                  {v} view
                </option>
              ))}
            </select>
          )}
          <button type="submit" disabled={disabled || !ok}>
            Make the change
          </button>
        </div>
        <div className="muted small">Enter sends it. The result is a new version; this one stays.</div>
      </form>
      <FineTune stage={stage} schema={schema} target={target} asset={asset} refData={refData} suggestion={suggestion} disabled={disabled} onSubmit={onAdjust} />
    </div>
  )
}

function placeholderFor(stage: StageId): string {
  switch (stage) {
    case 'concept':
      return 'e.g. make the helmet open-faced and the shield round'
    case 'turnaround':
      return 'e.g. the back view should show the cloak'
    case 'mesh':
      return 'e.g. thicker sword, less bulky shoulders'
    case 'prep':
      return 'e.g. more wear on the armour'
    case 'rig':
      return 'e.g. smoother bending at the left elbow'
    case 'export':
      return 'e.g. drop the sprite sheet, add another detail level'
  }
}

// ---------------------------------------------------------------------------
// Fine-tune (the Adjust form) with a simple layer and an Advanced disclosure
// ---------------------------------------------------------------------------

const SIZE_OPTIONS = [768, 1024, 1280, 1536, 2048]

function FineTune({
  stage,
  schema,
  target,
  asset,
  refData,
  suggestion,
  disabled,
  onSubmit,
}: {
  stage: StageId
  schema: AdjustParam[]
  target: Version
  asset: Asset
  refData: RefData | null
  suggestion: Suggestion | null
  disabled: boolean
  onSubmit: (params: Record<string, unknown>) => void
}) {
  const initial = useMemo(() => {
    const out: Record<string, unknown> = {}
    for (const p of schema) {
      const current = target.params?.[p.name]
      out[p.name] = current !== undefined ? current : p.default !== undefined ? p.default : defaultFor(p)
    }
    return out
  }, [schema, target])
  const [values, setValues] = useState<Record<string, unknown>>(initial)
  useEffect(() => setValues(initial), [initial])

  const set = (k: string, v: unknown) => setValues((s) => ({ ...s, [k]: v }))
  const changed = schema.filter((p) => JSON.stringify(values[p.name] ?? null) !== JSON.stringify(initial[p.name] ?? null))

  const schemaHasFlag = schema.some((p) => p.simple !== undefined)
  const simple = schema.filter((p) => isSimpleParam(stage, p, schemaHasFlag))
  const advanced = schema.filter((p) => !simple.includes(p))
  const hasSize = stage === 'concept' && simple.some((p) => p.name === 'width') && simple.some((p) => p.name === 'height')
  const simpleShown = hasSize ? simple.filter((p) => p.name !== 'height') : simple

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!changed.length) return
    const params: Record<string, unknown> = {}
    for (const p of changed) params[p.name] = values[p.name]
    onSubmit(params)
  }

  const cat = refData?.categories.find((c) => c.id === asset.category)
  const plat = refData?.platforms.find((p) => p.id === asset.platform_target)
  const budget = cat?.budgets?.[asset.platform_target]?.triangles ?? suggestion?.budgets.triangles ?? plat?.triangles_character ?? 20000

  const renderField = (p: AdjustParam, adv: boolean) => {
    if (hasSize && p.name === 'width') {
      const w = Number(values.width ?? 1024)
      const opts = SIZE_OPTIONS.filter((o) => (p.min === undefined || o >= p.min) && (p.max === undefined || o <= p.max))
      if (!opts.includes(w)) opts.push(w)
      return (
        <Field key="size" label="Size" term="term.size" hint="Width and height of the picture">
          <select
            value={w}
            disabled={disabled}
            onChange={(e) => {
              const n = Number(e.target.value)
              set('width', n)
              set('height', n)
            }}
          >
            {opts
              .sort((a, b) => a - b)
              .map((o) => (
                <option key={o} value={o}>
                  {o} px{o === 1024 ? ' (recommended)' : ''}
                </option>
              ))}
          </select>
        </Field>
      )
    }
    if (stage === 'mesh' && p.name === 'target_faces' && !adv) {
      const cur = values.target_faces
      const curN = typeof cur === 'number' ? cur : null
      const levels: { label: string; value: number | null }[] = [
        ...(p.default === null || p.default === undefined ? [{ label: `Automatic (${budget.toLocaleString('en-US')} for ${stagePlainPlatform(asset.platform_target)})`, value: null }] : []),
        { label: `Lower detail (${Math.round(budget / 2).toLocaleString('en-US')} triangles, smaller file)`, value: clamp(Math.round(budget / 2), p) },
        { label: `Standard (${budget.toLocaleString('en-US')} triangles, the platform budget)`, value: clamp(budget, p) },
        { label: `Higher detail (${(budget * 2).toLocaleString('en-US')} triangles, over budget)`, value: clamp(budget * 2, p) },
      ]
      if (curN !== null && !levels.some((l) => l.value === curN)) levels.push({ label: `Current (${curN.toLocaleString('en-US')} triangles)`, value: curN })
      return (
        <Field key={p.name} label="Detail level" term="param.target_faces" hint="More triangles mean more detail and a heavier model">
          <select value={cur === null || cur === undefined ? '' : String(cur)} disabled={disabled} onChange={(e) => set('target_faces', e.target.value === '' ? null : Number(e.target.value))}>
            {levels.map((l) => (
              <option key={String(l.value)} value={l.value === null ? '' : String(l.value)}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
      )
    }
    if (stage === 'concept' && p.name === 'model') {
      return (
        <Field key={p.name} label={paramPlain('model', p.label)} term="param.model" hint="Which AI model paints the picture.">
          <select value={String(values.model ?? '')} disabled={disabled} onChange={(e) => set('model', e.target.value)}>
            {(p.options ?? []).map((o) => (
              <option key={String(o)} value={String(o)}>
                {modelLabel(String(o))}
                {suggestion && String(o) === suggestion.model ? ' (suggested)' : ''}
              </option>
            ))}
          </select>
        </Field>
      )
    }
    return <AdjustField key={p.name} param={p} value={values[p.name]} onChange={(v) => set(p.name, v)} disabled={disabled} />
  }

  return (
    <form className="finetune" onSubmit={submit}>
      <div className="row-between">
        <div className="field-label">
          Fine-tune
          <Info entry={{ plain: 'Fine-tune', meaning: 'Re-run this step with different settings.', why: 'Use it when the result is close but a setting is off; the description stays the same.', technical: 'adjust' }} />
        </div>
        {stage === 'concept' && suggestion && (
          <span className="muted small">
            We suggest {suggestion.model_label}, because {suggestion.reason}; {fmtDuration(totalEstimate(suggestion.estimates, refData))} to a finished asset.
          </span>
        )}
      </div>
      {schema.length === 0 && <div className="muted small">Nothing to fine-tune in this step.</div>}
      {simpleShown.length > 0 && <div className="adjust-grid">{simpleShown.map((p) => renderField(p, false))}</div>}
      {advanced.length > 0 && (
        <Disclosure label={`Advanced (${advanced.length} more setting${advanced.length > 1 ? 's' : ''})`}>
          <div className="adjust-grid">{advanced.map((p) => renderField(p, true))}</div>
        </Disclosure>
      )}
      <div className="row-gap">
        <button type="submit" disabled={disabled || !changed.length}>
          Apply and re-run
        </button>
        <button type="button" disabled={!changed.length} onClick={() => setValues(initial)}>
          Reset
        </button>
        <span className="muted small">{changed.length ? `${changed.length} changed: ${changed.map((p) => paramPlain(p.name, p.label)).join(', ')}` : 'no changes yet'}</span>
      </div>
    </form>
  )
}

function stagePlainPlatform(id: string): string {
  return platformPlain(id).split(':')[0].toLowerCase() || id
}

function clamp(n: number, p: AdjustParam): number {
  if (p.min !== undefined) n = Math.max(p.min, n)
  if (p.max !== undefined) n = Math.min(p.max, n)
  return n
}

function defaultFor(p: AdjustParam): unknown {
  switch (p.type) {
    case 'number':
    case 'integer':
      return p.min ?? 0
    case 'boolean':
      return false
    case 'multiselect':
      return []
    case 'select':
      return p.options?.[0] ?? ''
    default:
      return ''
  }
}

function AdjustField({ param: p, value, onChange, disabled }: { param: AdjustParam; value: unknown; onChange: (v: unknown) => void; disabled: boolean }) {
  const label = paramPlain(p.name, p.label)
  const term = `param.${p.name}`
  const hint = p.help?.meaning ?? p.description
  const tech = <span className="mono muted">{p.name}</span>
  const hintNode = hint ? (
    <>
      {hint} <span className="muted">({tech})</span>
    </>
  ) : (
    tech
  )
  switch (p.type) {
    case 'number':
    case 'integer': {
      const empty = value === null || value === undefined || value === ''
      const num = empty ? NaN : typeof value === 'number' ? value : Number(value)
      const step = p.step ?? (p.type === 'integer' ? 1 : 0.01)
      const hasRange = p.min !== undefined && p.max !== undefined && p.max - p.min <= 100000
      return (
        <Field
          label={
            <span className="row-between">
              {label}
              <span className="mono">{Number.isFinite(num) ? num : 'auto'}</span>
            </span>
          }
          term={term}
          hint={hintNode}
        >
          <div className="slider-row">
            {hasRange && (
              <input
                type="range"
                min={p.min}
                max={p.max}
                step={step}
                value={Number.isFinite(num) ? num : (p.default as number) ?? p.min ?? 0}
                disabled={disabled}
                onChange={(e) => onChange(p.type === 'integer' ? Math.round(Number(e.target.value)) : Number(e.target.value))}
              />
            )}
            <input
              type="number"
              className="num"
              min={p.min}
              max={p.max}
              step={step}
              placeholder="auto"
              value={Number.isFinite(num) ? num : ''}
              disabled={disabled}
              onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
            />
          </div>
        </Field>
      )
    }
    case 'boolean':
      return (
        <Field label={label} inline term={term} hint={hintNode}>
          <input type="checkbox" checked={!!value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        </Field>
      )
    case 'select':
      return (
        <Field label={label} term={term} hint={hintNode}>
          <select value={String(value ?? '')} disabled={disabled} onChange={(e) => onChange(e.target.value === '' ? null : coerceOption(p, e.target.value))}>
            {(value === null || value === undefined) && <option value="">automatic</option>}
            {(p.options ?? []).map((o) => (
              <option key={String(o)} value={String(o)}>
                {p.name === 'model' ? modelLabel(String(o)) : String(o)}
              </option>
            ))}
          </select>
        </Field>
      )
    case 'multiselect': {
      const arr = Array.isArray(value) ? (value as unknown[]).map(String) : []
      return (
        <Field label={label} term={term} hint={hintNode}>
          <div className="check-row">
            {(p.options ?? []).map((o) => (
              <label key={String(o)} className="check-item">
                <input
                  type="checkbox"
                  checked={arr.includes(String(o))}
                  disabled={disabled}
                  onChange={(e) => {
                    const next = e.target.checked ? [...arr, String(o)] : arr.filter((x) => x !== String(o))
                    onChange((p.options ?? []).filter((x) => next.includes(String(x))))
                  }}
                />
                {String(o)}
              </label>
            ))}
          </div>
        </Field>
      )
    }
    default:
      return (
        <Field label={label} term={term} hint={hintNode}>
          <input value={Array.isArray(value) ? value.join(', ') : String(value ?? '')} placeholder={p.default === null ? 'automatic' : undefined} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
        </Field>
      )
  }
}

function coerceOption(p: AdjustParam, raw: string): unknown {
  const match = (p.options ?? []).find((o) => String(o) === raw)
  return match !== undefined ? match : raw
}
