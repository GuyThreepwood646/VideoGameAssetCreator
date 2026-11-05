import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api, PLATFORMS, STYLE_MODELS, type LookPreset, type PlatformId, type Project, type StyleLock, type StyleModel } from '../api'
import { Info } from '../components/Info'
import { PresetRow } from '../components/PresetRow'
import { ChipInput, Disclosure, ErrorText, Field, SubmitTextarea, timeAgo, useAsync } from '../components/ui'
import { modelLabel, platformConsequence, platformPlain } from '../help'
import { lockFromPreset, lockMatchesPreset, presetById, usePresets } from '../presets'
import { MatchPicture } from './MatchPicture'

export default function ProjectsPage() {
  const { data: projects, error, loading, reload } = useAsync(() => api.listProjects(), [])
  const [params, setParams] = useSearchParams()
  const editId = params.get('edit')
  const editing = projects?.find((p) => p.id === editId) ?? null
  const { presets } = usePresets()

  return (
    <div className="projects-layout">
      <section className="col">
        <div className="row-between">
          <h1 className="page-title">
            Projects
            <Info term="term.project" />
          </h1>
        </div>
        <ErrorText error={error} />
        {loading && !projects && <div className="muted">Loading...</div>}
        {projects && projects.length === 0 && <EmptyState />}
        <ul className="project-list">
          {projects?.map((p) => (
            <li key={p.id} className={'project-row' + (p.id === editId ? ' active' : '')}>
              <button type="button" className="project-main" onClick={() => setParams({ edit: p.id })}>
                <span className="project-name">{p.name}</span>
                <span className="muted small">
                  {platformPlain(p.platform_target)} / {presetById(presets, p.style_lock.preset)?.label ?? modelLabel(p.style_lock.model)}
                  {p.style_lock.loras.length ? ` / ${p.style_lock.loras.length} style add-on${p.style_lock.loras.length > 1 ? 's' : ''}` : ''} / created {timeAgo(p.created_at)}
                </span>
                <span className="swatches">
                  {p.style_lock.palette.map((c, i) => (
                    <span key={i} className="swatch" style={{ background: c }} title={c} />
                  ))}
                </span>
              </button>
              <Link className="btn" to={`/projects/${p.id}`}>
                Open assets
              </Link>
            </li>
          ))}
        </ul>
        <CreateProjectForm
          onCreated={(p) => {
            reload()
            setParams({ edit: p.id })
          }}
          primary={!projects || projects.length === 0}
        />
      </section>
      <section className="col col-wide">
        {editing ? (
          <StyleLockEditor key={editing.id} project={editing} onSaved={reload} onDeleted={() => { setParams({}); reload() }} />
        ) : (
          <div className="placeholder-box">
            {projects && projects.length > 0 ? 'Select a project to change its look (style lock) and default platform, or open its assets.' : 'Create a project to get started.'}
          </div>
        )}
      </section>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="empty-state">
      <h2 className="card-title">Three words to know</h2>
      <p>
        <strong>Project</strong>
        <Info term="term.project" /> is one game, or one visually coherent set. It owns the style lock (the look every asset shares: painting style, palette, model choice) and the default platform target (mobile, PC). Everything made inside a project matches; you would make a second project for a different game or a different art style, not for a second character.
      </p>
      <p>
        <strong>Asset</strong>
        <Info term="term.asset" /> is one distinct thing: a character, a creature, a prop, a tileable material. The knight and the wolf are two assets in the same project. An asset carries its prompt, its category (which decides the pose, the rig type and the budgets) and its platform target.
      </p>
      <p>
        <strong>Version</strong>
        <Info term="term.version" /> is one attempt at one stage of one asset. When you regenerate a concept you get more versions in the concept stage; when you refine one, the result is a new version whose parent is the one you refined. Every later stage builds on the version you selected in the stage before, so nothing is lost by trying something.
      </p>
      <p className="muted">Start by creating a project below, then add your first asset.</p>
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

function CreateProjectForm({ onCreated, primary }: { onCreated: (p: Project) => void; primary: boolean }) {
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
      setName('')
      onCreated(p)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card form-inline" onSubmit={submit}>
      <h2 className="card-title">New project</h2>
      <div className="form-row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Project name, e.g. Hollow Keep" aria-label="Project name" autoFocus />
        <select value={platform} onChange={(e) => setPlatform(e.target.value as PlatformId)} aria-label="Default platform target">
          <PlatformOptions />
        </select>
        <Info term={'platform.' + platform} />
        <button type="submit" className={primary ? 'primary' : ''} disabled={busy || !name.trim()}>
          Create
        </button>
      </div>
      <div className="muted small">{platformConsequence(platform)}</div>
      {presets && presets.length > 0 && (
        <div className="col">
          <div className="field-label">
            Pick a look
            <Info term="term.look" />
            <span className="muted small"> (you can change it later)</span>
          </div>
          <PresetRow presets={presets} currentId={chosen?.id ?? null} busy={busy} compact onPick={(p) => setPresetId(p.id)} />
          {chosen && (
            <div className="muted small">
              Look: <strong>{chosen.label}</strong>. {chosen.description}
            </div>
          )}
        </div>
      )}
      {unavailable && <div className="muted small">Ready-made looks need a newer backend; the style lock can be filled by hand after creating.</div>}
      <ErrorText error={error} />
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
    <form className="card" onSubmit={save}>
      <div className="row-between">
        <h2 className="card-title">
          Style lock: {project.name}
          <Info term="term.style_lock" />
        </h2>
        <div className="row-gap">
          <button type="button" className="btn" onClick={() => navigate(`/projects/${project.id}`)}>
            Open assets
          </button>
          <button type="button" className="danger" onClick={remove}>
            Delete
          </button>
        </div>
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
      <Field label="Style words" term="term.prompt_block" hint="Added in front of every description in this project. Enter saves, Shift+Enter adds a line.">
        <SubmitTextarea rows={3} value={lock.prompt_block} onChange={(e) => set('prompt_block', e.target.value)} placeholder="e.g. hand-painted stylised fantasy, chunky proportions, soft rim light, clean silhouette" />
      </Field>
      <div className="grid-2">
        <Field label="Painter" term={'model.' + lock.model}>
          <select value={lock.model} onChange={(e) => set('model', e.target.value as StyleModel)}>
            {STYLE_MODELS.map((m) => (
              <option key={m} value={m}>
                {modelLabel(m)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Style add-ons" term="term.loras" hint="Optional. Enter adds one.">
          <ChipInput value={lock.loras} onChange={(v) => set('loras', v)} placeholder="add-on name" />
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
      <Field label="Avoid" term="term.negative" hint="Things the painter should leave out.">
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
      </div>
    </form>
  )
}
