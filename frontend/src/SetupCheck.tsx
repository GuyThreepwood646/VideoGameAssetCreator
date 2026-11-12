// "Is everything ready?" card at the top of the Health page, and the "Setup needs attention" header link. Both read
// GET /api/setup (backend/vgac/preflight.py; the backend keeps a result for a minute, Check again asks afresh).
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, type SetupGroup, type SetupItem, type SetupReport, type SetupState } from './api'
import { Info, Term } from './components/Info'
import { humanError, setupItemHelp, setupStagePlain } from './help'

const GROUP_ORDER: SetupGroup[] = ['tools', 'engines', 'models', 'storage']
const GROUP_LABEL: Record<SetupGroup, string> = { tools: 'Tools', engines: 'ComfyUI instances (graphics engines)', models: 'Models', storage: 'Storage' }
const STATE_WORD: Record<SetupState, string> = { ok: 'Ready', warn: 'Worth a look', fail: 'Needs fixing', unknown: 'Not checked' }
const STATE_DOT: Record<SetupState, string> = { ok: 'ok', warn: 'warn', fail: 'bad', unknown: 'off' }

export function useSetup(intervalMs = 60000) {
  const [report, setReport] = useState<SetupReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  const load = useCallback(async (refresh = false) => {
    setChecking(true)
    try {
      setReport(await api.setup(refresh))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const id = window.setInterval(() => void load(), intervalMs)
    return () => window.clearInterval(id)
  }, [load, intervalMs])

  return { report, error, checking, reload: load }
}

function stepsText(item: SetupItem): string {
  return item.needed_for.length ? `Affects: ${item.needed_for.map(setupStagePlain).join(', ')}` : ''
}

function Problem({ item }: { item: SetupItem }) {
  const steps = stepsText(item)
  return (
    <li className={'setup-problem setup-' + item.state}>
      <div className="setup-problem-head">
        <span className={'dot ' + STATE_DOT[item.state]} />
        <strong>{item.label}</strong>
        <span className="muted small">{STATE_WORD[item.state]}</span>
        <Info entry={setupItemHelp(item)} />
      </div>
      <div>{item.detail}</div>
      {item.fix && <div className="setup-fix">What to do: {item.fix}</div>}
      {steps && <div className="muted small">{steps}</div>}
    </li>
  )
}

function Row({ item }: { item: SetupItem }) {
  return (
    <li className="setup-row">
      <span className={'dot ' + STATE_DOT[item.state]} />
      <span className="setup-row-label">{item.label}</span>
      <span className="muted small setup-row-detail">{item.detail}</span>
      {item.state === 'unknown' ? <Info term="setup_not_checked" /> : <Info entry={setupItemHelp(item)} />}
    </li>
  )
}

export default function SetupCheck() {
  const { report, error, checking, reload } = useSetup()
  const problems = report ? report.items.filter((i) => i.state === 'fail' || i.state === 'warn') : []
  problems.sort((a, b) => (a.state === b.state ? 0 : a.state === 'fail' ? -1 : 1))
  const allGood = report != null && problems.length === 0
  return (
    <div className="card setup-card">
      <h2 className="card-title">
        <Term term="setup_check">Is everything ready?</Term>
      </h2>
      {!report && !error && <div className="muted">Checking...</div>}
      {error && !report && <div className="error">Could not run the setup check: {humanError(error)}</div>}
      {allGood && <div className="setup-ok">Everything the app needs is in place.</div>}
      {problems.length > 0 && (
        <>
          <div className={report!.summary.fails > 0 ? 'warn' : 'muted'}>
            {report!.summary.fails > 0
              ? `${report!.summary.fails} thing${report!.summary.fails === 1 ? '' : 's'} need${report!.summary.fails === 1 ? 's' : ''} fixing`
              : 'Everything needed is in place'}
            {report!.summary.warns > 0 ? `, ${report!.summary.warns} worth a look` : ''}.
          </div>
          <ul className="setup-problems">
            {problems.map((p) => (
              <Problem key={p.id} item={p} />
            ))}
          </ul>
        </>
      )}
      {report && (
        <div className="row-gap">
          <button type="button" className="small-btn" disabled={checking} onClick={() => void reload(true)}>
            {checking ? 'Checking...' : 'Check again'}
          </button>
          <Info term="setup_check_again" />
          {report.summary.unknown > 0 && (
            <span className="muted small">
              {report.summary.unknown} not checked right now <Info term="setup_not_checked" />
            </span>
          )}
        </div>
      )}
      {report && (
        <details className="setup-all">
          <summary>
            Show all checks <Info term="setup_show_all" />
          </summary>
          {GROUP_ORDER.map((g) => {
            const rows = report.items.filter((i) => i.group === g)
            if (!rows.length) return null
            return (
              <div key={g} className="setup-group">
                <h3 className="setup-group-title">
                  {GROUP_LABEL[g]} <Info term={`setup_group_${g}`} />
                </h3>
                <ul className="setup-rows">
                  {rows.map((i) => (
                    <Row key={i.id} item={i} />
                  ))}
                </ul>
              </div>
            )
          })}
        </details>
      )}
    </div>
  )
}

// Header link: shown only when something fails.
export function SetupBadge() {
  const { report } = useSetup(120000)
  if (!report || report.summary.fails === 0) return null
  return (
    <span className="setup-badge">
      <Link to="/health" className="chip chip-bad" title="Open the Health page to see what is missing and how to fix it">
        Setup needs attention
      </Link>
      <Info term="setup_needs_attention" />
    </span>
  )
}
