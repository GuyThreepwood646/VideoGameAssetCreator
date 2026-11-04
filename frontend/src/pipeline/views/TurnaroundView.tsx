import { useState } from 'react'
import { libraryUrl, TURNAROUND_VIEWS, type Check, type Version } from '../../api'
import { Info } from '../../components/Info'
import { Disclosure, fmtNum, isCheckList, PlainChecks, TrafficDot } from '../../components/ui'
import { metricPlain } from '../../help'

type ViewMetrics = Record<string, unknown> & { passed?: boolean; checks?: Check[] }

const VIEW_NAMES: Record<string, string> = { front: 'Front', left: 'Left side', back: 'Back', right: 'Right side', quarter: 'Three-quarter', top: 'Top' }

export function TurnaroundView({ version, onRegenerateView }: { version: Version; onRegenerateView: (view: string) => void }) {
  const [cutout, setCutout] = useState(false)
  const views = TURNAROUND_VIEWS.filter((v) => v in version.files)
  const perView = (version.metrics.views ?? {}) as Record<string, ViewMetrics>
  const passed = typeof version.metrics.views_passed === 'number' ? version.metrics.views_passed : null
  const total = typeof version.metrics.views_total === 'number' ? version.metrics.views_total : views.length
  return (
    <div className="turnaround-view">
      <div className="view-intro muted small">
        We look at your character from every side so the 3D model gets the back right. If a view looks wrong, redraw just that view.
        <Info term="term.turnaround" />
      </div>
      <div className="viewer-toolbar">
        <button type="button" aria-pressed={!cutout} onClick={() => setCutout(false)}>
          Picture
        </button>
        <button type="button" aria-pressed={cutout} onClick={() => setCutout(true)}>
          Cutout
        </button>
        <Info term="term.cutout" />
        <span className="viewer-status">
          {views.length} views
          {passed !== null ? (
            <>
              , {passed} of {total} match the front
              <Info term="term.consistency_ok" />
            </>
          ) : null}
        </span>
      </div>
      <div className="views-row">
        {views.map((view) => {
          const path = cutout ? (version.files[`${view}_cutout`] ?? version.files[view]) : version.files[view]
          const m = perView[view]
          const failed = m?.passed === false
          const checks = m && isCheckList(m.checks) ? m.checks : null
          const numbers = m ? Object.entries(m).filter(([k, v]) => k !== 'passed' && typeof v === 'number') : []
          return (
            <div key={view} className={'view-cell' + (failed ? ' failed' : '')}>
              <div className={'view-image' + (cutout ? ' checker' : '')}>
                <img src={libraryUrl(path)} alt={view} />
              </div>
              <div className="row-between">
                <strong>{VIEW_NAMES[view] ?? view}</strong>
                {m && m.passed !== undefined && (
                  <span className={'traffic-line ' + (m.passed ? 'ok' : 'bad')}>
                    <TrafficDot state={m.passed ? 'ok' : 'bad'} />
                    {m.passed ? 'matches' : 'needs attention'}
                  </span>
                )}
              </div>
              {checks && checks.length > 0 && <PlainChecks checks={checks} compact />}
              {numbers.length > 0 && (
                <Disclosure label="Details">
                  <div className="chips chips-compact">
                    {numbers.map(([k, v]) => (
                      <span key={k} className="chip" title={k}>
                        <span className="chip-key">{metricPlain(k)}</span> {fmtNum(v as number)}
                      </span>
                    ))}
                  </div>
                </Disclosure>
              )}
              <button type="button" className="small-btn" onClick={() => onRegenerateView(view)}>
                Redraw this view
              </button>
            </div>
          )
        })}
        {views.length === 0 && <div className="muted">No view images in this version.</div>}
      </div>
    </div>
  )
}
