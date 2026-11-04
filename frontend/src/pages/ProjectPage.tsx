import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, libraryUrl, selectedVersion, STAGES, stageState, STYLE_MODELS, type AssetSummary, type PlatformId, type Project, type Suggestion } from '../api'
import { Info } from '../components/Info'
import { ChipInput, Disclosure, ErrorText, Field, SubmitTextarea, useAsync } from '../components/ui'
import { useEvents } from '../events'
import { rememberConceptModel } from '../guide'
import { examplePrompts, fmtDuration, modelLabel, platformConsequence, platformPlain, PROMPT_TEMPLATE, stagePlain, suggestFallback, totalEstimate } from '../help'
import { categoryLabel, useRefData } from '../refdata'

export default function ProjectPage() {
  const { pid = '' } = useParams()
  const { data: project, error: perr } = useAsync(() => api.getProject(pid), [pid])
  const { data: assets, error, reload } = useAsync(async () => {
    const list = await api.listAssets(pid)
    // The list may omit the stage versions; fetch the full objects when so.
    return Promise.all(list.map((a) => (a.stages ? Promise.resolve(a) : api.getAsset(a.id).catch(() => a))))
  }, [pid])
  const { ref } = useRefData()

  // Any version finishing for an asset of this project refreshes the grid.
  useEvents(
    (ev) => {
      if (ev.type === 'version' && assets?.some((a) => a.id === ev.version.asset_id)) reload()
      if (ev.type === 'job' && (ev.job.status === 'done' || ev.job.status === 'error') && assets?.some((a) => a.id === ev.job.asset_id)) reload()
    },
    [assets],
  )

  return (
    <div className="project-layout">
      <section className="col col-wide">
        <div className="row-between">
          <h1 className="page-title">
            <Link to="/" className="crumb">
              Projects
            </Link>
            <span className="crumb-sep">/</span>
            {project?.name ?? '...'}
          </h1>
          {project && (
            <span className="muted small">
              {platformPlain(project.platform_target)}
              <Info term={'platform.' + project.platform_target} /> / {modelLabel(project.style_lock.model)}
              <Info term={'model.' + project.style_lock.model} /> / <Link to={`/?edit=${project.id}`}>style lock</Link>
              <Info term="term.style_lock" />
            </span>
          )}
        </div>
        <ErrorText error={perr ?? error} />
        <div className="asset-grid">
          {assets
            ?.filter((a) => !a.base_asset_id || !assets.some((b) => b.id === a.base_asset_id))
            .map((a) => {
              const variants = assets.filter((v) => v.base_asset_id === a.id)
              return (
                <AssetGroup key={a.id} base={a} variants={variants} categoryName={categoryLabel(ref, a.category)} />
              )
            })}
          {assets && assets.length === 0 && (
            <div className="empty-state">
              <h2 className="card-title">
                No assets yet
                <Info term="term.asset" />
              </h2>
              <p className="muted">An asset is one thing for your game: a character, a creature, a prop. Describe your first one on the right; one sentence is enough.</p>
            </div>
          )}
        </div>
      </section>
      <aside className="col col-side">{project && <CreateAssetForm project={project} />}</aside>
    </div>
  )
}

// A base asset followed by a strip of its variants (grouped by base_asset_id).
function AssetGroup({ base, variants, categoryName }: { base: AssetSummary; variants: AssetSummary[]; categoryName: string }) {
  return (
    <>
      <AssetCard asset={base} categoryName={categoryName} />
      {variants.length > 0 && (
        <div className="variant-strip" aria-label={`variants of ${base.name}`}>
          {variants.map((v) => (
            <AssetCard key={v.id} asset={v} categoryName={`variant: ${v.variant_change ?? ''}`} />
          ))}
        </div>
      )}
    </>
  )
}

function AssetCard({ asset, categoryName }: { asset: AssetSummary; categoryName: string }) {
  const concept = selectedVersion(asset, 'concept')
  const thumb = libraryUrl(concept?.files.cutout ?? concept?.files.image)
  return (
    <Link to={`/assets/${asset.id}`} className="asset-card">
      <div className="asset-thumb checker">{thumb ? <img src={thumb} alt="" /> : <span className="muted small">no picture yet</span>}</div>
      <div className="asset-name">{asset.name}</div>
      <div className="muted small">{categoryName}</div>
      <div className="stage-dots" aria-label="stage progress">
        {STAGES.map((s) => {
          const st = stageState(asset, s)
          return <span key={s} className={'sdot sdot-' + st} title={`${stagePlain(s)}: ${st === 'none' ? 'not started' : st === 'done' ? 'done' : st === 'running' ? 'working' : 'failed'}`} />
        })}
      </div>
    </Link>
  )
}

function autoName(prompt: string): string {
  const head = prompt.split(/[,.;:]/)[0].trim().replace(/^(an?|the)\s+/i, '')
  const words = head.split(/\s+/).filter(Boolean).slice(0, 4).join(' ')
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : ''
}

