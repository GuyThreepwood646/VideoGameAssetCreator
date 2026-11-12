// Projects page (docs/PROJECTS-PAGE-SPEC.md): a grid of project cards with picture covers, New project always
// at the top (a dialog), a welcome panel on the first visit, and the look and settings in a side drawer.
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api, libraryUrl, PLATFORMS, selectedVersion, STAGES, stageState, STYLE_MODELS, versionsOf, type AssetSummary, type LookPreset, type PlatformId, type Project, type StyleLock, type StyleModel } from '../api'
import { Info } from '../components/Info'
import { PresetRow } from '../components/PresetRow'
import { ChipInput, Disclosure, ErrorText, Field, SubmitTextarea, timeAgo, useAsync } from '../components/ui'
import { modelLabel, platformConsequence, platformPlain, stagePlain } from '../help'
import { lockFromPreset, lockMatchesPreset, presetById, usePresets } from '../presets'
import { MatchPicture } from './MatchPicture'

type SortKey = 'recent' | 'name' | 'newest'
const SORT_KEY = 'vgac.projects.sort'
const SORTS: { id: SortKey; label: string }[] = [
  { id: 'recent', label: 'Recent activity' },
  { id: 'name', label: 'Name' },
  { id: 'newest', label: 'Newest' },
]
// Search appears once the list is long enough to need it.
const SEARCH_FROM = 6

function readSort(): SortKey {
  const s = localStorage.getItem(SORT_KEY)
  return s === 'name' || s === 'newest' ? s : 'recent'
}

export default function ProjectsPage() {
  const { data: projects, error, loading, reload } = useAsync(() => api.listProjects(), [])
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const editId = params.get('edit')
  const editing = projects?.find((p) => p.id === editId) ?? null
  const { presets } = usePresets()
  const [creating, setCreating] = useState(false)
  const [query, setQuery] = useState('')
  const [sort, setSortState] = useState<SortKey>(readSort)
  const setSort = (s: SortKey) => {
    setSortState(s)
    localStorage.setItem(SORT_KEY, s)
  }

  const shown = useMemo(() => {
    if (!projects) return []
    const look = (p: Project) => presetById(presets, p.style_lock.preset)?.label ?? 'Custom look'
    const q = query.trim().toLowerCase()
    const list = q ? projects.filter((p) => [p.name, look(p), platformPlain(p.platform_target)].some((t) => t.toLowerCase().includes(q))) : [...projects]
    const activity = (p: Project) => p.last_activity ?? p.created_at
    if (sort === 'name') list.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
    else if (sort === 'newest') list.sort((a, b) => b.created_at.localeCompare(a.created_at))
    else list.sort((a, b) => activity(b).localeCompare(activity(a)))
    return list.map((p) => ({ project: p, look: look(p) }))
  }, [projects, query, sort, presets])

  const open = (p: Project) => navigate(`/projects/${p.id}`)
  const firstVisit = !!projects && projects.length === 0
  const assetTotal = projects?.reduce((n, p) => n + (p.asset_count ?? 0), 0) ?? 0
  const hasCounts = !!projects?.some((p) => p.asset_count !== undefined)
  const searching = query.trim() !== ''

  return (
    <div className="projects-page">
      <header className="projects-head">
        <div className="col-tight">
          <h1 className="page-title">
            Projects
            <Info term="term.project" />
          </h1>
          {projects && projects.length > 0 && (
            <div className="muted small">
              {projects.length} project{projects.length === 1 ? '' : 's'}
              {hasCounts ? `, ${assetTotal} asset${assetTotal === 1 ? '' : 's'}` : ''}
            </div>
          )}
        </div>
        <span className="grow" />
        {projects && projects.length >= SEARCH_FROM && (
          <input
            type="search"
            className="projects-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
            placeholder="Search projects"
            aria-label="Search projects"
          />
        )}
        {projects && projects.length > 1 && (
          <select className="projects-sort" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort projects">
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        )}
        {projects && !firstVisit && (
          <button type="button" className="primary btn-lg" onClick={() => setCreating(true)}>
            <PlusIcon /> New project
          </button>
        )}
      </header>

      {error ? (
        <div className="row-gap">
          <ErrorText error={error} />
          <button type="button" onClick={reload}>
            Try again
          </button>
        </div>
      ) : null}

      {loading && !projects && !error && (
        <ul className="project-grid" aria-busy="true" aria-label="Loading projects">
          {[0, 1, 2].map((i) => (
            <li key={i} className="pcard pcard-skeleton" />
          ))}
        </ul>
      )}

      {firstVisit && <Welcome onCreated={open} />}

      {projects && !searching && <JumpBackIn projects={projects} />}

      {projects && projects.length > 0 &&
        (shown.length === 0 ? (
          <div className="projects-nomatch">
            <span>No projects match "{query.trim()}".</span>
            <button type="button" className="link" onClick={() => setQuery('')}>
              Clear search
            </button>
          </div>
        ) : (
          <ul className="project-grid">
            {!searching && (
              <li>
                <button type="button" className="pcard pcard-new" onClick={() => setCreating(true)}>
                  <span className="pcard-new-icon">
                    <PlusIcon size={22} />
                  </span>
                  <span className="pcard-new-label">New project</span>
                  <span className="muted small">For a new game or a new art style</span>
                </button>
              </li>
            )}
            {shown.map(({ project, look }) => (
              <li key={project.id}>
                <ProjectCard project={project} look={look} active={project.id === editId} onSettings={() => setParams({ edit: project.id })} />
              </li>
            ))}
          </ul>
        ))}

      {creating && (
        <Modal title="New project" onClose={() => setCreating(false)}>
          <CreateProjectForm
            onCreated={(p) => {
              setCreating(false)
              open(p)
            }}
          />
        </Modal>
      )}

      {editing && (
        <Drawer title="Look and settings" onClose={() => setParams({})}>
          <StyleLockEditor
            key={editing.id}
            project={editing}
            onSaved={reload}
            onDeleted={() => {
              setParams({})
              reload()
            }}
          />
        </Drawer>
      )}
    </div>
  )
}

