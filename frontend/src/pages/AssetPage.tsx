import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, ApiError, STAGES, stageState, versionsOf, type Asset, type AutopilotBody, type AutopilotState, type Job, type StageId, type Suggestion } from '../api'
import { Info } from '../components/Info'
import { JobLog } from '../components/JobLog'
import { Disclosure, ErrorText, Field, SubmitTextarea, type FixRequest } from '../components/ui'
import { useEvents } from '../events'
import { autopilotLine, autopilotSecondsLeft, fmtDuration, humanError, platformPlain, stageEstimate, stageGerund, stagePlain, suggestFallback, VARIANT_EXAMPLES } from '../help'
import { autopilotStage, isAutopilotActive, type AutopilotActions } from '../pipeline/AutopilotPanel'
import { StagePanel } from '../pipeline/StagePanel'
import { categoryLabel, useRefData, type RefData } from '../refdata'

export default function AssetPage() {
  const { aid = '', stage: stageParam } = useParams()
  const navigate = useNavigate()
  const stage: StageId = STAGES.includes(stageParam as StageId) ? (stageParam as StageId) : 'concept'
  const { ref } = useRefData()
  const [asset, setAsset] = useState<Asset | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState<unknown>(null)
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
  const [showLog, setShowLog] = useState(false)
  const [variantOpen, setVariantOpen] = useState(false)
  const [baseName, setBaseName] = useState<string | null>(null)
  // Autopilot state; apAvailable is null until known, false when the backend has no autopilot (404).
  const [autopilot, setAutopilot] = useState<AutopilotState | null>(null)
  const [apAvailable, setApAvailable] = useState<boolean | null>(null)
  const [apDismissed, setApDismissed] = useState<string | null>(null) // updated_at of a dismissed paused card
  const [apBusy, setApBusy] = useState(false)
  const reloadTimer = useRef<number | null>(null)
  const followRef = useRef<string | null>(null) // the autopilot stage the page last followed to

  const reload = useCallback(async () => {
    try {
      const a = await api.getAsset(aid)
      setAsset(a)
      setError(null)
      if ('autopilot' in a) {
        setApAvailable(true)
        if (a.autopilot) setAutopilot(a.autopilot)
      }
    } catch (e) {
      setError(e)
    }
  }, [aid])

  // Probe the autopilot endpoint once per asset (the asset itself may not carry the state).
  useEffect(() => {
    setAutopilot(null)
    setApAvailable(null)
    setApDismissed(null)
    followRef.current = null
    let alive = true
    api.getAutopilot(aid).then(
      (st) => {
        if (!alive) return
        setApAvailable(true)
        setAutopilot(st)
      },
      (e) => {
        if (!alive) return
        if (e instanceof ApiError && e.status === 404) setApAvailable(false)
      },
    )
    return () => {
      alive = false
    }
  }, [aid])

  // While the autopilot runs, re-read its state every few seconds (covers the polling fallback).
  const apRunning = autopilot?.status === 'running'
  useEffect(() => {
    if (!apRunning) return
    const t = window.setInterval(() => {
      api.getAutopilot(aid).then(
        (st) => setAutopilot((cur) => (cur && cur.status === st.status && cur.stage === st.stage && cur.job_id === st.job_id ? cur : st)),
        () => undefined,
      )
    }, 3000)
    return () => window.clearInterval(t)
  }, [aid, apRunning])

  // Follow the autopilot from stage to stage while the page is looking at the stage it was on.
  const apStageNow = autopilotStage(autopilot)
  const apStatus = autopilot?.status
  useEffect(() => {
    if (!apStageNow || !apStatus || apStatus === 'idle') return
    const following = followRef.current === null || followRef.current === stage
    if (following && apStageNow !== stage) navigate(`/assets/${aid}/${apStageNow}`, { replace: true })
    followRef.current = apStageNow
  }, [apStageNow, apStatus]) // eslint-disable-line react-hooks/exhaustive-deps

  const reloadSoon = useCallback(() => {
    if (reloadTimer.current != null) return
    reloadTimer.current = window.setTimeout(() => {
      reloadTimer.current = null
      void reload()
    }, 150)
  }, [reload])

  useEffect(() => {
    void reload()
    api.listJobs({ asset_id: aid }).then(setJobs, () => setJobs([]))
  }, [aid, reload])

  // The base asset's name when this is a variant.
  const baseId = asset?.base_asset_id ?? null
  useEffect(() => {
    if (!baseId) {
      setBaseName(null)
      return
    }
    let alive = true
    api.getAsset(baseId).then(
      (b) => alive && setBaseName(b.name),
      () => alive && setBaseName(null),
    )
    return () => {
      alive = false
    }
  }, [baseId])

  // Model suggestion and per-stage time estimates for this asset's category/style/platform.
  const sugKey = asset ? `${asset.category}|${asset.style_preset}|${asset.platform_target}` : ''
  useEffect(() => {
    if (!asset) return
    let alive = true
    api.suggest({ category: asset.category, style: asset.style_preset, platform: asset.platform_target }).then(
      (s) => alive && setSuggestion(s),
      () => alive && setSuggestion(suggestFallback(asset.category, asset.style_preset, asset.platform_target, ref)),
    )
    return () => {
      alive = false
    }
  }, [sugKey, ref]) // eslint-disable-line react-hooks/exhaustive-deps

  useEvents(
    (ev) => {
      if (ev.type === 'job') {
        if (ev.job.asset_id !== aid) return
        setJobs((list) => {
          const i = list.findIndex((j) => j.id === ev.job.id)
          if (i < 0) return [...list, ev.job]
          const next = list.slice()
          next[i] = ev.job
          return next
        })
        if (ev.job.status === 'done' || ev.job.status === 'error' || ev.job.status === 'cancelled' || ev.job.status === 'running') reloadSoon()
      } else if (ev.type === 'version' && ev.version.asset_id === aid) {
        reloadSoon()
      } else if (ev.type === 'autopilot' && ev.asset_id === aid) {
        setApAvailable(true)
        setAutopilot(ev.autopilot)
        reloadSoon()
      }
    },
    [aid, reloadSoon],
  )

  const apCall = async (fn: () => Promise<AutopilotState | void>) => {
    setApBusy(true)
    try {
      const st = await fn()
      if (st) setAutopilot(st)
      setApDismissed(null)
      setError(null)
      reloadSoon()
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setApAvailable(false)
      else setError(e)
    } finally {
      setApBusy(false)
    }
  }
  const autopilotActions: AutopilotActions = {
    start: (body?: AutopilotBody) => void apCall(() => api.startAutopilot(aid, body ?? {})),
    stop: () => void apCall(() => api.stopAutopilot(aid)),
    // Proposed contract: POST autopilot {fix: {stage, params}} re-runs that stage with the fix, then continues.
    fixAndContinue: (fix: FixRequest) => void apCall(() => api.startAutopilot(aid, { fix: { stage: fix.stage, params: fix.params } })),
    continueAnyway: () =>
      void apCall(async () => {
        const vid = autopilot?.version_id
        const s = autopilotStage(autopilot)
        if (vid && s && asset && asset.selected?.[s] !== vid) await api.select(vid)
        return api.startAutopilot(aid, {})
      }),
    dismiss: () => setApDismissed(autopilot?.updated_at ?? 'dismissed'),
  }
  // A dismissed paused or stopped card stays hidden until the state changes again.
  const apShown = autopilot && apDismissed !== null && apDismissed === (autopilot.updated_at ?? 'dismissed') && autopilot.status !== 'running' ? null : autopilot

  const cancelJob = async (id: string) => {
    try {
      const j = await api.cancelJob(id)
      setJobs((list) => list.map((x) => (x.id === id ? j : x)))
      reloadSoon()
    } catch (e) {
      setError(e)
    }
  }

  if (!asset) {
    return (
      <div className="page-narrow">
        <ErrorText error={error} />
        {!error && <div className="muted">Loading asset...</div>}
      </div>
    )
  }

  const stageInfo = (s: StageId) => ref?.stages.find((x) => x.id === s) ?? null
  const status = statusLine(asset, jobs, ref, suggestion, autopilot)

  return (
    <div className={'pipeline-layout' + (showLog ? ' with-log' : '')}>
      <aside className="stage-rail">
        <div className="rail-head">
          <Link to={`/projects/${asset.project_id}`} className="crumb">
            Back to assets
          </Link>
          <div className="asset-title">{asset.name}</div>
          {asset.base_asset_id && (
            <div className="variant-of">
              variant of <Link to={`/assets/${asset.base_asset_id}`}>{baseName ?? 'the original'}</Link>
              {asset.variant_change ? `: ${asset.variant_change}` : ''}
              <Info term="term.variant" />
            </div>
          )}
          <div className="muted small">
            {categoryLabel(ref, asset.category)}
            <Info term={'category.' + asset.category} />
            {' / '}
            {platformPlain(asset.platform_target)}
            <Info term={'platform.' + asset.platform_target} />
          </div>
          {asset.parts.length > 0 && (
            <div className="muted small">
              parts: {asset.parts.join(', ')}
              <Info term="term.parts" />
            </div>
          )}
        </div>
        <nav className="rail-stages" aria-label="Progress">
          {STAGES.map((s, i) => {
            const st = stageState(asset, s)
            const list = versionsOf(asset, s)
            const kept = list.some((v) => v.selected || asset.selected[s] === v.id)
            const reached = list.length > 0
            const cls = ['rail-stage', s === stage ? 'active' : '', reached ? 'reached' : 'future', kept ? 'kept' : ''].join(' ')
            const meta = st === 'running' ? 'working' : st === 'error' ? 'failed' : kept ? 'kept' : st === 'done' ? 'choose one' : ''
            const help = stageInfo(s)
            return (
              <div key={s} className={cls}>
                <button type="button" className="rail-stage-btn" onClick={() => navigate(`/assets/${asset.id}/${s}`)} title={`${stagePlain(s, help)} (technical name: ${s})`} aria-current={s === stage ? 'step' : undefined}>
                  <span className={'sdot sdot-' + st} />
                  <span className="rail-index">{i + 1}</span>
                  <span className="rail-label">{stagePlain(s, help)}</span>
                  <span className="rail-meta">{meta}</span>
                </button>
                <Info term={'stage.' + s} />
              </div>
            )
          })}
        </nav>
        <div className="rail-prompt">
          <div className="field-label">
            Description
            <Info term="term.prompt" />
          </div>
          <div className="small">{asset.prompt}</div>
        </div>
      </aside>
      <section className="stage-main">
        <header className="asset-head">
          <div className="asset-status">
            <span className={'traffic traffic-' + status.tone} aria-hidden="true" />
            <span className="asset-status-text">{status.text}</span>
            {status.raw && (
              <Disclosure label="Show details" className="inline-disclosure">
                <pre className="raw-error">{status.raw}</pre>
              </Disclosure>
            )}
          </div>
          <div className="row-gap">
            {autopilot?.status === 'running' && (
              <button type="button" className="small-btn" disabled={apBusy} onClick={autopilotActions.stop} title="Stop the autopilot after the current step">
                Stop autopilot
              </button>
            )}
            <button type="button" className="small-btn" aria-pressed={variantOpen} disabled={!asset.selected?.concept} onClick={() => setVariantOpen((v) => !v)} title={asset.selected?.concept ? 'Make a copy of this asset with one change' : 'Keep a concept picture first'}>
              Make a variant
            </button>
            <Info term="term.variant" />
            <button type="button" className="small-btn" aria-pressed={showLog} onClick={() => setShowLog((v) => !v)} title="Show the job log">
              Details
            </button>
          </div>
        </header>
        {variantOpen && <VariantPanel asset={asset} onClose={() => setVariantOpen(false)} onError={setError} />}
        <ErrorText error={error} />
        <StagePanel
          key={stage}
          asset={asset}
          stage={stage}
          jobs={jobs}
          suggestion={suggestion}
          baseName={baseName}
          autopilot={apShown}
          apAvailable={apAvailable}
          autopilotActions={autopilotActions}
          onMakeVariant={() => setVariantOpen(true)}
          onChanged={reloadSoon}
          onError={setError}
          onCancelJob={cancelJob}
        />
      </section>
      {showLog && (
        <aside className="job-col">
          <JobLog jobs={jobs} onCancel={cancelJob} />
        </aside>
      )}
    </div>
  )
}