function CreateAssetForm({ project }: { project: Project }) {
  const navigate = useNavigate()
  const { ref, error: refError } = useRefData()
  const [name, setName] = useState('')
  const [category, setCategory] = useState('')
  const [style, setStyle] = useState('')
  const [platform, setPlatform] = useState<PlatformId>(project.platform_target)
  const [prompt, setPrompt] = useState('')
  const [parts, setParts] = useState<string[]>([])
  const [model, setModel] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null)

  useEffect(() => {
    if (!ref) return
    if (!category && ref.categories[0]) {
      const first = ref.categories.find((c) => c.id === 'humanoid') ?? ref.categories[0]
      setCategory(first.id)
      setParts(first.parts_default ?? [])
    }
    if (!style) {
      const match = ref.styles.find((s) => s.model === project.style_lock.model) ?? ref.styles[0]
      if (match) setStyle(match.id)
    }
  }, [ref, category, style, project.style_lock.model])

  // Suggestion updates as category, style or platform change.
  useEffect(() => {
    if (!category || !style) return
    let alive = true
    api.suggest({ category, style, platform }).then(
      (s) => alive && setSuggestion(s),
      () => alive && setSuggestion(suggestFallback(category, style, platform, ref)),
    )
    return () => {
      alive = false
    }
  }, [category, style, platform, ref])

  const changeCategory = (id: string) => {
    setCategory(id)
    const c = ref?.categories.find((x) => x.id === id)
    if (c) setParts(c.parts_default ?? [])
  }

  const cat = ref?.categories.find((c) => c.id === category)
  const budget = cat?.budgets?.[platform] ?? suggestion?.budgets
  const examples = examplePrompts(category)
  const chosenModel = model || suggestion?.model || ''

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!category || !prompt.trim()) return
    setBusy(true)
    setError(null)
    try {
      const finalName = name.trim() || autoName(prompt) || 'Untitled'
      const a = await api.createAsset(project.id, { name: finalName, category, style_preset: style, platform_target: platform, prompt: prompt.trim(), parts })
      rememberConceptModel(a.id, model && model !== suggestion?.model ? model : null)
      navigate(`/assets/${a.id}`)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2 className="card-title">New asset</h2>
      <ErrorText error={refError} />
      <Field label="Describe it" term="term.prompt" hint="One sentence is enough. Enter creates the asset, Shift+Enter adds a line.">
        <SubmitTextarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={PROMPT_TEMPLATE} autoFocus />
      </Field>
      <div className="examples">
        <span className="muted small">Examples:</span>
        {examples.map((ex) => (
          <button key={ex} type="button" className="example-btn" onClick={() => setPrompt(ex)} title="Click to use this description">
            {ex}
          </button>
        ))}
      </div>
      <Field label="What is it" term={'category.' + category} hint={cat ? (cat.rig_engine === 'none' ? 'No skeleton; it will not animate.' : 'It will get a skeleton and can be animated.') : undefined}>
        <select value={category} onChange={(e) => changeCategory(e.target.value)}>
          {ref?.categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </Field>
      <div className="grid-2">
        <Field label="Look" term={'style.' + style}>
          <select value={style} onChange={(e) => setStyle(e.target.value)}>
            {ref?.styles.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Platform" term={'platform.' + platform} hint={budget ? `${budget.triangles.toLocaleString('en-US')} triangles, ${budget.texture} px texture` : platformConsequence(platform)}>
          <select value={platform} onChange={(e) => setPlatform(e.target.value as PlatformId)}>
            {(ref?.platforms ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {platformPlain(p.id)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {suggestion && (
        <div className="suggest-line">
          We suggest <strong>{model && model !== suggestion.model ? modelLabel(model) : suggestion.model_label}</strong>
          <Info term={'model.' + (chosenModel || suggestion.model)} />
          {model && model !== suggestion.model ? ` (you chose it; we would have picked ${suggestion.model_label})` : `, because ${suggestion.reason}`}; {fmtDuration(totalEstimate(suggestion.estimates, ref))} to a finished asset.
        </div>
      )}
      <Disclosure label="Advanced">
        <div className="col">
          <Field label="Name" hint={`Optional. We will call it "${autoName(prompt) || 'Untitled'}" if you leave this empty.`}>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={autoName(prompt) || 'Knight'} />
          </Field>
          <Field label="Painter" term={'model.' + (chosenModel || 'zturbo')} hint="Which AI model paints the first picture. Applied to the first attempt; you can change it later under Fine-tune.">
            <select value={chosenModel} onChange={(e) => setModel(e.target.value)}>
              {STYLE_MODELS.map((m) => (
                <option key={m} value={m}>
                  {modelLabel(m)}
                  {suggestion && m === suggestion.model ? ' (suggested)' : ''}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Parts" term="term.parts" hint="Pieces that must move or swap on their own. Enter adds one.">
            <ChipInput value={parts} onChange={setParts} placeholder="e.g. weapon" />
          </Field>
        </div>
      </Disclosure>
      <div className="row-gap">
        <button type="submit" className="primary" disabled={busy || !prompt.trim() || !category}>
          {busy ? 'Creating...' : 'Create asset'}
        </button>
        <span className="muted small">Next: we paint the first picture.</span>
        <ErrorText error={error} />
      </div>
    </form>
  )
}
