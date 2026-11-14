// "Is everything ready?" card at the top of the Health page, and the "Setup needs attention" header link. Both read
// GET /api/setup (backend/vgac/preflight.py; the backend keeps a result for a minute, Check again asks afresh).
import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { api, type FirstRun, type SetupGroup, type SetupItem, type SetupReport, type SetupState } from './api'
import { Info, Term } from './components/Info'
import PackInstall from './components/PackInstall'
import { humanError, setupItemHelp, setupStagePlain } from './help'

const GROUP_ORDER: SetupGroup[] = ['tools', 'engines', 'models', 'storage']
const GROUP_LABEL: Record<SetupGroup, string> = { tools: 'Tools', engines: 'ComfyUI instances (graphics engines)', models: 'Models', storage: 'Storage' }
const STATE_WORD: Record<SetupState, string> = { ok: 'Ready', warn: 'Worth a look', fail: 'Needs fixing', unknown: 'Not checked', optional: 'Optional' }
const STATE_DOT: Record<SetupState, string> = { ok: 'ok', warn: 'warn', fail: 'bad', unknown: 'off', optional: 'off' }

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

// What each setting asks for, in the words of the box (the backend checks the value and answers in plain words).
const FIELD_HELP: Record<string, { label: string; placeholder: string }> = {
  blender_exe: { label: 'Where is blender.exe?', placeholder: 'The full path of blender.exe, for example C:\\Tools\\Blender\\blender.exe' },
  unity_exe: { label: 'Where is Unity.exe?', placeholder: 'The full path of Unity.exe' },
  unity_project: { label: 'Which folder is the Unity project?', placeholder: 'The full path of a Unity project folder' },
}

function fieldHelp(setting: string): { label: string; placeholder: string } {
  if (FIELD_HELP[setting]) return FIELD_HELP[setting]
  if (setting.endsWith('.engine.comfy_dir')) return { label: 'Where is ComfyUI?', placeholder: 'The full path of the folder that holds ComfyUI and its Python' }
  return { label: 'Where is it?', placeholder: 'The full path' }
}

// One setting, given one at a time: the backend checks it, saves it and says what changed and whether a restart is needed.
function SettingField({ setting, onSaved }: { setting: string; onSaved: () => void }) {
  const help = fieldHelp(setting)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<{ ok: boolean; text: string } | null>(null)
  const save = async () => {
    setBusy(true)
    try {
      const r = await api.setSetting(setting, value.trim())
      setAnswer({ ok: true, text: r.message })
      setValue('')
      onSaved()
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="setup-field">
      <label className="field-label">{help.label}</label>
      <div className="row-gap">
        <input
          type="text"
          className="setup-path"
          value={value}
          placeholder={help.placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && value.trim() && !busy && void save()}
        />
        <button type="button" className="small-btn" disabled={busy || !value.trim()} onClick={() => void save()}>
          {busy ? 'Checking...' : 'Use this'}
        </button>
      </div>
      {answer && <div className={answer.ok ? 'setup-ok small' : 'error small'}>{answer.text}</div>}
    </div>
  )
}

function Problem({ item, onSaved }: { item: SetupItem; onSaved?: () => void }) {
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
      {item.setting && onSaved && <SettingField setting={item.setting} onSaved={onSaved} />}
    </li>
  )
}