// The most recently active project's latest assets, each with how far it has got and its next step.
function lastTouched(a: AssetSummary): string {
  let t = a.created_at
  for (const s of STAGES) for (const v of versionsOf(a, s)) if (v.created_at > t) t = v.created_at
  return t
}

function JumpBackIn({ projects }: { projects: Project[] }) {
  const recent = useMemo(() => {
    const withAssets = projects.filter((p) => (p.asset_count ?? 0) > 0)
    return withAssets.sort((a, b) => (b.last_activity ?? b.created_at).localeCompare(a.last_activity ?? a.created_at))[0] ?? null
  }, [projects])
  const { data: assets } = useAsync(() => (recent ? api.listAssets(recent.id) : Promise.resolve([] as AssetSummary[])), [recent?.id])
  if (!recent || !assets || assets.length === 0) return null
  const latest = [...assets].sort((a, b) => lastTouched(b).localeCompare(lastTouched(a))).slice(0, 4)
  return (
    <section className="jump" aria-labelledby="jump-title">
      <div className="row-between">
        <h2 id="jump-title" className="jump-title">
          Jump back in <span className="muted">in {recent.name}</span>
        </h2>
        <Link to={`/projects/${recent.id}`} className="small">
          All {recent.asset_count} assets
        </Link>
      </div>
      <ul className="jump-row">
        {latest.map((a) => {
          const concept = selectedVersion(a, 'concept')
          const thumb = libraryUrl(concept?.files.cutout ?? concept?.files.image)
          const states = STAGES.map((s) => stageState(a, s))
          const working = states.indexOf('running')
          const failed = states.indexOf('error')
          const next = states.indexOf('none')
          const status =
            working >= 0
              ? { cls: 'jump-working', text: `Working: ${stagePlain(STAGES[working])}` }
              : failed >= 0
                ? { cls: 'jump-error', text: `Needs a look: ${stagePlain(STAGES[failed])}` }
                : next < 0
                  ? { cls: 'jump-done', text: 'Ready for your game' }
                  : { cls: '', text: `Next: ${stagePlain(STAGES[next])}` }
          const done = states.filter((s) => s === 'done').length
          return (
            <li key={a.id}>
              <Link to={`/assets/${a.id}`} className="jump-card">
                <span className="jump-thumb checker">{thumb ? <img src={thumb} alt="" loading="lazy" /> : null}</span>
                <span className="jump-text">
                  <span className="jump-name">{a.name}</span>
                  <span className={'small ' + (status.cls || 'muted')}>{status.text}</span>
                  <span className="jump-bar" role="img" aria-label={`${done} of ${STAGES.length} steps done`}>
                    <span style={{ width: `${(done / STAGES.length) * 100}%` }} />
                  </span>
                </span>
              </Link>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function PlusIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" className="icon">
      <path d="M8 2.5v11M2.5 8h11" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" fill="none" />
    </svg>
  )
}

function GearIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" className="icon">
      <path
        d="M6.9 1.5h2.2l.35 1.8a5 5 0 0 1 1.3.75l1.73-.6 1.1 1.9-1.38 1.2a5 5 0 0 1 0 1.5l1.38 1.2-1.1 1.9-1.73-.6a5 5 0 0 1-1.3.75l-.35 1.8H6.9l-.35-1.8a5 5 0 0 1-1.3-.75l-1.73.6-1.1-1.9 1.38-1.2a5 5 0 0 1 0-1.5L2.42 5.35l1.1-1.9 1.73.6a5 5 0 0 1 1.3-.75z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
        fill="none"
      />
      <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.2" fill="none" />
    </svg>
  )
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? '')).toUpperCase() || '?'
}

