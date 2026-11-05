// "Your next step": one card that says what just happened, what to do now and the
// single primary action with a time estimate. Exactly one primary button exists on
// the page: it lives here, except in the review state where it is the "Yes, keep it"
// answer next to the picture.
import { Link } from 'react-router'
import { libraryUrl, previousStage, STAGES, versionsOf, type Asset, type AutopilotState, type Job, type StageId, type StageInfo, type Version } from '../api'
import { Info } from '../components/Info'
import { Disclosure, type FixRequest } from '../components/ui'
import { fmtDuration, humanError, looksGood, stageEstimate, stageGerund, stageHelp, stagePlain, stageVerb } from '../help'
import { AutopilotCard, FinishCard, isAutopilotActive, isFinished, MakeItAction, type AutopilotActions } from './AutopilotPanel'

export type GuideState = 'blocked' | 'empty' | 'running' | 'error' | 'review' | 'selected' | 'finished'

type Props = {
  asset: Asset
  stage: StageId
  jobs: Job[]
  stages: StageInfo[] | null
  estimates: Record<string, number> | null
  active: Version | undefined
  selected: Version | undefined
  busy: boolean
  baseName?: string | null
  // Autopilot: null state when the backend has no autopilot yet (apAvailable false).
  autopilot: AutopilotState | null
  apAvailable: boolean | null
  autopilotActions: AutopilotActions
  onMakeVariant: () => void
  onRun: () => void
  onRunNext: (next: StageId) => void
  onRetry: (failed: Version) => void
  onCancel: (jobId: string) => void
  // A check's Fix: re-run (onFix) or open a tool (onOpenTool, 2.5). Used by the finish card and the autopilot card.
  onFix?: (fix: FixRequest) => void
  onOpenTool?: (fix: FixRequest) => void
  // Reload the asset (the finish card's Unity hand-off after "Check again").
  onChanged?: () => void
}

// Whether the finishing card (turntable, downloads, what next) shows for this stage.
export function finishShown(asset: Asset, stage: StageId, autopilot: AutopilotState | null, jobs: Job[]): boolean {
  if (!isFinished(asset)) return false
  if (isAutopilotActive(autopilot)) return false
  if (guideState(asset, stage, jobs, undefined) === 'running') return false
  return autopilot?.status === 'done' || stage === 'export'
}

export function nextStage(stage: StageId): StageId | null {
  const i = STAGES.indexOf(stage)
  return i < STAGES.length - 1 ? STAGES[i + 1] : null
}

export function activeJobFor(jobs: Job[], versions: Version[]): Job | undefined {
  const ids = new Set(versions.map((v) => v.id))
  const live = jobs.filter((j) => (j.status === 'running' || j.status === 'queued') && j.version_id && ids.has(j.version_id))
  return live.find((j) => j.status === 'running') ?? live[0]
}

export function guideState(asset: Asset, stage: StageId, jobs: Job[], selected: Version | undefined): GuideState {
  const versions = versionsOf(asset, stage)
  const prev = previousStage(stage, asset.category)
  const prevSelected = prev ? asset.selected?.[prev] || versionsOf(asset, prev).some((v) => v.selected) : true
  if (versions.some((v) => v.status === 'running' || v.status === 'queued') || activeJobFor(jobs, versions)) return 'running'
  if (selected && selected.status === 'done') return stage === 'export' ? 'finished' : 'selected'
  if (versions.some((v) => v.status === 'done')) return 'review'
  const last = versions[versions.length - 1]
  if (last && last.status === 'error') return 'error'
  if (!prevSelected) return 'blocked'
  return 'empty'
}