// "Make a variant": a sibling asset with one change that keeps this one's identity.
function VariantPanel({ asset, onClose, onError }: { asset: Asset; onClose: () => void; onError: (e: unknown) => void }) {
  const navigate = useNavigate()
  const [change, setChange] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const defaultName = `${asset.name} - ${change.trim() || '...'}`
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!change.trim()) return
    setBusy(true)
    try {
      const r = await api.createVariant(asset.id, { change: change.trim(), ...(name.trim() ? { name: name.trim() } : {}) })
      navigate(`/assets/${r.asset.id}/concept`)
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="variant-panel" onSubmit={submit}>
      <div className="row-between">
        <div className="field-label">
          Make a variant of {asset.name}
          <Info term="term.variant" />
        </div>
        <button type="button" className="link small" onClick={onClose}>
          close
        </button>
      </div>
      <div className="muted small">We start from the picture you kept and repaint it with your change, so it stays the same character. The rest of the steps run as usual.</div>
      <Field label="What changes?" term="term.variant_change">
        <SubmitTextarea rows={2} value={change} onChange={(e) => setChange(e.target.value)} placeholder="e.g. winter cloak and fur boots" autoFocus />
      </Field>
      <div className="examples">
        <span className="muted small">Examples:</span>
        {VARIANT_EXAMPLES.map((ex) => (
          <button key={ex} type="button" className="example-btn" onClick={() => setChange(ex)}>
            {ex}
          </button>
        ))}
      </div>
      <Field label="Name" hint={`Optional. Defaults to "${defaultName}".`}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={defaultName} />
      </Field>
      <div className="row-gap">
        <button type="submit" disabled={busy || !change.trim()}>
          {busy ? 'Creating...' : 'Create variant'}
        </button>
      </div>
    </form>
  )
}

