import { useEffect, useState } from 'react'
import type { Job } from '../api'
import { useEventMode } from '../events'
import { duration, StatusBadge, timeAgo } from './ui'

export function JobLog({ jobs, onCancel }: { jobs: Job[]; onCancel: (id: string) => void }) {
  const mode = useEventMode()
  const [, tick] = useState(0)
  // Re-render every few seconds so durations of running jobs advance.
  useEffect(() => {
    const id = window.setInterval(() => tick((t) => t + 1), 2000)
    return () => window.clearInterval(id)
  }, [])

  const sorted = jobs.slice().sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
  const active = sorted.filter((j) => j.status === 'queued' || j.status === 'running').length

  return (
    <div className="joblog">
      <div className="joblog-head">
        <span className="field-label">Job log</span>
        <span className="muted small">
          {active ? `${active} active, ` : ''}
          {mode === 'ws' ? 'live' : mode === 'poll' ? 'polling 3 s' : 'connecting'}
        </span>
      </div>
      <ul className="joblist">
        {sorted.map((j) => (
          <li key={j.id} className={'jobrow job-' + j.status}>
            <div className="row-between">
              <span className="job-kind">
                {j.kind} <span className="muted small">on {j.instance}</span>
              </span>
              <StatusBadge status={j.status} />
            </div>
            {(j.status === 'running' || j.status === 'queued') && (
              <div className="progress" aria-label={`progress ${Math.round(j.progress * 100)} percent`}>
                <div className="progress-bar" style={{ width: `${Math.round(j.progress * 100)}%` }} />
              </div>
            )}
            <div className="job-msg small">
              {j.message}
              {j.error ? <span className="error"> {j.error}</span> : null}
            </div>
            <div className="row-between small muted">
              <span>
                {timeAgo(j.created_at)}
                {j.started_at ? ` / ${duration(j.started_at, j.finished_at)}` : ''}
              </span>
              {(j.status === 'queued' || j.status === 'running') && (
                <button type="button" className="link" onClick={() => onCancel(j.id)}>
                  cancel
                </button>
              )}
            </div>
          </li>
        ))}
        {sorted.length === 0 && <li className="muted small">No jobs for this asset yet.</li>}
      </ul>
    </div>
  )
}