export function GuidePanel({ asset, stage, jobs, stages, estimates, active, selected, busy, baseName, autopilot, apAvailable, autopilotActions, onMakeVariant, onRun, onRunNext, onRetry, onCancel, onFix, onOpenTool, onChanged }: Props) {
  const info = (s: StageId) => stages?.find((x) => x.id === s) ?? null
  const versions = versionsOf(asset, stage)
  const prev = previousStage(stage, asset.category)
  const next = nextStage(stage)
  const state = guideState(asset, stage, jobs, selected)
  const est = stageEstimate(stage, info(stage), estimates)
  const stepNo = STAGES.indexOf(stage) + 1

  // The autopilot owns the card while it runs, is paused or stopped.
  if (autopilot && isAutopilotActive(autopilot)) {
    return <AutopilotCard asset={asset} stage={stage} autopilot={autopilot} jobs={jobs} stages={stages} estimates={estimates} busy={busy} actions={autopilotActions} onOpenTool={onOpenTool} />
  }
  // The finishing moment, once every step is kept.
  if (finishShown(asset, stage, autopilot, jobs)) {
    return <FinishCard asset={asset} byAutopilot={autopilot?.status === 'done'} onMakeVariant={onMakeVariant} onFix={onFix} busy={busy} onChanged={onChanged} />
  }

  // "Make it" shows whenever nothing is running and the asset is not finished.
  // It is the primary button except while a result waits to be judged (review:
  // "Yes, keep it" stays primary) and after a failure (Try again stays primary).
  const makeIt =
    apAvailable !== false && state !== 'running' && !isFinished(asset) ? (
      <MakeItAction asset={asset} stages={stages} estimates={estimates} busy={busy} primary={state !== 'review' && state !== 'error'} onStart={(body) => autopilotActions.start(body)} />
    ) : null
  const secondary = (node: React.ReactNode) => (makeIt && state !== 'review' && state !== 'error' ? <span className="or-just">{node}</span> : node)

  let happened: string
  let now: string
  let action: React.ReactNode = null
  let extra: React.ReactNode = null

  switch (state) {
    case 'blocked': {
      happened = 'Nothing here yet.'
      now = `First ${stageVerb(prev!, info(prev!)).toLowerCase()} and keep one of the results.`
      action = (
        <Link className={'btn' + (makeIt ? '' : ' primary')} to={`/assets/${asset.id}/${prev}`}>
          Go to {stagePlain(prev!, info(prev!))}
        </Link>
      )
      break
    }
    case 'empty': {
      happened = prev ? `You kept a result from "${stagePlain(prev, info(prev))}".` : `You described "${asset.name}".`
      now = stageHelp(stage, info(stage)).meaning
      action = (
        <button type="button" className={makeIt ? '' : 'primary'} disabled={busy} onClick={onRun}>
          {stageVerb(stage, info(stage))}
          <span className="btn-est">{fmtDuration(est)}</span>
        </button>
      )
      break
    }
    case 'running': {
      const job = activeJobFor(jobs, versions)
      const progress = job?.progress ?? 0
      const left = job?.status === 'running' ? est * (1 - progress) : est
      const isVariantRepaint = stage === 'concept' && !!asset.base_asset_id && (job?.kind.includes('refine') || versions.length <= 2)
      happened = isVariantRepaint ? `We are repainting ${baseName ?? 'the original'} with your change${asset.variant_change ? ` ("${asset.variant_change}")` : ''}.` : `${stageGerund(stage)}.`
      now = `${isVariantRepaint ? 'The rest of the steps run as usual. ' : ''}${job?.status === 'queued' ? `Waiting for a free machine, then ${fmtDuration(est)}.` : `${fmtDuration(left).replace(/^about /, 'About ')} left.`} You can leave this page and come back.`
      extra = (
        <div className="guide-progress">
          <div className="progress" aria-label={`progress ${Math.round(progress * 100)} percent`}>
            <div className="progress-bar" style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
          <span className="muted small">{job?.message || 'working'}</span>
        </div>
      )
      action = job ? (
        <button type="button" disabled={busy} onClick={() => onCancel(job.id)}>
          Cancel
        </button>
      ) : null
      break
    }
    case 'error': {
      const failed = versions[versions.length - 1]
      happened = humanError(failed.error)
      now = 'Try again, or change the description.'
      action = (
        <button type="button" className="primary" disabled={busy} onClick={() => onRetry(failed)}>
          Try again
          <span className="btn-est">{fmtDuration(est)}</span>
        </button>
      )
      extra = failed.error ? (
        <Disclosure label="Show details">
          <pre className="raw-error">{failed.error}</pre>
        </Disclosure>
      ) : null
      break
    }
    case 'review': {
      const done = versions.filter((v) => v.status === 'done').length
      happened = stage === 'concept' && asset.base_asset_id ? `${baseName ?? 'The original'} has been repainted with your change.` : done > 1 ? `${done} results are ready.` : 'Your result is ready.'
      now = done > 1 ? 'Look at them below, click the one you like best, then answer "Happy with this?".' : 'Look at it below and answer "Happy with this?".'
      extra = <div className="muted small">The next step unlocks when you keep one.</div>
      break
    }
    case 'selected': {
      const idx = selected ? versions.indexOf(selected) + 1 : 0
      const good = selected ? looksGood(selected.metrics) : null
      happened = `You kept version ${idx}${good === true ? ' and it passed every check' : good === false ? ', though a check needs attention' : ''}.`
      const nextVersions = versionsOf(asset, next!)
      const nextInfo = info(next!)
      if (nextVersions.length) {
        now = `"${stagePlain(next!, nextInfo)}" already has ${nextVersions.length} result${nextVersions.length > 1 ? 's' : ''}. Continue there.`
        action = (
          <Link className={'btn' + (makeIt ? '' : ' primary')} to={`/assets/${asset.id}/${next}`}>
            Go to {stagePlain(next!, nextInfo)}
          </Link>
        )
      } else {
        now = stageHelp(next!, nextInfo).meaning
        const nextEst = stageEstimate(next!, nextInfo, estimates)
        action = (
          <button type="button" className={makeIt ? '' : 'primary'} disabled={busy} onClick={() => onRunNext(next!)}>
            {stageVerb(next!, nextInfo)}
            <span className="btn-est">{fmtDuration(nextEst)}</span>
          </button>
        )
      }
      break
    }
    case 'finished': {
      happened = 'Your asset is ready.'
      now = 'Download the files below or open them in your game engine. Everything is kept, so you can still change any step.'
      const file = selected?.files.fbx ?? selected?.files.glb
      action = file ? (
        <a className="btn primary" href={libraryUrl(file)} download={file.split('/').pop()}>
          Download {selected?.files.fbx ? 'FBX' : 'GLB'}
        </a>
      ) : null
      break
    }
  }

  // An active version that failed while others are fine: say so quietly.
  const activeFailed = state !== 'error' && active && active.status === 'error'

  return (
    <section className={'guide guide-' + state} aria-live="polite">
      <div className="guide-body">
        <div className="guide-kicker">
          Your next step
          <span className="muted"> (step {stepNo} of {STAGES.length}: {stagePlain(stage, info(stage))})</span>
          <Info term={'stage.' + stage} />
        </div>
        <div className="guide-happened">{happened}</div>
        <div className="guide-now">{now}</div>
        {extra}
        {activeFailed && <div className="muted small">The version you are looking at failed: {humanError(active.error)}</div>}
        {apAvailable === false && state !== 'running' && <div className="muted small">Autopilot (one button that runs every step) is not available on this backend yet.</div>}
      </div>
      {(action || makeIt) && (
        <div className={'guide-action' + (makeIt && action ? ' guide-action-col' : '')}>
          {makeIt}
          {action && secondary(action)}
        </div>
      )}
    </section>
  )
}
