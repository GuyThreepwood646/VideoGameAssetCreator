// The strip of candidate cards above the active version: one card per version of
// this step, with its thumbnail (or progress while it runs), checks light and likes.
import { isSetAside, isStopped, libraryUrl, versionFeedback, type Job, type StageId, type Version } from '../api'
import { StatusBadge, TrafficDot } from '../components/ui'
import { useGlbSnapshot } from '../glbSnapshot'
import { useState } from 'react'
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
  const [compare, setCompare] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  if (!versions.length) return null
  const done = versions.filter((v) => v.status === 'done')
  // In compare mode a click picks a version to compare (two at most) instead of opening it.
  const click = (id: string) => {
    if (!compare) return onPick(id)
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]))
  }
  const a = versions.find((v) => v.id === picked[0])
  const b = versions.find((v) => v.id === picked[1])
  return (
    <>
      {done.length > 1 && (
        <div className="row-gap small compare-toggle">
          <button type="button" className="small-btn" aria-pressed={compare} onClick={() => { setCompare((c) => !c); setPicked([]) }} title="Pick two versions to see them side by side">
            Compare
          </button>
          {compare && <span className="muted">Pick two versions below.</span>}
        </div>
      )}
      <div className="candidates">
        {versions.map((v, i) => (
          <CandidateCard
            key={v.id}
            version={v}
            index={i + 1}
            stage={stage}
            job={jobOfVersion(jobs, v)}
            isActive={compare ? picked.includes(v.id) : activeId === v.id}
            isSelected={selectedId === v.id}
            onClick={() => click(v.id)}
          />
        ))}
      </div>
      {compare && a && b && <ComparePane a={a} b={b} aNo={versions.indexOf(a) + 1} bNo={versions.indexOf(b) + 1} stage={stage} />}
    </>
  )
}

function versionPicture(stage: StageId, v: Version): string | undefined {
  const path = stage === 'concept' ? (v.files.cutout ?? v.files.image) : stage === 'turnaround' ? (v.files.front_cutout ?? v.files.front ?? Object.values(v.files)[0]) : v.files.preview
  return libraryUrl(path)
}

function ComparePic({ stage, v }: { stage: StageId; v: Version }) {
  const is3d = stage === 'mesh' || stage === 'prep' || stage === 'rig' || stage === 'export'
  const glbUrl = is3d && !v.files.preview ? libraryUrl(v.files.glb) : undefined
  const { src: snap } = useGlbSnapshot(glbUrl)
  return versionPicture(stage, v) ?? snap ? <img src={(versionPicture(stage, v) ?? snap) as string} alt="" /> : <span className="muted small">no picture</span>
}

// Two versions side by side, or one over the other with a slider (UI-25).
function ComparePane({ a, b, aNo, bNo, stage }: { a: Version; b: Version; aNo: number; bNo: number; stage: StageId }) {
  const [mode, setMode] = useState<'side' | 'slider'>('side')
  const [cut, setCut] = useState(50)
  return (
    <div className="compare-pane" aria-label={`Comparing version ${aNo} and version ${bNo}`}>
      <div className="row-gap small">
        <strong>
          Version {aNo} and version {bNo}
        </strong>
        <span className="segmented">
          <button type="button" aria-pressed={mode === 'side'} onClick={() => setMode('side')}>
            Side by side
          </button>
          <button type="button" aria-pressed={mode === 'slider'} onClick={() => setMode('slider')}>
            Slider
          </button>
        </span>
      </div>
      {mode === 'side' ? (
        <div className="compare-side">
          {[a, b].map((v, i) => (
            <figure key={v.id} className="checker">
              <ComparePic stage={stage} v={v} />
              <figcaption className="small">Version {i === 0 ? aNo : bNo}</figcaption>
            </figure>
          ))}
        </div>
      ) : (
        <div className="compare-slider">
          <div className="compare-stack checker">
            <ComparePic stage={stage} v={b} />
            <div className="compare-over" style={{ clipPath: `inset(0 ${100 - cut}% 0 0)` }}>
              <ComparePic stage={stage} v={a} />
            </div>
          </div>
          <input type="range" min={0} max={100} value={cut} onChange={(e) => setCut(Number(e.target.value))} aria-label={`Show version ${aNo} on the left and version ${bNo} on the right`} />
          <div className="row-between small">
            <span>Version {aNo}</span>
            <span>Version {bNo}</span>
          </div>
        </div>
      )}
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
          <strong title={`Technical id: ${shortId(version.id)}`}>Version {index}</strong>
        </span>
        {stage === 'concept' && version.params.source === 'upload' && (
          <span className="chip chip-accent" title="Made from a picture you added, not painted by the app">
            Your picture
          </span>
        )}
        {stage === 'mesh' && version.params.source === 'upload' && (
          <span className="chip chip-accent" title="A 3D model you added, not built by the app">
            Your model
          </span>
        )}
        {version.params.source === 'from_model' && (
          <span className="chip chip-accent" title="Pictures the app took of the 3D model you added">
            From your model
          </span>
        )}
        {version.status === 'done' ? (
          good === null || good === undefined ? null : (
            <span className={'traffic-line ' + (good === false ? 'bad' : 'ok')} title={reason ?? 'Every check passed'}>
              <TrafficDot state={good === false ? 'bad' : 'ok'} />
              {good === false ? 'Needs a look' : 'Looks good'}
            </span>
          )
        ) : (
          <StatusBadge status={version.status} stopped={isStopped(version)} setAside={isSetAside(version)} />
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
