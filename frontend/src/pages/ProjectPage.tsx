import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, ApiError, itemsApi, libraryUrl, selectedVersion, STAGES, stageState, STYLE_MODELS, type AssetSummary, type ItemDetect, type PlatformId, type Project, type Suggestion } from '../api'
import { ImageDrop, nameFromFile, type PickedImage } from '../components/ImageDrop'
import { Info } from '../components/Info'
import { ChipInput, Disclosure, ErrorText, Field, SubmitTextarea, useAsync } from '../components/ui'
import { useEvents } from '../events'
import { autopilotDefault, rememberAutopilotDefault, rememberConceptModel } from '../guide'
import { axisWord, examplePrompts, fmtDuration, metres, modelLabel, platformConsequence, platformPlain, PROMPT_TEMPLATE, sizeAxisOf, sizeDefault, sizeFromDescription, stagePlain, suggestFallback, totalEstimate } from '../help'
import { presetById, usePresets } from '../presets'
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
  const { presets } = usePresets()

  // Any version finishing for an asset of this project refreshes the grid. A job
  // end sends a job event and a version event; the short delay folds them into one reload.
  const reloadRef = useRef(reload)
  useEffect(() => {
    reloadRef.current = reload
  })
  const reloadTimer = useRef<number | null>(null)
  const reloadSoon = () => {
    if (reloadTimer.current != null) return
    reloadTimer.current = window.setTimeout(() => {
      reloadTimer.current = null
      reloadRef.current()
    }, 150)
  }
  useEffect(
    () => () => {
      if (reloadTimer.current != null) window.clearTimeout(reloadTimer.current)
      reloadTimer.current = null
    },
    [],
  )
  useEvents(
    (ev) => {
      if (ev.type === 'version' && assets?.some((a) => a.id === ev.version.asset_id)) reloadSoon()
      if (ev.type === 'job' && (ev.job.status === 'done' || ev.job.status === 'error' || ev.job.status === 'cancelled') && assets?.some((a) => a.id === ev.job.asset_id)) reloadSoon()
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
              <Info term={'platform.' + project.platform_target} /> / {presetById(presets, project.style_lock.preset)?.label ?? modelLabel(project.style_lock.model)}
              <Info term={project.style_lock.preset && presetById(presets, project.style_lock.preset) ? 'term.look' : 'model.' + project.style_lock.model} /> / <Link to={`/?edit=${project.id}`}>style lock</Link>
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
  const [autopilot, setAutopilot] = useState(autopilotDefault())
  const [startFrom, setStartFrom] = useState<'words' | 'picture'>('words')
  const [picture, setPicture] = useState<PickedImage | null>(null)
  // Carried items in the description (a sword, a quiver): made separately unless the person keeps them on the body.
  const [found, setFound] = useState<ItemDetect | null>(null)
  const [itemChoice, setItemChoice] = useState<'separate' | 'keep'>('separate')
  const { presets } = usePresets()
  // The project's ready-made look names the asset style new assets default to.
  const projectLook = presetById(presets, project.style_lock.preset)
  const projectStyleId = projectLook && ref?.styles.some((s) => s.id === projectLook.style_preset) ? projectLook.style_preset : (ref?.styles.find((s) => s.model === project.style_lock.model) ?? ref?.styles[0])?.id

  useEffect(() => {
    if (!ref) return
    if (!category && ref.categories[0]) {
      const first = ref.categories.find((c) => c.id === 'humanoid') ?? ref.categories[0]
      setCategory(first.id)
      setParts(first.parts_default ?? [])
    }
    if (!style && projectStyleId) setStyle(projectStyleId)
  }, [ref, category, style, projectStyleId])

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

  // As the person types, look for carried-item words (the list lives in the backend's data/items.yaml).
  useEffect(() => {
    if (!prompt.trim() || !category) {
      setFound(null)
      return
    }
    let alive = true
    const t = window.setTimeout(() => {
      itemsApi.detect(prompt, category).then(
        (d) => alive && setFound(d.items.length ? d : null),
        () => alive && setFound(null),
      )
    }, 350)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [prompt, category])

  const changeCategory = (id: string) => {
    setCategory(id)
    const c = ref?.categories.find((x) => x.id === id)
    if (c) setParts(c.parts_default ?? [])
  }

  const cat = ref?.categories.find((c) => c.id === category)
  const budget = cat?.budgets?.[platform] ?? suggestion?.budgets
  const examples = examplePrompts(category)
  // Light G13: a size written in the description becomes the prep default (the backend reads the same words).
  const described = category && category !== 'material' ? sizeFromDescription(prompt, sizeDefault(category)) : null
  const describedWord = described ? (described.word ?? axisWord(sizeAxisOf(cat ?? category), category)) : ''
  const chosenModel = model || suggestion?.model || ''
  const overridden = !!model && !!suggestion && model !== suggestion.model
  // The six painters, plus the suggested one if the backend names a model this list does not know.
  const modelOptions: string[] = [...STYLE_MODELS]
  if (suggestion?.model && !modelOptions.includes(suggestion.model)) modelOptions.push(suggestion.model)
  const optionLabel = (m: string) => (suggestion && m === suggestion.model && !(STYLE_MODELS as readonly string[]).includes(m) ? suggestion.model_label : modelLabel(m))

  const fromPicture = startFrom === 'picture'
  const ready = !!category && (fromPicture ? !!picture : !!prompt.trim())
  const fallbackName = autoName(prompt) || (fromPicture && picture ? nameFromFile(picture.name) : '') || 'Untitled'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      const finalName = name.trim() || fallbackName
      const a = await api.createAsset(project.id, {
        name: finalName,
        category,
        style_preset: style,
        platform_target: platform,
        prompt: prompt.trim(),
        parts,
        ...(chosenModel && !fromPicture ? { concept_model: chosenModel } : {}),
        ...(fromPicture && picture ? { image: picture.dataUrl } : {}),
        ...(found?.items.length ? { items: itemChoice } : {}),
      })
      rememberConceptModel(a.id, model && model !== suggestion?.model ? model : null)
      rememberAutopilotDefault(autopilot)
      if (autopilot) {
        // Build it automatically. A backend without the endpoint (404) just lands on the asset page.
        try {
          await api.startAutopilot(a.id, { stop_on: 'block', until_stage: 'export' })
        } catch (err) {
          if (!(err instanceof ApiError && err.status === 404)) console.warn('autopilot did not start', err)
        }
      }
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
      <div className="field">
        <span className="field-label">
          Start from
          <Info term="term.start_from" />
        </span>
        <div className="segmented" role="radiogroup" aria-label="Start from">
          <label className="check-item">
            <input type="radio" name="start-from" checked={!fromPicture} onChange={() => setStartFrom('words')} />
            <span>A description</span>
          </label>
          <label className="check-item">
            <input type="radio" name="start-from" checked={fromPicture} onChange={() => setStartFrom('picture')} />
            <span>A picture I have</span>
          </label>
        </div>
      </div>
      {fromPicture && (
        <ImageDrop
          value={picture}
          onChange={setPicture}
          label="Your picture"
          hint="Best: one subject on a plain background, all of it in the picture. The app cuts it out and checks it is ready for 3D."
        />
      )}
      {fromPicture ? (
        <Field label="What is it? (optional, helps the later steps)" term="term.what_is_it_note" hint="For example: a knight in red armour with a round shield.">
          <SubmitTextarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="A few words about your picture" />
        </Field>
      ) : (
        <Field label="Describe it" term="term.prompt" hint="One sentence is enough. Enter creates the asset, Shift+Enter adds a line.">
          <SubmitTextarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={PROMPT_TEMPLATE} autoFocus />
        </Field>
      )}
      {described && (
        <div className="muted small described-size">
          Size: {metres(described.value)} m {describedWord}, from your description. You can change it later in {stagePlain('prep')}.
        </div>
      )}
      {found && found.items.length > 0 && <ItemsLine found={found} choice={itemChoice} onChoice={setItemChoice} />}
      {!fromPicture && (
        <div className="examples">
          <span className="muted small">Examples:</span>
          {examples.map((ex) => (
            <button key={ex} type="button" className="example-btn" onClick={() => setPrompt(ex)} title="Click to use this description">
              {ex}
            </button>
          ))}
        </div>
      )}
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
        <Field label="Look" term={'style.' + style} hint={projectLook ? (style === projectStyleId ? `Your project's look: ${projectLook.label}.` : `Different from your project's look (${projectLook.label}).`) : undefined}>
          <select value={style} onChange={(e) => setStyle(e.target.value)}>
            {ref?.styles.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
                {projectLook && s.id === projectStyleId ? ` (${projectLook.label}, the project look)` : ''}
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
      {suggestion && !fromPicture && (
        <div className="suggest-line">
          {overridden ? (
            <>
              Painter: <strong>{modelLabel(model)}</strong>
              <Info term={'model.' + model} /> (your choice; suggested: {suggestion.model_label})
            </>
          ) : (
            <>
              Suggested: <strong>{suggestion.model_label}</strong>
              <Info term={'model.' + suggestion.model} /> because {suggestion.reason}
            </>
          )}
          ; {fmtDuration(totalEstimate(suggestion.estimates, ref))} to a finished asset. <span className="muted">Change the painter under Advanced.</span>
        </div>
      )}
      {suggestion?.recipe_plain && (
        <div className="recipe-line small">
          Recipe: {suggestion.recipe_plain}
          <Info term="term.recipe" />
        </div>
      )}
      {(suggestion?.recipe_notes ?? []).map((note) => (
        <div className="suggest-line muted" key={note}>Good to know: {note}</div>
      ))}
      <Disclosure label="Advanced">
        <div className="col">
          <Field label="Name" hint={`Optional. We will call it "${fallbackName}" if you leave this empty.`}>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={fallbackName === 'Untitled' ? 'Knight' : fallbackName} />
          </Field>
          <Field
            label="Painter"
            term="term.concept_model"
            hint={
              suggestion ? (
                <>
                  Suggested: {suggestion.model_label} because {suggestion.reason}. Pick another to override it; you can change it later under Fine-tune.
                  {overridden && (
                    <>
                      {' '}
                      <button type="button" className="link small" onClick={() => setModel('')}>
                        Use the suggestion
                      </button>
                    </>
                  )}
                </>
              ) : (
                'Which AI model paints the first picture. You can change it later under Fine-tune.'
              )
            }
          >
            <span className="row-gap">
              <select value={chosenModel} onChange={(e) => setModel(e.target.value === suggestion?.model ? '' : e.target.value)} aria-label="Painter">
                {!chosenModel && <option value="">choosing...</option>}
                {modelOptions.map((m) => (
                  <option key={m} value={m}>
                    {optionLabel(m)}
                    {suggestion && m === suggestion.model ? ' (suggested)' : ''}
                  </option>
                ))}
              </select>
              {chosenModel && <Info term={'model.' + chosenModel} />}
            </span>
          </Field>
          <Field label="Parts" term="term.parts" hint="Pieces that must move or swap on their own. Enter adds one.">
            <ChipInput value={parts} onChange={setParts} placeholder="e.g. weapon" />
          </Field>
        </div>
      </Disclosure>
      <label className="check-item autopilot-check">
        <input type="checkbox" checked={autopilot} onChange={(e) => setAutopilot(e.target.checked)} />
        <span>
          Build it automatically after creating
          <Info term="term.autopilot" />
          <span className="muted small block">Runs every step and only stops if something needs you.</span>
        </span>
      </label>
      <div className="row-gap">
        <button type="submit" className="primary" disabled={busy || !ready}>
          {busy ? 'Creating...' : autopilot ? 'Create and make it' : 'Create asset'}
        </button>
        <span className="muted small">
          {autopilot ? 'Next: every step runs by itself.' : fromPicture ? 'Next: we cut out your picture and check it is ready for 3D.' : 'Next: we paint the first picture.'}
        </span>
        <ErrorText error={error} />
      </div>
    </form>
  )
}

// The quiet line under the description when it names carried items (docs/CHARACTER-PARTS-RESEARCH.md 6.2).
function ItemsLine({ found, choice, onChoice }: { found: ItemDetect; choice: 'separate' | 'keep'; onChoice: (c: 'separate' | 'keep') => void }) {
  const names = found.items.map((i) => i.label)
  return (
    <div className="items-line" role="group" aria-label="Carried items">
      <p className="small">
        {found.sentence ?? `Weapons work best as their own asset: ${names.join(', ')}.`}
        <Info term="term.items" />
      </p>
      <div className="segmented" role="radiogroup" aria-label="What to do with the items">
        <label className="check-item">
          <input type="radio" name="items-choice" checked={choice === 'separate'} onChange={() => onChoice('separate')} />
          <span>Make them separately (recommended)</span>
        </label>
        <label className="check-item">
          <input type="radio" name="items-choice" checked={choice === 'keep'} onChange={() => onChoice('keep')} />
          <span>Keep them part of the body</span>
        </label>
      </div>
      <p className="muted small">
        {choice === 'separate'
          ? `Found: ${found.items.map((i) => `${i.phrase} (${i.place.toLowerCase()})`).join('; ')}. Clothes that bend with the body stay part of it.`
          : 'They will be painted and built as part of the body, so they bend with it and cannot be held.'}
      </p>
    </div>
  )
}
