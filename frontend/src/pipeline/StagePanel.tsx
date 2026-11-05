import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router'
import {
  api,
  ApiError,
  libraryUrl,
  previousStage,
  selectedVersion,
  TURNAROUND_VIEWS,
  versionFeedback,
  versionsOf,
  type AdjustParam,
  type Asset,
  type AutopilotState,
  type Check,
  type Feedback,
  type FeedbackBody,
  type Job,
  type StageId,
  type Suggestion,
  type Version,
} from '../api'
import { Info } from '../components/Info'
import { firstStaleStage, isStopped, STAGES as STAGE_LIST } from '../api'
import { ResumeButton } from '../components/ui'
import { ChecksList, Disclosure, isCheckList, MetricChips, PlainChecks, StatusBadge, SubmitTextarea, timeAgo, type FixRequest } from '../components/ui'
import { preferredConceptModel } from '../guide'
import { FIX_TOOLS, humanError, modelLabel, paramPlain, stagePlaceholder, stagePlain } from '../help'
import { useRefData, type RefData } from '../refdata'
import { type AutopilotActions } from './AutopilotPanel'
import { CandidateStrip, shortId, ThumbIcon } from './CandidateStrip'
import { FineTune } from './FineTune'
import { OwnPictures, ReferencePicture } from './OwnPictures'
import { finishShown, GuidePanel, guideState } from './GuidePanel'
import { readToolState, toolPath, type ToolFocus } from './openTool'
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
  autopilot: AutopilotState | null
  apAvailable: boolean | null
  autopilotActions: AutopilotActions
  onMakeVariant: () => void
  onChanged: () => void
  onError: (e: unknown) => void
  // Stop this step now, run the stopped step again with the same settings, or leave it stopped.
  onStop: () => void
  onResume: () => void
  onDismissStopped: () => void
}