// The first thing a new machine meets: what is missing as a whole, which steps cannot run yet, and a restart note.
function FirstRunBanner({ firstRun }: { firstRun: FirstRun }) {
  const blocked = firstRun.steps.filter((s) => !s.can_run)
  if (firstRun.state === 'ready' && firstRun.restart_needed.length === 0) return null
  return (
    <div className="setup-firstrun">
      {firstRun.state === 'settings_problem' && (
        <div className="error">
          This computer&apos;s settings file could not be used, so the app started with nothing set up and no step will run. {firstRun.settings_problem}
        </div>
      )}
      {firstRun.state === 'not_set_up' && (
        <div className="warn">
          Nothing is set up yet, so no step can run. The app needs ComfyUI (it runs the picture and 3D models) and Blender (it cleans up, rigs and hands off). Give where each one is below.
        </div>
      )}
      {firstRun.state === 'partly_set_up' && blocked.length > 0 && (
        <div className="warn">Some steps cannot run yet: {blocked.map((s) => s.plain).join(', ')}.</div>
      )}
      {firstRun.restart_needed.length > 0 && (
        <div className="muted">Saved. Close the app and open it again to use {firstRun.restart_needed.length === 1 ? 'one setting' : `${firstRun.restart_needed.length} settings`} you changed.</div>
      )}
      {firstRun.unchecked > 0 && firstRun.state === 'not_set_up' && (
        <div className="muted small">The model files are checked once a ComfyUI is running.</div>
      )}
    </div>
  )
}

function Row({ item, onManage }: { item: SetupItem; onManage?: (kit: string) => void }) {
  return (
    <li className="setup-row">
      <span className={'dot ' + STATE_DOT[item.state]} />
      <span className="setup-row-label">{item.label}</span>
      <span className="muted small setup-row-detail">{item.detail}</span>
      {item.kit && onManage && (
        <button type="button" className="link small" onClick={() => onManage(item.kit!)}>
          {item.state === 'ok' ? 'Manage' : 'Get it'}
        </button>
      )}
      {item.state === 'unknown' ? <Info term="setup_not_checked" /> : <Info entry={setupItemHelp(item)} />}
    </li>
  )
}

export default function SetupCheck() {
  const { report, error, checking, reload } = useSetup()
  const problems = report ? report.items.filter((i) => i.state === 'fail' || i.state === 'warn') : []
  problems.sort((a, b) => (a.state === b.state ? 0 : a.state === 'fail' ? -1 : 1))
  const allGood = report != null && problems.length === 0
  // Optional extras (the hair library): offered, never a fault. The button opens the guided panel (PackInstall).
  const extras = report ? report.items.filter((i) => i.state === 'optional' && i.offer) : []
  const [openKit, setOpenKit] = useState<string | null>(null)
  const hash = useLocation().hash
  useEffect(() => {
    if (hash.startsWith('#get-')) setOpenKit(hash.slice(5))
  }, [hash])
  useEffect(() => {
    if (openKit && hash === '#get-' + openKit) document.getElementById('get-' + openKit)?.scrollIntoView({ block: 'center' })
  }, [openKit, hash, report])
  const panel = openKit ? <PackInstall kit={openKit} onChanged={() => void reload(true)} onClose={() => setOpenKit(null)} /> : null
  return (
    <div className="card setup-card">
      <h2 className="card-title">
        <Term term="setup_check">Is everything ready?</Term>
      </h2>
      {!report && !error && <div className="muted">Checking...</div>}
      {error && !report && <div className="error">Could not run the setup check: {humanError(error)}</div>}
      {report?.first_run && <FirstRunBanner firstRun={report.first_run} />}
      {allGood && report?.first_run?.state !== 'settings_problem' && <div className="setup-ok">Everything the app needs is in place.</div>}
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
              <Problem key={p.id} item={p} onSaved={() => void reload(true)} />
            ))}
          </ul>
        </>
      )}
      {extras.length > 0 && (
        <div className="setup-extras">
          <div className="field-label">Optional extras</div>
          <ul className="setup-rows">
            {extras.map((i) => (
              <li key={i.id} className="setup-row">
                <span className="dot off" />
                <span className="setup-row-label">{i.label}</span>
                <span className="muted small setup-row-detail">{i.detail}</span>
                <button type="button" className="small-btn" onClick={() => setOpenKit(i.offer!.kit)}>
                  {i.offer!.button}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {panel}
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
                    <Row key={i.id} item={i} onManage={setOpenKit} />
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