// The newest assets' pictures; with none yet, the look's palette as stripes with the project's initials.
function ProjectCover({ project }: { project: Project }) {
  const covers = (project.covers ?? []).map((c) => libraryUrl(c)).filter((c): c is string => !!c)
  if (covers.length > 0) {
    return (
      <div className={'pcover checker pcover-n' + covers.length}>
        {covers.map((src, i) => (
          <img key={i} src={src} alt="" loading="lazy" />
        ))}
      </div>
    )
  }
  const pal = project.style_lock.palette.filter((c) => /^#[0-9a-f]{3,8}$/i.test(c))
  const colours = pal.length ? pal : ['#2f333b', '#3a3e46', '#24272d']
  const step = 100 / colours.length
  const stripes = colours.map((c, i) => `${c} ${(i * step).toFixed(1)}% ${((i + 1) * step).toFixed(1)}%`).join(', ')
  return (
    <div className="pcover pcover-palette" style={{ backgroundImage: `linear-gradient(115deg, ${stripes})` }}>
      <span className="pcover-initials">{initials(project.name)}</span>
    </div>
  )
}

function ProjectCard({ project: p, look, active, onSettings }: { project: Project; look: string; active: boolean; onSettings: () => void }) {
  const count = p.asset_count
  const summary =
    count === undefined
      ? `Created ${timeAgo(p.created_at)}`
      : count === 0
        ? `No assets yet, created ${timeAgo(p.created_at)}`
        : `${count} asset${count === 1 ? '' : 's'}, active ${timeAgo(p.last_activity ?? p.created_at)}`
  return (
    <article className={'pcard' + (active ? ' active' : '')}>
      <Link className="pcard-link" to={`/projects/${p.id}`} aria-label={`Open ${p.name}: ${look}, ${summary}`}>
        <ProjectCover project={p} />
        <span className="pcard-body">
          <span className="pcard-name">{p.name}</span>
          <span className="muted small">
            {look} / {platformPlain(p.platform_target).split(':')[0]}
          </span>
          <span className="pcard-foot small">
            <span className="muted">{summary}</span>
            <span className="swatches" aria-hidden="true">
              {p.style_lock.palette.slice(0, 6).map((c, i) => (
                <span key={i} className="swatch" style={{ background: c }} />
              ))}
            </span>
          </span>
        </span>
      </Link>
      <button type="button" className="pcard-settings" onClick={onSettings} aria-label={`Look and settings for ${p.name}`} title="Look and settings">
        <GearIcon />
      </button>
    </article>
  )
}

// Focus moves into the panel on open and back to whatever opened it on close; Esc closes.
function useDialog(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const target = ref.current?.querySelector<HTMLElement>('[data-autofocus], input, select, textarea') ?? ref.current
    target?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current()
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener && document.contains(opener)) opener.focus()
    }
  }, [])
  return ref
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useDialog(onClose)
  return (
    <div className="overlay" onClick={onClose}>
      <div ref={ref} tabIndex={-1} className="modal-card" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={(e) => e.stopPropagation()}>
        <div className="row-between">
          <h2 id="modal-title" className="overlay-title">
            {title}
          </h2>
          <CloseButton onClick={onClose} />
        </div>
        {children}
      </div>
    </div>
  )
}

