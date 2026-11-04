// "Your next step": one card that says what just happened, what to do now and the
// single primary action with a time estimate. Exactly one primary button exists on
// the page: it lives here, except in the review state where it is the "Yes, keep it"
// answer next to the picture.
import { Link } from 'react-router'
import { libraryUrl, previousStage, STAGES, versionsOf, type Asset, type Job, type StageId, type StageInfo, type Version } from '../api'
import { Info } from '../components/Info'
import { Disclosure } from '../components/ui'
import { fmtDuration, humanError, looksGood, stageEstimate, stageGerund, stageHelp, stagePlain, stageVerb } from '../help'

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
  onRun: () => void
  onRunNext: (next: StageId) => void
  onRetry: (failed: Version) => void
  onCancel: (jobId: string) => void
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
  const prev = previousStage(stage)
  const prevSelected = prev ? asset.selected?.[prev] || versionsOf(asset, prev).some((v) => v.selected) : true
  if (versions.some((v) => v.status === 'running' || v.status === 'queued') || activeJobFor(jobs, versions)) return 'running'
  if (selected && selected.status === 'done') return stage === 'export' ? 'finished' : 'selected'
  if (versions.some((v) => v.status === 'done')) return 'review'
  const last = versions[versions.length - 1]
  if (last && last.status === 'error') return 'error'
  if (!prevSelected) return 'blocked'
  return 'empty'
}

export function GuidePanel({ asset, stage, jobs, stages, estimates, active, selected, busy, baseName, onRun, onRunNext, onRetry, onCancel }: Props) {
  const info = (s: StageId) => stages?.find((x) => x.id === s) ?? null
  const versions = versionsOf(asset, stage)
  const prev = previousStage(stage)
  const next = nextStage(stage)
  const state = guideState(asset, stage, jobs, selected)
  const est = stageEstimate(stage, info(stage), estimates)
  const stepNo = STAGES.indexOf(stage) + 1

  let happened: string
  let now: string
  let action: React.ReactNode = null
  let extra: React.ReactNode = null

  switch (state) {
    case 'blocked': {
      happened = 'Nothing here yet.'
      now = `First ${stageVerb(prev!, info(prev!)).toLowerCase()} and keep one of the results.`
      action = (
        <Link className="btn primary" to={`/assets/${asset.id}/${prev}`}>
          Go to {stagePlain(prev!, info(prev!))}
        </Link>
      )
      break
    }
    case 'empty': {
      happened = prev ? `You kept a result from "${stagePlain(prev, info(prev))}".` : `You described "${asset.name}".`
      now = stageHelp(stage, info(stage)).meaning
      action = (
        <button type="button" className="primary" disabled={busy} onClick={onRun}>
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
          <Link className="btn primary" to={`/assets/${asset.id}/${next}`}>
            Go to {stagePlain(next!, nextInfo)}
          </Link>
        )
      } else {
        now = stageHelp(next!, nextInfo).meaning
        const nextEst = stageEstimate(next!, nextInfo, estimates)
        action = (
          <button type="button" className="primary" disabled={busy} onClick={() => onRunNext(next!)}>
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
      </div>
      {action && <div className="guide-action">{action}</div>}
    </section>
  )
}
