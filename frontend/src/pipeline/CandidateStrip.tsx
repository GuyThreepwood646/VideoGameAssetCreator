// The strip of candidate cards above the active version: one card per version of
// this step, with its thumbnail (or progress while it runs), checks light and likes.
import { isStopped, libraryUrl, versionFeedback, type Job, type StageId, type Version } from '../api'
import { StatusBadge, TrafficDot } from '../components/ui'
import { useGlbSnapshot } from '../glbSnapshot'
import { firstFailing, looksGood } from '../help'

export function CandidateStrip({
  versions,
  stage,
  jobs,
  activeId,
  selectedId,
  onPick,
}: {
  versions: Version[]
  stage: StageId
  jobs: Job[]
  activeId: string | null
  selectedId: string | null
  onPick: (id: string) => void
}) {
  if (!versions.length) return null
  return (
    <div className="candidates">
      {versions.map((v, i) => (
        <CandidateCard
          key={v.id}
          version={v}
          index={i + 1}
          stage={stage}
          job={jobOfVersion(jobs, v)}
          isActive={activeId === v.id}
          isSelected={selectedId === v.id}
          onClick={() => onPick(v.id)}
        />
      ))}
    </div>
  )
}

// The job that produces a version: its job_id when the backend sent one, else the
// first job that names the version (older backends left job_id null).
export function jobOfVersion(jobs: Job[], v: Version): Job | undefined {
  return (v.job_id ? jobs.find((j) => j.id === v.job_id) : undefined) ?? jobs.find((j) => j.version_id === v.id)
}

export function shortId(id: string) {
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
  const cls = ['candidate', isActive ? 'active' : '', isSelected ? 'selected' : '', version.status === 'rejected' ? 'rejected' : '', isStopped(version) ? 'stopped' : ''].join(' ')
  const good = version.status === 'done' ? looksGood(version.metrics) : null
  const reason = version.status === 'done' ? firstFailing(version.metrics) : null
  const thumbs = version.status === 'done' ? versionFeedback(version)?.thumbs : null
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
        {stage === 'concept' && version.params.source === 'upload' && (
          <span className="chip chip-accent" title="Made from a picture you added, not painted by the app">
            Your picture
          </span>
        )}
        {version.status === 'done' ? (
          <span className={'traffic-line ' + (good === false ? 'bad' : good ? 'ok' : 'none')} title={reason ?? (good ? 'Every check passed' : 'No checks for this version')}>
            <TrafficDot state={good === false ? 'bad' : good ? 'ok' : 'none'} />
            {good === false ? 'needs attention' : good ? 'looks good' : 'no checks'}
          </span>
        ) : (
          <StatusBadge status={version.status} stopped={isStopped(version)} />
        )}
        {(isSelected || thumbs) && (
          <span className="chips">
            {isSelected && <span className="chip chip-ok">kept</span>}
            {thumbs === 'up' && (
              <span className="chip chip-accent" title="You gave this a thumbs up">
                <ThumbIcon down={false} /> liked
              </span>
            )}
            {thumbs === 'down' && (
              <span className="chip chip-muted" title="You gave this a thumbs down">
                <ThumbIcon down /> not liked
              </span>
            )}
          </span>
        )}
      </div>
    </button>
  )
}

export function ThumbIcon({ down }: { down: boolean }) {
  return (
    <svg className={'thumb-icon' + (down ? ' thumb-down' : '')} viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">
      <path
        d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