function stageOfJob(job: Job, asset: Asset): StageId | null {
  const prefix = job.kind.split('.')
  for (const p of prefix) if (STAGES.includes(p as StageId)) return p as StageId
  if (job.version_id) for (const s of STAGES) if (versionsOf(asset, s).some((v) => v.id === job.version_id)) return s
  return null
}

// The single status line in the asset header.
function statusLine(asset: Asset, jobs: Job[], ref: RefData | null, suggestion: Suggestion | null, autopilot: AutopilotState | null): { text: string; tone: 'ok' | 'warn' | 'bad' | 'none'; raw?: string } {
  const live = jobs.filter((j) => j.status === 'running' || j.status === 'queued')
  const job = live.find((j) => j.status === 'running') ?? live[0]
  if (autopilot && isAutopilotActive(autopilot)) {
    const s = autopilotStage(autopilot)
    if (autopilot.status === 'running') {
      const apJob = jobs.find((j) => j.id === autopilot.job_id) ?? job
      const queued = apJob?.status === 'queued'
      const until = STAGES.includes(autopilot.until_stage as StageId) ? (autopilot.until_stage as StageId) : 'export'
      const left = s ? autopilotSecondsLeft(s, queued ? 0 : (apJob?.progress ?? 0), until, ref?.stages, suggestion?.estimates) : null
      return { text: autopilotLine(s, left, { queued }) + '.', tone: 'ok' }
    }
    return { text: `Autopilot ${autopilot.status === 'paused' ? 'paused' : 'stopped'}${s ? ` at "${stagePlain(s)}"` : ''}: ${autopilot.reason ?? 'it needs you.'}`, tone: autopilot.status === 'paused' ? 'warn' : 'bad' }
  }
  if (job) {
    const s = stageOfJob(job, asset)
    const step = s ? `step ${STAGES.indexOf(s) + 1} of ${STAGES.length}` : ''
    if (job.status === 'queued') return { text: `${s ? stageGerund(s) : 'Working'}: waiting for a free machine${step ? `, ${step}` : ''}.`, tone: 'warn' }
    const est = s ? stageEstimate(s, ref?.stages.find((x) => x.id === s), suggestion?.estimates) : 120
    const left = est * (1 - (job.progress ?? 0))
    return { text: `${s ? stageGerund(s) : 'Working'}, ${fmtDuration(left)} left${step ? `, ${step}` : ''}.`, tone: 'ok' }
  }
  const sorted = jobs.slice().sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
  const last = sorted[0]
  const done = STAGES.filter((s) => asset.selected?.[s]).length
  if (last && last.status === 'error') {
    const s = stageOfJob(last, asset)
    return { text: `${s ? stagePlain(s) + ': ' : ''}${humanError(last.error)}`, tone: 'bad', raw: last.error ?? undefined }
  }
  if (done === STAGES.length) return { text: `${autopilot?.status === 'done' ? 'Autopilot finished. ' : 'Finished. '}Your asset is ready to hand to the game.`, tone: 'ok' }
  return { text: `Nothing running. ${done} of ${STAGES.length} steps done.`, tone: 'none' }
}
