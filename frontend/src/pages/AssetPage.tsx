import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, ApiError, STAGES, stageState, stoppedVersions, versionsOf, type Asset, type AutopilotBody, type AutopilotState, type Job, type JobStatus, type StageId, type Suggestion } from '../api'
import { Info } from '../components/Info'
import { JobLog } from '../components/JobLog'
import { Disclosure, ErrorText, Field, SubmitTextarea, type FixRequest } from '../components/ui'
import { useEvents } from '../events'
import { autopilotLine, autopilotSecondsLeft, fmtDuration, humanError, plainJargon, platformPlain, stageEstimate, stageGerund, stagePlain, suggestFallback, VARIANT_EXAMPLES } from '../help'
import { autopilotStage, isAutopilotActive, type AutopilotActions } from '../pipeline/AutopilotPanel'
import { ItemsPanel } from '../pipeline/ItemsPanel'
import { COMPOSE_CATEGORIES, ComposePanel } from '../pipeline/ComposePanel'
import { HairRailLine, HairRebuildNote, HairSettingPanel, useHairPanel } from '../pipeline/HairSetting'
import { StagePanel } from '../pipeline/StagePanel'
import { ViewerLookContext } from '../previewLight'
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
  // "Add an item" (items): the panel on a character with a skeleton.
  const [itemsOpen, setItemsOpen] = useState(false)
  // Step 0.5: a character from several of your own pictures (an optional step before step 1).
  const [composeOpen, setComposeOpen] = useState(false)
  const [baseName, setBaseName] = useState<string | null>(null)
  // Hair from the library: the "Hair" line and its Change panel (characters only; nothing shows on an older backend).
  const [hairOpen, setHairOpen] = useState(false)
  // The autopilot state has one home: asset.autopilot. Asset reloads, the websocket
  // event, the mount fetch and the action responses all go through newerAutopilot.
  // apAvailable is null until known, false when the backend has no autopilot (404).
  const [apAvailable, setApAvailable] = useState<boolean | null>(null)
  const [apDismissed, setApDismissed] = useState<string | null>(null) // updated_at of a dismissed paused card
  const [apBusy, setApBusy] = useState(false)
  const reloadTimer = useRef<number | null>(null)
  const followRef = useRef<string | null>(null) // the autopilot stage the page last followed to
  const jobStatusRef = useRef(new Map<string, JobStatus>()) // last seen status per job id
  const autopilot = asset && asset.id === aid ? (asset.autopilot ?? null) : null
  // The status line in the header shows only while the guide card is scrolled out of view (UI-14).
  const [guideVisible, setGuideVisible] = useState(true)
  const haveAsset = !!asset
  useEffect(() => {
    const main = document.querySelector('.stage-main')
    const card = main?.querySelector('.guide')
    if (!main || !card || typeof IntersectionObserver === 'undefined') {
      setGuideVisible(false)
      return
    }
    const io = new IntersectionObserver(([e]) => setGuideVisible(e.isIntersecting), { root: main, threshold: 0.2 })
    io.observe(card)
    return () => io.disconnect()
  })
  // Window title: where you are and, while work runs, progress (UI-38).
  useEffect(() => {
    if (!asset || asset.id !== aid) return
    const k = STAGES.indexOf(stage) + 1
    document.title = `${asset.name}, step ${k} | VGAC`
    return () => {
      document.title = 'Video Game Asset Creator'
    }
  }, [asset, aid, stage])
  // Keyboard shortcuts for the review loop (UI-40): K keep, R try again, C change, arrows for versions, 1 to 6 for steps.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.ctrlKey || e.metaKey || e.altKey || (t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable))) return
      if (document.querySelector('[role=dialog]')) return
      const byText = (txt: string) => [...document.querySelectorAll<HTMLButtonElement>('.happy-answers button')].find((b) => b.innerText.trim().toLowerCase().startsWith(txt))
      const k = e.key.toLowerCase()
      if (k === 'k') byText('yes, keep')?.click()
      else if (k === 'r') byText('try again')?.click()
      else if (k === 'c') byText('change something')?.click()
      else if (k === 'arrowleft' || k === 'arrowright') {
        const list = [...document.querySelectorAll<HTMLButtonElement>('.candidate')]
        const i = list.findIndex((b) => b.getAttribute('aria-pressed') === 'true')
        list[i + (k === 'arrowright' ? 1 : -1)]?.click()
      } else if (/^[1-6]$/.test(k) && aid) navigate(`/assets/${aid}/${STAGES[Number(k) - 1]}`)
      else return
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [aid, navigate])
  // A new step opens at its top, not where the last one was scrolled to (UI-15).
  useEffect(() => {
    document.querySelector('.stage-main')?.scrollTo({ top: 0 })
  }, [stage, aid, haveAsset])
  const hair = useHairPanel(asset && asset.id === aid ? asset : null)

  const applyAutopilot = useCallback((assetId: string, st: AutopilotState) => {
    setAsset((cur) => {
      if (!cur || cur.id !== assetId) return cur
      const next = newerAutopilot(cur.autopilot ?? null, st)
      return next === cur.autopilot ? cur : { ...cur, autopilot: next }
    })
  }, [])

  const reload = useCallback(async () => {
    try {
      const a = await api.getAsset(aid)
      // A reload that started before a newer websocket state must not undo it.
      setAsset((cur) => (cur && cur.id === a.id && cur.autopilot ? { ...a, autopilot: newerAutopilot(cur.autopilot, a.autopilot ?? null) } : a))
      setError(null)
      if ('autopilot' in a) setApAvailable(true)
    } catch (e) {
      setError(e)
    }
  }, [aid])

  // Load the asset, then read the autopilot state once (this also finds out whether
  // the backend has the autopilot at all). After that the websocket pushes it.
  useEffect(() => {
    setApAvailable(null)
    setApDismissed(null)
    followRef.current = null
    jobStatusRef.current = new Map()
    let alive = true
    void reload()
      .then(() => api.getAutopilot(aid))
      .then(
        (st) => {
          if (!alive) return
          setApAvailable(true)
          applyAutopilot(aid, st)
        },
        (e) => {
          if (!alive) return
          if (e instanceof ApiError && e.status === 404) setApAvailable(false)
        },
      )
    api.listJobs({ asset_id: aid }).then(
      (list) => {
        if (!alive) return
        for (const j of list) jobStatusRef.current.set(j.id, j.status)
        setJobs(list)
      },
      () => {
        if (alive) setJobs([])
      },
    )
    return () => {
      alive = false
    }
  }, [aid, reload, applyAutopilot])

  // Slow fallback while the autopilot runs, in case a websocket event was missed.
  const apRunning = autopilot?.status === 'running'
  useEffect(() => {
    if (!apRunning) return
    const t = window.setInterval(() => {
      api.getAutopilot(aid).then(
        (st) => applyAutopilot(aid, st),
        () => undefined,
      )
    }, AUTOPILOT_FALLBACK_MS)
    return () => window.clearInterval(t)
  }, [aid, apRunning, applyAutopilot])

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
        // Progress events only move the bar; the asset is refetched when a job changes status.
        const seen = jobStatusRef.current
        const before = seen.get(ev.job.id)
        seen.set(ev.job.id, ev.job.status)
        if (before !== ev.job.status) reloadSoon()
      } else if (ev.type === 'version' && ev.version.asset_id === aid) {
        reloadSoon()
      } else if (ev.type === 'autopilot' && ev.asset_id === aid) {
        setApAvailable(true)
        applyAutopilot(aid, ev.autopilot)
        reloadSoon()
      }
    },
    [aid, reloadSoon, applyAutopilot],
  )

  const apCall = async (fn: () => Promise<AutopilotState | void>) => {
    setApBusy(true)
    try {
      const st = await fn()
      if (st) applyAutopilot(aid, st)
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
    // Stop acts now: the autopilot pauses and the step in progress stops. Resume carries on from that step.
    stop: () => void apCall(async () => (await api.stopAsset(aid)).autopilot),
    resume: () => void apCall(async () => (await api.resumeAsset(aid)).autopilot),
    resumeFrom: (from: StageId) =>
      void apCall(() => {
        const keep = autopilot && autopilot.status !== 'idle' ? { stop_on: autopilot.stop_on as AutopilotBody['stop_on'], until_stage: autopilot.until_stage as StageId } : {}
        return api.startAutopilot(aid, { ...keep, from_stage: from })
      }),
    // Proposed contract: POST autopilot {fix: {stage, params}} re-runs that stage with the fix, then continues.
    fixAndContinue: (fix: FixRequest) => void apCall(() => api.startAutopilot(aid, { fix: { stage: fix.stage, params: fix.params, ...(fix.op === 'refine' && fix.instruction ? { op: fix.op, instruction: fix.instruction } : {}) } })),
    continueAnyway: () =>
      void apCall(async () => {
        const vid = autopilot?.version_id
        const s = autopilotStage(autopilot)
        if (vid && s && asset && asset.selected?.[s] !== vid) await api.select(vid)
        return api.startAutopilot(aid, {})
      }),
    // Steer by hand: stored on the asset, so coming back later shows no autopilot card and no old Resume.
    // An older backend without the call still hides the card for this visit.
    dismiss: () => {
      setApDismissed(autopilot?.updated_at ?? 'dismissed')
      void api.dismissAutopilot(aid).then(
        (st) => {
          applyAutopilot(aid, st)
          reloadSoon()
        },
        () => undefined,
      )
    },
  }
  // A dismissed paused or stopped card stays hidden until the state changes again.
  const apShown = autopilot && apDismissed !== null && apDismissed === (autopilot.updated_at ?? 'dismissed') && autopilot.status !== 'running' ? null : autopilot

  // Stop and Resume for one step (the same calls stop or resume the autopilot when it is the one running).
  const stepCall = async (fn: () => Promise<{ autopilot: AutopilotState } | unknown>) => {
    try {
      const out = await fn()
      if (out && typeof out === 'object' && 'autopilot' in out) applyAutopilot(aid, (out as { autopilot: AutopilotState }).autopilot)
      setError(null)
      reloadSoon()
    } catch (e) {
      setError(e)
    }
  }
  const stopStep = (s: StageId) => void stepCall(() => api.stopAsset(aid, s))
  const resumeStep = (s: StageId) => void stepCall(() => api.resumeAsset(aid, s))
  const dismissStopped = (s: StageId) => void stepCall(() => api.dismissStopped(aid, s))

  const cancelJob = async (id: string) => {
    try {
      const j = await api.cancelJob(id)
      jobStatusRef.current.set(j.id, j.status)
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
    <ViewerLookContext.Provider value={asset.style_preset}>
    <div className={'pipeline-layout' + (showLog ? ' with-log' : '')}>
      <aside className="stage-rail">
        <div className="rail-head">
          <Link to={`/projects/${asset.project_id}`} className="crumb">
            Back to assets
          </Link>
          <RailEdit asset={asset} field="name" onSaved={reload} onError={setError} />
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
          {asset.held_item?.character_id && (
            <div className="muted small">
              item for <Link to={`/assets/${asset.held_item.character_id}`}>{asset.held_item.character_name ?? 'its character'}</Link>
              <Info term="term.items" />
            </div>
          )}
          {(asset.equipment?.set_aside?.length ?? 0) > 0 && (
            <div className="muted small">
              made separately:{' '}
              {asset.equipment!.set_aside.map((it, i) => (
                <span key={it.item_asset_id}>
                  {i > 0 ? ', ' : ''}
                  <Link to={`/assets/${it.item_asset_id}`}>{it.label}</Link>
                </span>
              ))}
              <Info term="term.items" />
            </div>
          )}
          {hair.panel && <HairRailLine panel={hair.panel} open={hairOpen} onToggle={() => setHairOpen((v) => !v)} />}
          {asset.parts.length > 0 && (
            <div className="muted small">
              parts: {asset.parts.join(', ')}
              <Info term="term.parts" />
            </div>
          )}
        </div>
        <nav className="rail-stages" aria-label="Progress">
          {COMPOSE_CATEGORIES.includes(asset.category) && (
            <div className={'rail-stage rail-stage-half reached' + (composeOpen ? ' active' : '')}>
              <button type="button" className="rail-stage-btn" aria-pressed={composeOpen} onClick={() => setComposeOpen((v) => !v)} title="Optional: make the character from several of your own pictures (a face, a body, an outfit). The results arrive in step 1.">
                <span className="sdot sdot-idle" />
                <span className="rail-index">0.5</span>
                <span className="rail-label">From your pictures</span>
                <span className="rail-meta">extra</span>
              </button>
              <Info term="term.compose" />
            </div>
          )}
          {STAGES.map((s, i) => {
            const st = stageState(asset, s)
            const list = versionsOf(asset, s)
            const kept = list.some((v) => v.selected || asset.selected[s] === v.id)
            const reached = list.length > 0
            const cls = ['rail-stage', s === stage && !composeOpen ? 'active' : '', reached ? 'reached' : 'future', kept ? 'kept' : ''].join(' ')
            const stopped = st !== 'running' && stoppedVersions(asset, s).length > 0
            // A step with a Stop or Resume button next to it needs no status word: the button says it.
            const meta = st === 'running' ? '' : stopped ? 'stopped' : st === 'error' ? 'failed' : kept ? 'kept' : st === 'done' ? 'choose one' : ''
            const help = stageInfo(s)
            return (
              <div key={s} className={cls + (stopped ? ' stopped' : '')}>
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
            Prompt (description)
            <Info term="term.prompt" />
          </div>
          <RailEdit asset={asset} field="prompt" onSaved={reload} onError={setError} />
        </div>
      </aside>
      <section className="stage-main">
        <header className="asset-head">
          <div className={'asset-status' + (guideVisible ? ' asset-status-quiet' : '')}>
            <span className={'traffic traffic-' + status.tone} aria-hidden="true" />
            <span className="asset-status-text">{status.text}</span>
            {status.raw && (
              <Disclosure label="Show details" className="inline-disclosure">
                <pre className="raw-error">{status.raw}</pre>
              </Disclosure>
            )}
          </div>
          <div className="row-gap">
            <button type="button" className="small-btn" aria-pressed={variantOpen} disabled={!asset.selected?.concept} onClick={() => setVariantOpen((v) => !v)} title={asset.selected?.concept ? 'Make a copy of this asset with one change' : 'Keep a concept picture first'}>
              Make a variant
            </button>
            <Info term="term.variant" />
            {['humanoid', 'quadruped', 'multileg', 'winged', 'serpent'].includes(asset.category) && (
              <button type="button" className="small-btn" aria-pressed={itemsOpen} onClick={() => setItemsOpen((v) => !v)} title="Give the character a sword, a shield, a hat or a backpack">
                Add an item
              </button>
            )}
            <button type="button" className="small-btn" aria-pressed={showLog} onClick={() => setShowLog((v) => !v)} title="Show what the app did and when: every job, with its time and result">
              Activity
            </button>
          </div>
        </header>
        {variantOpen && <VariantPanel asset={asset} onClose={() => setVariantOpen(false)} onError={setError} />}
        {itemsOpen && <ItemsPanel asset={asset} onClose={() => setItemsOpen(false)} />}
        {composeOpen && (
          <ComposePanel
            asset={asset}
            onClose={() => setComposeOpen(false)}
            onCreated={() => {
              if (stage !== 'concept') navigate(`/assets/${asset.id}/concept`)
              reloadSoon()
              api.listJobs({ asset_id: aid }).then(setJobs, () => undefined)
            }}
            onError={setError}
          />
        )}
        {hairOpen && hair.panel && (
          <HairSettingPanel
            asset={asset}
            panel={hair.panel}
            onClose={() => setHairOpen(false)}
            onSaved={(p) => {
              hair.setPanel(p)
              reloadSoon()
              api.listJobs({ asset_id: aid }).then(setJobs, () => undefined)
            }}
            onError={setError}
          />
        )}
        {hair.panel && autopilot?.status !== 'running' && <HairRebuildNote asset={asset} panel={hair.panel} jobs={jobs} busy={apBusy} onRebuild={(from) => autopilotActions.resumeFrom(from)} />}
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
          onStop={() => stopStep(stage)}
          onResume={() => resumeStep(stage)}
          onDismissStopped={() => dismissStopped(stage)}
          hideStaleFrom={hair.panel?.rebuild && autopilot?.status !== 'running' ? (hair.panel.rebuild.from_stage as StageId) : null}
        />
      </section>
      {showLog && (
        <aside className="job-col">
          <JobLog jobs={jobs} onCancel={cancelJob} />
        </aside>
      )}
    </div>
    </ViewerLookContext.Provider>
  )
}

const AUTOPILOT_FALLBACK_MS = 30000

// The newer of two autopilot states by updated_at (the incoming one wins a tie).
// Returns cur itself when nothing changed, so callers can skip a re-render.
// The backend never clears a state once set, so a missing incoming state keeps cur.
function newerAutopilot(cur: AutopilotState | null, next: AutopilotState | null): AutopilotState | null {
  if (!next) return cur
  if (!cur) return next
  const a = cur.updated_at ? Date.parse(cur.updated_at) : NaN
  const b = next.updated_at ? Date.parse(next.updated_at) : NaN
  if (Number.isFinite(a) && Number.isFinite(b) && b < a) return cur
  if (JSON.stringify(cur) === JSON.stringify(next)) return cur
  return next
}

// "Make a variant": a sibling asset with one change that keeps this one's identity.
// The asset's name or description, editable in place (UI-04, UI-26). A changed description offers a new step 1 picture.
function RailEdit({ asset, field, onSaved, onError }: { asset: Asset; field: 'name' | 'prompt'; onSaved: () => Promise<unknown> | void; onError: (e: unknown) => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [asked, setAsked] = useState(false)
  const [busy, setBusy] = useState(false)
  const current = field === 'name' ? asset.name : asset.prompt
  const isName = field === 'name'
  const save = async (thenRun: boolean) => {
    setBusy(true)
    try {
      await api.updateAsset(asset.id, { [field]: text.trim() })
      if (thenRun) await api.runStage(asset.id, 'concept', {})
      await onSaved()
      setEditing(false)
      setAsked(false)
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }
  if (!editing) {
    return (
      <div className={isName ? 'asset-title rail-edit' : 'small rail-edit'}>
        <span>{current}</span>
        <button type="button" className="link small rail-edit-btn" aria-label={isName ? 'Rename this asset' : 'Edit the description'} onClick={() => { setText(current ?? ''); setEditing(true) }}>
          Edit
        </button>
      </div>
    )
  }
  if (asked) {
    return (
      <div className="col-tight small">
        <div>Make a new step 1 picture from this description?</div>
        <div className="row-gap">
          <button type="button" className="primary small-btn" disabled={busy} onClick={() => save(true)}>Make it</button>
          <button type="button" className="small-btn" disabled={busy} onClick={() => save(false)}>Just save the text</button>
        </div>
      </div>
    )
  }
  const ok = text.trim().length > 0
  const submit = () => {
    if (!ok || text.trim() === (current ?? '').trim()) return setEditing(false)
    if (isName) void save(false)
    else setAsked(true)
  }
  return (
    <div className="col-tight">
      {isName ? (
        <input value={text} autoFocus aria-label="Asset name" maxLength={200} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') setEditing(false) }} />
      ) : (
        <textarea value={text} rows={4} autoFocus aria-label="Description" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false) }} />
      )}
      <div className="row-gap">
        <button type="button" className="primary small-btn" disabled={busy || !ok} onClick={submit}>Save</button>
        <button type="button" className="small-btn" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}

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
          Close
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
    return { text: `Autopilot ${autopilot.status === 'paused' ? 'paused' : 'stopped'}${s ? ` at "${stagePlain(s)}"` : ''}: ${plainJargon(autopilot.reason) || 'it needs you.'}`, tone: autopilot.status === 'paused' ? 'warn' : 'bad' }
  }
  if (job) {
    const s = stageOfJob(job, asset)
    const step = s ? `step ${STAGES.indexOf(s) + 1} of ${STAGES.length}` : ''
    if (job.status === 'queued') return { text: `${s ? stageGerund(s) : 'Working'}: waiting for the graphics card${step ? `, ${step}` : ''}.`, tone: 'warn' }
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