function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useDialog(onClose)
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <div ref={ref} tabIndex={-1} className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h2 id="drawer-title" className="overlay-title">
            {title}
          </h2>
          <CloseButton onClick={onClose} />
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </div>
  )
}

function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="icon-btn" onClick={onClick} aria-label="Close" title="Close (Esc)">
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    </button>
  )
}

function Welcome({ onCreated }: { onCreated: (p: Project) => void }) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <div className="welcome-main">
        <div className="col-tight">
          <span className="welcome-kicker">Welcome</span>
          <h2 id="welcome-title" className="welcome-title">
            Start your first project
          </h2>
          <p className="muted welcome-lead">A project is one game, or one art style. Give it a name and pick a look; every character, creature and prop you make inside it will match.</p>
        </div>
        <CreateProjectForm onCreated={onCreated} roomy />
      </div>
      <aside className="welcome-side">
        <h3 className="welcome-side-title">How it works</h3>
        <ol className="overlay-steps">
          <li>
            <span className="overlay-num">1</span>
            <div>
              <strong>Make a project.</strong>
              <div className="muted">Name it and choose the look your game uses.</div>
            </div>
          </li>
          <li>
            <span className="overlay-num">2</span>
            <div>
              <strong>Describe an asset.</strong>
              <div className="muted">One sentence is enough: what it is, two or three details, the colours.</div>
            </div>
          </li>
          <li>
            <span className="overlay-num">3</span>
            <div>
              <strong>Keep it or ask for changes.</strong>
              <div className="muted">We build it step by step, from picture to game-ready model, and you approve each step.</div>
            </div>
          </li>
        </ol>
        <Disclosure label="Three words to know">
          <ThreeWords />
        </Disclosure>
      </aside>
    </section>
  )
}

function ThreeWords() {
  return (
    <div className="col-tight three-words">
      <p>
        <strong>Project</strong>
        <Info term="term.project" /> is one game, or one visually coherent set. It owns the look every asset shares and the default platform. Make a second project for a different game or art style, not for a second character.
      </p>
      <p>
        <strong>Asset</strong>
        <Info term="term.asset" /> is one distinct thing: a character, a creature, a prop, a tileable material. Its category decides the pose, the rig and the budgets.
      </p>
      <p>
        <strong>Version</strong>
        <Info term="term.version" /> is one attempt at one step of one asset. Trying again or changing something makes a new version, so nothing is lost by trying.
      </p>
    </div>
  )
}

function PlatformOptions() {
  return (
    <>
      {PLATFORMS.map((id) => (
        <option key={id} value={id}>
          {platformPlain(id)}
        </option>
      ))}
    </>
  )
}