export function StagePanel({ asset, stage, jobs, suggestion, baseName, autopilot, apAvailable, autopilotActions, onMakeVariant, onChanged, onError, onStop, onResume, onDismissStopped }: Props) {
  const navigate = useNavigate()
  const versions = versionsOf(asset, stage)
  const selected = selectedVersion(asset, stage)
  const [activeId, setActiveId] = useState<string | null>(null)
  const active = useMemo(() => versions.find((v) => v.id === activeId) ?? selected ?? versions[versions.length - 1], [versions, activeId, selected])
  const prev = previousStage(stage, asset.category)
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

  // A Fix that opens a tool (2.5): go to the fix's stage, open "Change something" on the kept version and focus
  // the tool there. Nothing runs until the person applies it.
  const location = useLocation()
  const [toolFocus, setToolFocus] = useState<ToolFocus | null>(null)
  const openTool = (f: FixRequest) => {
    const { to, state: navState } = toolPath(asset.id, f)
    if (f.stage !== stage) {
      navigate(to, { state: navState })
      return
    }
    if (selected) setActiveId(selected.id)
    setChangeOpen(true)
    setToolFocus(navState.openTool)
  }
  const handledTool = useRef<number | null>(null)
  useEffect(() => {
    const t = readToolState(location.state, stage)
    if (!t || handledTool.current === t.nonce) return
    handledTool.current = t.nonce
    if (selected) setActiveId(selected.id)
    setChangeOpen(true)
    setToolFocus(t)
    // Drop the request from history so going back and forth does not open the tool again.
    navigate(location.pathname, { replace: true, state: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key, stage])

  const fix = (f: FixRequest) => {
    if (f.ui) {
      openTool(f)
      return
    }
    return act(async () => {
      if (f.op === 'refine' && f.instruction && f.stage === stage && active) {
        // "Redraw it ready for 3D": the refine on this version with the fixed instruction.
        await api.refine(active.id, { instruction: f.instruction, params: f.params })
      } else if (f.stage === stage && active) {
        await api.adjust(active.id, f.params)
      } else {
        await api.runStage(asset.id, f.stage, { params: f.params })
        navigate(`/assets/${asset.id}/${f.stage}`)
      }
    })
  }

  const checks = active && isCheckList(active.metrics.checks) ? (active.metrics.checks as Check[]) : null
  const ctx = { platform: asset.platform_target, category: asset.category }
  // The finishing card above already shows the export's title, model and downloads.
  const hideReady = stage === 'export' && !!active && active.id === selected?.id && finishShown(asset, stage, autopilot, jobs)

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
        autopilot={autopilot}
        apAvailable={apAvailable}
        autopilotActions={autopilotActions}
        onMakeVariant={onMakeVariant}
        onRun={run}
        onRunNext={runNext}
        onRetry={retry}
        onStop={onStop}
        onResume={onResume}
        onDismissStopped={onDismissStopped}
        onFix={fix}
        onOpenTool={openTool}
        onChanged={onChanged}
      />

      {(() => {
        // An earlier step was changed after later steps were made: offer to rebuild them.
        const stale = firstStaleStage(asset)
        const apBusy = autopilot?.status === 'running' || autopilot?.status === 'paused' || autopilot?.status === 'error'
        if (!stale || apBusy || apAvailable === false || jobs.some((j) => j.status === 'running' || j.status === 'queued')) return null
        const name = stagePlain(stale, ref?.stages.find((s) => s.id === stale) ?? null)
        return (
          <div className="stale-note">
            <span>
              Step {STAGE_LIST.indexOf(stale) + 1} ("{name}") and the steps after it were built from an older result of an earlier step.
            </span>
            <ResumeButton small label={`Rebuild from "${name}"`} disabled={busy} onClick={() => autopilotActions.resumeFrom(stale)} title="Makes that step again from what you kept, and rebuilds every step after it. The results you have now stay in the history." />
          </div>
        )
      })()}

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
        <OwnPictures asset={asset} stage={stage} active={active} canRun={canRun} busy={busy} act={act} onCreated={(id) => setActiveId(id)} />
        <CandidateStrip versions={versions} stage={stage} jobs={jobs} activeId={active?.id ?? null} selectedId={selected?.id ?? null} onPick={setActiveId} />
        {versions.length === 0 && <div className="placeholder-box">Nothing here yet. Use the button above to start.</div>}
        {active && (
          <div className="active-version">
            <div className="row-between">
              <div className="row-gap">
                <strong>Version {versions.indexOf(active) + 1}</strong>
                <span className="muted small mono">{shortId(active.id)}</span>
                <StatusBadge status={active.status} stopped={isStopped(active)} />
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
            <StageViewer stage={stage} version={active} category={asset.category} assetName={asset.name} onChanged={onChanged} hideReady={hideReady} onRefineView={(view) => act(() => api.refine(active.id, { instruction: `Regenerate the ${view} view`, view }))} />
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
                onFeedbackSaved={onChanged}
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
                focus={toolFocus}
                onRefine={(instruction, view, reference) => act(() => api.refine(active.id, { instruction, view, ...(reference ? { reference_image: reference } : {}) }))}
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

function StageViewer({ stage, version, category, assetName, onChanged, hideReady, onRefineView }: { stage: StageId; version: Version; category: string; assetName: string; onChanged: () => void; hideReady?: boolean; onRefineView: (view: string) => void }) {
  // A queued version cancelled before it ran is rejected with no files.
  if (isStopped(version)) {
    return <div className="placeholder-box">You stopped this version before it finished. Resume (above) runs it again with the same settings.</div>
  }
  if (version.status === 'rejected' && Object.keys(version.files).length === 0) {
    return <div className="placeholder-box">This version was stopped before it made a result.</div>
  }
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
      return <MeshView version={version} category={category} />
    case 'rig':
      return <RigView version={version} />
    case 'export':
      return <ExportView version={version} hideReady={hideReady} assetName={assetName} onChanged={onChanged} />
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
  onFeedbackSaved,
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
  onFeedbackSaved: () => void
}) {
  const fb = useFeedback(active, onFeedbackSaved)
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
        <FeedbackButtons fb={fb} />
        <button type="button" className="link small" disabled={busy || active.status !== 'done'} onClick={onReject} title="Hide this version; it stays in the history">
          Reject
        </button>
      </div>
      <FeedbackNote key={active.id} fb={fb} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Thumbs up or down (POST /api/versions/{vid}/feedback). It only records which
// results the person liked; a thumbs down opens an optional "What was wrong?" note.
// ---------------------------------------------------------------------------

type FeedbackState = {
  current: Feedback
  saving: boolean
  error: string | null
  send: (body: FeedbackBody) => void
}

function useFeedback(version: Version, onSaved: () => void): FeedbackState {
  const stored = versionFeedback(version)
  const storedKey = `${version.id}|${stored?.thumbs ?? ''}|${stored?.note ?? ''}`
  const [current, setCurrent] = useState<Feedback>(stored ?? { thumbs: null })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Follow the stored value when the version changes or the backend sends a newer one.
  useEffect(() => {
    setCurrent(versionFeedback(version) ?? { thumbs: null })
    setError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storedKey])

  const send = (body: FeedbackBody) => {
    const before = current
    setCurrent({ thumbs: body.thumbs, note: body.thumbs === 'down' ? (body.note ?? before.note ?? null) : null })
    setSaving(true)
    setError(null)
    api.feedback(version.id, body).then(
      (v) => {
        setSaving(false)
        const saved = versionFeedback(v)
        if (saved) setCurrent(saved)
        onSaved()
      },
      (e) => {
        setSaving(false)
        setCurrent(before)
        setError(e instanceof ApiError && e.status === 404 ? 'This backend does not record likes yet.' : 'Could not save that. Try again.')
      },
    )
  }
  return { current, saving, error, send }
}

function FeedbackButtons({ fb }: { fb: FeedbackState }) {
  const t = fb.current.thumbs
  // Clicking the pressed button again clears the answer.
  const toggle = (want: 'up' | 'down') => {
    if (t === want) fb.send({ thumbs: null })
    else if (want === 'down' && fb.current.note) fb.send({ thumbs: 'down', note: fb.current.note })
    else fb.send({ thumbs: want })
  }
  return (
    <span className="answer-group feedback-group">
      <span className="muted small">
        Liked it?
        <Info term="term.feedback" />
      </span>
      <button
        type="button"
        className="thumb-btn"
        aria-pressed={t === 'up'}
        disabled={fb.saving}
        onClick={() => toggle('up')}
        title={t === 'up' ? 'You liked this. Click again to clear.' : 'Thumbs up: you liked this result'}
      >
        <ThumbIcon down={false} />
        <span>Thumbs up</span>
      </button>
      <button
        type="button"
        className="thumb-btn"
        aria-pressed={t === 'down'}
        disabled={fb.saving}
        onClick={() => toggle('down')}
        title={t === 'down' ? 'You did not like this. Click again to clear.' : 'Thumbs down: you did not like this result'}
      >
        <ThumbIcon down />
        <span>Thumbs down</span>
      </button>
    </span>
  )
}

function FeedbackNote({ fb }: { fb: FeedbackState }) {
  const savedNote = fb.current.note ?? ''
  const [text, setText] = useState(savedNote)
  useEffect(() => setText(savedNote), [savedNote])
  if (fb.current.thumbs !== 'down') return fb.error ? <div className="feedback-error small">{fb.error}</div> : null
  const changed = text.trim() !== savedNote.trim()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!changed || fb.saving) return
    fb.send({ thumbs: 'down', note: text.trim() })
  }
  return (
    <form className="feedback-note" onSubmit={submit}>
      <label className="field-label" htmlFor="feedback-note-input">
        What was wrong?
        <Info term="term.feedback_note" label="About this note" />
      </label>
      <div className="row-gap">
        <input
          id="feedback-note-input"
          value={text}
          maxLength={200}
          onChange={(e) => setText(e.target.value)}
          placeholder="Optional, e.g. face too dark, sword bent"
          disabled={fb.saving}
        />
        <button type="submit" className="small-btn" disabled={fb.saving || !changed}>
          {fb.saving ? 'Saving...' : 'Save note'}
        </button>
        {!changed && savedNote && <span className="muted small">Saved.</span>}
      </div>
      {fb.error && <div className="feedback-error small">{fb.error}</div>}
    </form>
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
  focus,
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
  focus: ToolFocus | null
  onRefine: (instruction: string, view?: string, reference?: string) => void
  onAdjust: (params: Record<string, unknown>) => void
}) {
  const [text, setText] = useState('')
  const [view, setView] = useState('')
  const [reference, setReference] = useState<string | null>(null)
  // The experimental reference picture only goes to the first picture of a character, creature or prop.
  const takesReference = stage === 'concept' && asset.category !== 'material'
  const panelRef = useRef<HTMLDivElement>(null)
  const describeRef = useRef<HTMLTextAreaElement>(null)
  // A tool with no Fine-tune setting of its own (the parts editor until it is built) lands on "Describe the change".
  const knobShown = !!focus?.knob && schema.some((p) => p.name === focus.knob)
  const missingTool = focus?.ui && !knobShown ? focus.ui : null
  useEffect(() => {
    if (!focus || knobShown) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    panelRef.current?.scrollIntoView({ block: 'center', behavior: reduce ? 'auto' : 'smooth' })
    describeRef.current?.focus({ preventScroll: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce])
  const ok = text.trim().length > 0
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ok) return
    onRefine(text.trim(), view || undefined, takesReference && reference ? reference : undefined)
    setText('')
    setReference(null)
  }
  const availableViews = TURNAROUND_VIEWS.filter((v) => v in target.files)
  return (
    <div className="change-panel" ref={panelRef}>
      {missingTool && (
        <div className="tool-note small">
          {FIX_TOOLS[missingTool]?.plain ? FIX_TOOLS[missingTool].plain.charAt(0).toUpperCase() + FIX_TOOLS[missingTool].plain.slice(1) : 'That tool'} is not in this version of the app yet. Describe the change below instead; nothing runs until you send it.
        </div>
      )}
      <form className="change-describe" onSubmit={submit}>
        <div className="field-label">
          Describe the change
          <Info term="term.instruction" />
        </div>
        <div className="refine-row">
          <SubmitTextarea ref={describeRef} rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={stagePlaceholder(stage)} aria-label="Describe the change" />
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
        {takesReference && <ReferencePicture value={reference} onChange={setReference} disabled={disabled} />}
      </form>
      <FineTune stage={stage} schema={schema} target={target} asset={asset} refData={refData} suggestion={suggestion} disabled={disabled} onSubmit={onAdjust} focus={focus} />
    </div>
  )
}
