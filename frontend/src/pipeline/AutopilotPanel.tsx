// Autopilot: the "Make it" action, the running/paused card that replaces the
// guide while the autopilot owns the asset, and the finishing moment card.
import { Fragment, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { libraryUrl, selectedVersion, STAGES, versionsOf, type Asset, type AutopilotBody, type AutopilotState, type Check, type Job, type StageId, type StageInfo, type StopOn, type Version } from '../api'
import { Info } from '../components/Info'
import { Disclosure, PlainChecks, TrafficDot, type FixRequest } from '../components/ui'
import { autopilotLine, autopilotSecondsLeft, checkFix, checkPassed, fixTooltip, fmtDuration, humanError, stagePlain, STOP_ON_OPTIONS } from '../help'
import Viewer from '../Viewer'
import { versionLook } from '../recipe'
import { finishLineFix, finishLines, unityHowto } from './finish'
import { UnityHandoff } from './UnityHandoff'
import { Deliverables } from './views/ExportView'

export type AutopilotActions = {
  start: (body?: AutopilotBody) => void
  stop: () => void
  fixAndContinue: (fix: FixRequest) => void
  continueAnyway: () => void
  dismiss: () => void
}

export function isAutopilotActive(ap: AutopilotState | null | undefined): boolean {
  return !!ap && (ap.status === 'running' || ap.status === 'paused' || ap.status === 'error')
}

export function autopilotStage(ap: AutopilotState | null | undefined): StageId | null {
  const s = ap?.stage
  return s && STAGES.includes(s as StageId) ? (s as StageId) : null
}

export function firstStageWithoutKept(asset: Asset): StageId | null {
  for (const s of STAGES) if (!asset.selected?.[s] && !versionsOf(asset, s).some((v) => v.selected)) return s
  return null
}

export function isFinished(asset: Asset): boolean {
  const e = selectedVersion(asset, 'export')
  return !!e && e.status === 'done'
}

// Find the version the autopilot is on (any stage).
export function autopilotVersion(asset: Asset, ap: AutopilotState | null | undefined): Version | undefined {
  if (!ap?.version_id) return undefined
  for (const s of STAGES) {
    const v = versionsOf(asset, s).find((x) => x.id === ap.version_id)
    if (v) return v
  }
  return undefined
}

// Seconds the autopilot would take from the first stage without a kept result.
export function makeItEstimate(asset: Asset, stages: StageInfo[] | null, estimates: Record<string, number> | null): number {
  const from = firstStageWithoutKept(asset)
  if (!from) return 0
  return autopilotSecondsLeft(from, 0, 'export', stages, estimates)
}

// "Make it": the one button that runs everything. Primary unless told otherwise.
export function MakeItAction({ asset, stages, estimates, busy, primary, onStart }: { asset: Asset; stages: StageInfo[] | null; estimates: Record<string, number> | null; busy: boolean; primary: boolean; onStart: (body: AutopilotBody) => void }) {
  const [stopOn, setStopOn] = useState<StopOn>('block')
  const est = makeItEstimate(asset, stages, estimates)
  const opt = STOP_ON_OPTIONS.find((o) => o.id === stopOn) ?? STOP_ON_OPTIONS[0]
  return (
    <div className="make-it">
      <button type="button" className={primary ? 'primary' : ''} disabled={busy} onClick={() => onStart({ stop_on: stopOn, until_stage: 'export' })} title="Runs every remaining step and keeps each result that passes its checks">
        Make it
        {est > 0 && <span className="btn-est">{fmtDuration(est)}</span>}
      </button>
      <div className="make-it-line muted small">
        Runs every step and only stops if something needs you.
        <Info term="term.autopilot" />
      </div>
      <label className="make-it-stop small">
        <select value={stopOn} onChange={(e) => setStopOn(e.target.value as StopOn)} aria-label="When the autopilot should stop" title={opt.hint} disabled={busy}>
          {STOP_ON_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <Info term="term.stop_on" />
      </label>
    </div>
  )
}

type CardProps = {
  asset: Asset
  stage: StageId
  autopilot: AutopilotState
  jobs: Job[]
  stages: StageInfo[] | null
  estimates: Record<string, number> | null
  busy: boolean
  actions: AutopilotActions
  // A Fix that opens a tool (2.5) goes here instead of "Fix and continue".
  onOpenTool?: (fix: FixRequest) => void
}

// The card shown instead of "Your next step" while the autopilot runs, is paused or stopped.
export function AutopilotCard({ asset, stage, autopilot: ap, jobs, stages, estimates, busy, actions, onOpenTool }: CardProps) {
  const apStage = autopilotStage(ap)
  const info = (s: StageId) => stages?.find((x) => x.id === s) ?? null
  const elsewhere = apStage && apStage !== stage ? (
    <Link to={`/assets/${asset.id}/${apStage}`} className="small">
      Go to {stagePlain(apStage, info(apStage))}
    </Link>
  ) : null

  if (ap.status === 'running') {
    const job = jobs.find((j) => j.id === ap.job_id)
    const progress = job?.progress ?? 0
    const queued = job?.status === 'queued'
    const until = STAGES.includes(ap.until_stage as StageId) ? (ap.until_stage as StageId) : 'export'
    const left = apStage ? autopilotSecondsLeft(apStage, queued ? 0 : progress, until, stages, estimates) : null
    return (
      <section className="guide guide-autopilot" aria-live="polite">
        <div className="guide-body">
          <div className="guide-kicker">
            Autopilot
            <Info term="term.autopilot" />
          </div>
          <div className="guide-happened">{autopilotLine(apStage, left, { queued })}</div>
          <div className="guide-now">
            It runs every step and only stops if something needs you. You can leave this page and come back. {elsewhere}
          </div>
          <div className="guide-progress">
            <div className="progress" aria-label={`progress ${Math.round(progress * 100)} percent`}>
              <div className="progress-bar" style={{ width: `${Math.round(progress * 100)}%` }} />
            </div>
            <span className="muted small">
              {job?.message || 'working'}
              {ap.stages_done.length ? ` / ${ap.stages_done.length} of ${STAGES.length} steps kept` : ''}
            </span>
          </div>
        </div>
        <div className="guide-action">
          <button type="button" disabled={busy} onClick={actions.stop} title="Stops after the current step; everything made so far is kept">
            Stop
          </button>
        </div>
      </section>
    )
  }

  // Paused or errored: the reason, the failing checks with their fixes, two ways on.
  const version = autopilotVersion(asset, ap)
  const checks = version && Array.isArray(version.metrics.checks) ? (version.metrics.checks as Check[]) : []
  const failing = checks.filter((c) => !checkPassed(c))
  // "Fix and continue" only takes a re-run fix; a fix that opens a tool waits for the person.
  const firstFix = failing.map((c) => checkFix(c)).find((f) => f !== null && !f.ui) ?? null
  const onFix = (f: FixRequest) => (f.ui ? onOpenTool?.(f) : actions.fixAndContinue(f))
  const versionFailed = version?.status === 'error'
  const paused = ap.status === 'paused'
  const idx = version && apStage ? versionsOf(asset, apStage).indexOf(version) + 1 : 0
  return (
    <section className={'guide ' + (paused ? 'guide-paused' : 'guide-error')} aria-live="polite">
      <div className="guide-body">
        <div className="guide-kicker">
          {paused ? 'Autopilot paused' : 'Autopilot stopped'}
          <Info term="term.autopilot" />
          {apStage && (
            <span className="muted">
              {' '}
              (step {STAGES.indexOf(apStage) + 1} of {STAGES.length}: {stagePlain(apStage, info(apStage))}
              {idx ? `, version ${idx}` : ''})
            </span>
          )}
        </div>
        <div className="guide-happened">{ap.reason || (paused ? 'The autopilot is waiting for you.' : 'The autopilot stopped.')}</div>
        {versionFailed && version?.error && (
          <div className="guide-now">
            {humanError(version.error)}
            <Disclosure label="Show details" className="inline-disclosure">
              <pre className="raw-error">{version.error}</pre>
            </Disclosure>
          </div>
        )}
        {failing.length > 0 && (
          <div className="guide-now">
            <PlainChecks checks={checks} ctx={{ platform: asset.platform_target, category: asset.category }} onFix={onFix} busy={busy} compact />
          </div>
        )}
        <div className="guide-now muted small">
          {paused
            ? failing.length
              ? firstFix
                ? '"Fix and continue" applies the first fix and carries on; "Continue anyway" keeps this result as it is.'
                : 'No automatic fix is known for this. Change something below, keep a result, then continue; or continue anyway.'
              : versionFailed
                ? 'Try again runs this step once more and carries on.'
                : 'Continue when you are ready; everything made so far is kept.'
            : 'Start again to continue from where it stopped.'}{' '}
          {elsewhere}
        </div>
      </div>
      <div className="guide-action guide-action-col">
        {paused && !versionFailed && firstFix && (
          <button type="button" className="primary" disabled={busy} onClick={() => actions.fixAndContinue(firstFix)} title={`${firstFix.label}: ${fixTooltip(firstFix, stages)} Then the autopilot carries on.`}>
            Fix and continue
          </button>
        )}
        {paused && !versionFailed && version?.status === 'done' && (
          <button type="button" className={firstFix ? '' : 'primary'} disabled={busy} onClick={actions.continueAnyway} title="Keeps this result and runs the remaining steps">
            Continue anyway
          </button>
        )}
        {paused && (versionFailed || !version) && (
          <button type="button" className="primary" disabled={busy} onClick={() => actions.start()}>
            Try again and continue
          </button>
        )}
        {!paused && (
          <button type="button" className="primary" disabled={busy} onClick={() => actions.start()}>
            Start again
          </button>
        )}
        <button type="button" className="link small" disabled={busy} onClick={actions.dismiss} title="Hide this card and steer by hand">
          Dismiss
        </button>
      </div>
    </section>
  )
}

// Text with `code` spans (the "Using it in Unity" lines).
function withCode(text: string): ReactNode {
  return text.split('`').map((part, i) => (i % 2 ? <code key={i}>{part}</code> : <Fragment key={i}>{part}</Fragment>))
}

// The finishing moment: turntable with the walk clip, what you get, downloads, using it in Unity, what next.
export function FinishCard({ asset, byAutopilot, onMakeVariant, onFix, busy = false, onChanged }: { asset: Asset; byAutopilot: boolean; onMakeVariant: () => void; onFix?: (fix: FixRequest) => void; busy?: boolean; onChanged?: () => void }) {
  const exp = selectedVersion(asset, 'export')
  const lines = finishLines(asset)
  const howto = unityHowto(asset)
  const rig = selectedVersion(asset, 'rig')
  const glb = libraryUrl(exp?.files.anim_glb ?? exp?.files.glb ?? rig?.files.anim_glb ?? rig?.files.glb)
  const clips = Array.isArray(exp?.metrics.clips) ? (exp!.metrics.clips as string[]) : []
  const walk = clips.find((c) => /walk/i.test(c)) ?? clips[0] ?? 'walk'
  // "Look:" where the chosen look changed the result (from prep, else the mesh step).
  const look = lines.some((l) => l.id === 'look') ? null : (versionLook(selectedVersion(asset, 'prep')) ?? versionLook(selectedVersion(asset, 'mesh')))
  return (
    <section className="guide guide-finished finish-card" aria-live="polite">
      <div className="guide-body">
        <div className="guide-kicker">{byAutopilot ? 'Autopilot finished' : 'Finished'}</div>
        <div className="guide-happened finish-title">Your asset is ready</div>
        <div className="guide-now">
          {asset.name} went through all {STAGES.length} steps. Download the files or hand them to your game engine. Everything is kept, so you can still change any step below.
        </div>
        {(lines.length > 0 || look) && (
          <div className="finish-lines">
            <div className="field-label">
              What you get
              <Info term="term.what_you_get" />
            </div>
            <ul>
              {lines.map((l) => {
                const fix = onFix ? finishLineFix(asset, l) : null
                return (
                  <li key={l.id} className={'finish-line ' + l.state}>
                    <TrafficDot state={l.state} title={l.state === 'ok' ? 'checked' : 'needs a look'} />
                    <span className="finish-line-text">
                      {l.text}
                      {l.term && <Info term={'term.' + l.term} />}
                    </span>
                    {fix && (
                      <button type="button" className="small-btn" disabled={busy} onClick={() => onFix!(fix)} title={fixTooltip(fix)}>
                        {fix.label}
                      </button>
                    )}
                  </li>
                )
              })}
              {look && (
                <li className={'finish-line ' + (look.ok ? 'ok' : 'warn')}>
                  <TrafficDot state={look.ok ? 'ok' : 'warn'} title={look.ok ? 'checked' : 'needs a look'} />
                  <span className="finish-line-text">
                    Look: {look.ok && look.look && look.summary ? `${look.look}, ${look.summary}.` : look.sentence}
                    <Info term="term.recipe" />
                    {look.crisp && <Info term="term.texture_crisp" />}
                  </span>
                </li>
              )}
            </ul>
          </div>
        )}
        {glb ? <Viewer url={glb} height={360} turntableDefault defaultClip={walk} className="finish-viewer" /> : <div className="placeholder-box">No 3D model to show.</div>}
        {exp && <Deliverables version={exp} />}
        {exp && <UnityHandoff version={exp} assetName={asset.name} onChanged={onChanged} compact />}
        {howto && (
          <Disclosure label="Using it in Unity" className="unity-howto">
            <div className="muted small">
              {howto.length === 1 ? 'One step' : `${['Two', 'Three', 'Four', 'Five', 'Six'][howto.length - 2] ?? howto.length} steps`} to put it in your game.
              <Info term="term.unity_howto" />
            </div>
            <ol>
              {howto.map((l, i) => (
                <li key={i}>{withCode(l)}</li>
              ))}
            </ol>
          </Disclosure>
        )}
        <div className="row-gap finish-actions">
          <Link className="btn primary" to={`/projects/${asset.project_id}`}>
            Make another
          </Link>
          <button type="button" onClick={onMakeVariant} disabled={!asset.selected?.concept} title="A copy of this asset with one change">
            Make a variant
          </button>
          <Info term="term.variant" />
        </div>
      </div>
    </section>
  )
}