function CreateProjectForm({ onCreated, roomy = false }: { onCreated: (p: Project) => void; roomy?: boolean }) {
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState<PlatformId>('pc_mid')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const { presets, unavailable } = usePresets()
  const [presetId, setPresetId] = useState<string | null>(null)
  // A new project starts with a look chosen: the first preset unless the person picks another.
  const chosen: LookPreset | null = presetById(presets, presetId) ?? presets?.[0] ?? null

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    setError(null)
    try {
      const p = await api.createProject({ name: name.trim(), platform_target: platform, ...(chosen ? { style_lock: lockFromPreset(chosen) } : {}) })
      onCreated(p)
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  return (
    <form className={'create-form' + (roomy ? ' roomy' : '')} onSubmit={submit}>
      <div className="create-fields">
        <Field label="Name">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Hollow Keep" aria-label="Project name" data-autofocus autoFocus maxLength={80} />
        </Field>
        <Field label="Platform" term={'platform.' + platform}>
          <select value={platform} onChange={(e) => setPlatform(e.target.value as PlatformId)} aria-label="Default platform target">
            <PlatformOptions />
          </select>
        </Field>
      </div>
      <div className="muted small">{platformConsequence(platform)}</div>
      {presets && presets.length > 0 && (
        <div className="col-tight">
          <div className="field-label">
            Look
            <Info term="term.look" />
            <span className="muted small"> (you can change it later)</span>
          </div>
          <PresetRow presets={presets} currentId={chosen?.id ?? null} busy={busy} compact={!roomy} onPick={(p) => setPresetId(p.id)} />
          {chosen && !roomy && (
            <div className="muted small">
              <strong>{chosen.label}</strong>: {chosen.description}
            </div>
          )}
        </div>
      )}
      {unavailable && <div className="muted small">Ready-made looks need a newer backend; the look can be filled by hand after creating.</div>}
      <ErrorText error={error} />
      <div className="row-gap">
        <button type="submit" className="primary btn-lg" disabled={busy || !name.trim()}>
          {busy ? 'Creating...' : 'Create project'}
        </button>
        {!name.trim() && <span className="muted small">Give it a name to continue.</span>}
      </div>
    </form>
  )
}

function StyleLockEditor({ project, onSaved, onDeleted }: { project: Project; onSaved: () => void; onDeleted: () => void }) {
  const navigate = useNavigate()
  const [name, setName] = useState(project.name)
  const [platform, setPlatform] = useState<PlatformId>(project.platform_target)
  const [lock, setLock] = useState<StyleLock>({ ...project.style_lock, loras: [...project.style_lock.loras], palette: [...project.style_lock.palette] })
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [newColour, setNewColour] = useState('#808080')
  const { presets, unavailable } = usePresets()
  const current = presetById(presets, lock.preset)
  const edited = !!current && !lockMatchesPreset(lock, current)

  useEffect(() => {
    setSaved(null)
  }, [name, platform, lock])

  const set = <K extends keyof StyleLock>(k: K, v: StyleLock[K]) => setLock((l) => ({ ...l, [k]: v }))

  const saveWith = async (next: StyleLock) => {
    setBusy(true)
    setError(null)
    try {
      await api.updateProject(project.id, { name: name.trim() || project.name, platform_target: platform, style_lock: next })
      setSaved(new Date().toLocaleTimeString())
      onSaved()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const save = async (e?: FormEvent) => {
    e?.preventDefault()
    await saveWith(lock)
  }

  // Choosing a look fills the fields and saves straight away.
  const usePreset = (p: LookPreset) => {
    const next = lockFromPreset(p)
    setLock(next)
    void saveWith(next)
  }

  const remove = async () => {
    if (!window.confirm(`Delete project "${project.name}" and all its assets?`)) return
    try {
      await api.deleteProject(project.id)
      onDeleted()
    } catch (err) {
      setError(err)
    }
  }

  const setPalette = (i: number, c: string) => set('palette', lock.palette.map((x, j) => (j === i ? c : x)))

  return (
    <form className="col" onSubmit={save}>
      <div className="row-between">
        <h3 className="drawer-project">
          {project.name}
          <Info term="term.style_lock" />
        </h3>
        <button type="button" className="btn" onClick={() => navigate(`/projects/${project.id}`)}>
          Open assets
        </button>
      </div>
      <div className="muted small">The look every asset in this project shares. Pick one once; every new asset follows it.</div>
      <div className="col">
        <div className="row-between">
          <div className="field-label">
            Look
            <Info term="term.look" />
          </div>
          <span className="small">
            {current ? (
              <>
                Using: <strong>{current.label}</strong>
                {edited ? ' (edited by hand)' : ''}
              </>
            ) : presets ? (
              <span className="muted">No ready-made look chosen; the fields below are set by hand.</span>
            ) : null}
          </span>
        </div>
        {presets && presets.length > 0 && <PresetRow presets={presets} currentId={current?.id ?? null} edited={edited} busy={busy} onPick={usePreset} />}
        {unavailable && <div className="muted small">Ready-made looks need a newer backend (GET /api/style-presets is missing); edit the fields below by hand.</div>}
      </div>
      <div className="grid-2">
        <Field label="Name">
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Default platform" term={'platform.' + platform} hint={platformConsequence(platform)}>
          <select value={platform} onChange={(e) => setPlatform(e.target.value as PlatformId)}>
            <PlatformOptions />
          </select>
        </Field>
      </div>
      <MatchPicture
        projectId={project.id}
        busy={busy}
        onApply={(change) => {
          const next = { ...lock, ...change }
          setLock(next)
          void saveWith(next)
        }}
      />
      <Disclosure label="Advanced (edit the look by hand)" defaultOpen={!current && !!presets}>
      <div className="col">
      <Field label="Style prompt (style words)" term="term.prompt_block" hint="Added in front of every description in this project. Enter saves, Shift+Enter adds a line.">
        <SubmitTextarea rows={3} value={lock.prompt_block} onChange={(e) => set('prompt_block', e.target.value)} placeholder="e.g. hand-painted stylised fantasy, chunky proportions, soft rim light, clean silhouette" />
      </Field>
      <div className="grid-2">
        <Field label="Model (the painter)" term={'model.' + lock.model}>
          <select value={lock.model} onChange={(e) => set('model', e.target.value as StyleModel)}>
            {STYLE_MODELS.map((m) => (
              <option key={m} value={m}>
                {modelLabel(m)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="LoRAs (style add-ons)" term="term.loras" hint="Optional. Enter adds one.">
          <ChipInput value={lock.loras} onChange={(v) => set('loras', v)} placeholder="LoRA name" />
        </Field>
      </div>
      <Field label="Palette" term="term.palette">
        <div className="palette-editor">
          {lock.palette.map((c, i) => (
            <span key={i} className="palette-item">
              <input type="color" value={/^#[0-9a-f]{6}$/i.test(c) ? c : '#808080'} onChange={(e) => setPalette(i, e.target.value)} aria-label={`colour ${i + 1}`} />
              <input className="hex" value={c} onChange={(e) => setPalette(i, e.target.value)} aria-label={`hex ${i + 1}`} />
              <button type="button" className="chip-x" aria-label="remove colour" onClick={() => set('palette', lock.palette.filter((_, j) => j !== i))}>
                x
              </button>
            </span>
          ))}
          <span className="palette-item">
            <input type="color" value={newColour} onChange={(e) => setNewColour(e.target.value)} aria-label="new colour" />
            <button type="button" onClick={() => set('palette', [...lock.palette, newColour])}>
              Add colour
            </button>
          </span>
        </div>
      </Field>
      <Field label="Negative prompt (things to avoid)" term="term.negative" hint="Things the model should leave out.">
        <SubmitTextarea rows={2} value={lock.negative} onChange={(e) => set('negative', e.target.value)} placeholder="e.g. text, watermark, blurry, extra limbs" />
      </Field>
      </div>
      </Disclosure>
      <div className="row-gap">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Saving...' : 'Save'}
        </button>
        {saved && <span className="muted small">Saved {saved}</span>}
        <ErrorText error={error} />
        <span className="grow" />
        <button type="button" className="danger" onClick={remove}>
          Delete project
        </button>
      </div>
    </form>
  )
}
