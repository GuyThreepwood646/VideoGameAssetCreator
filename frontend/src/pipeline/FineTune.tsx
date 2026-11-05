// Fine-tune (the Adjust form) with a simple layer and an Advanced disclosure.
// Applying it posts /api/versions/{vid}/adjust: a new version with these settings.
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { libraryUrl, selectedVersion, type AdjustParam, type Asset, type StageId, type Suggestion, type Version } from '../api'
import { Info } from '../components/Info'
import { Disclosure, Field } from '../components/ui'
import { aboutSize, fmtDuration, gridReadout, isSimpleParam, knobApplies, measuredSize, modelLabel, MOTION_BY_CATEGORY, MOVES_BY_MOTION, optionLabel, paramPlain, platformPlain, sizeHint, sizeQuestion, totalEstimate } from '../help'
import { type RefData } from '../refdata'
import { FrontPicker } from './FrontPicker'
import { type ToolFocus } from './openTool'

const SIZE_OPTIONS = [768, 1024, 1280, 1536, 2048]

export function FineTune({
  stage,
  schema,
  target,
  asset,
  refData,
  suggestion,
  disabled,
  onSubmit,
  focus,
}: {
  stage: StageId
  schema: AdjustParam[]
  target: Version
  asset: Asset
  refData: RefData | null
  suggestion: Suggestion | null
  disabled: boolean
  onSubmit: (params: Record<string, unknown>) => void
  // A Fix that opens a tool (2.5): fill these params in and bring this setting into view; nothing runs.
  focus?: ToolFocus | null
}) {
  // Knobs that apply to some categories only are left out for the others (2.3); they are never sent.
  const visible = useMemo(() => schema.filter((p) => knobApplies(p, asset.category)), [schema, asset.category])
  // Reset the form only when its content changes: another version, another schema or
  // other stored settings. Asset reloads hand over new objects with the same content,
  // and those must not wipe what the person is editing.
  const resetKey = `${target.id}|${JSON.stringify(visible)}|${JSON.stringify(target.params ?? {})}`
  const initial = useMemo(() => {
    const out: Record<string, unknown> = {}
    for (const p of visible) {
      // size_m was height_m in older versions (3.2): read the old name as an alias.
      const current = target.params?.[p.name] ?? (p.name === 'size_m' ? target.params?.height_m : undefined)
      // A per-category default (by_category, 3.2) wins over the knob's single default.
      const catDefault = p.by_category?.[asset.category]?.default
      out[p.name] = current !== undefined ? current : catDefault !== undefined ? catDefault : p.default !== undefined ? p.default : defaultFor(p)
    }
    // Moves and body parts left empty mean "all of them" (3.7, 4.2): show every one ticked. The backend records
    // turn_left and turn_right; the knob offers them as one move, "turn".
    for (const name of ['moves', 'hitbox_regions']) {
      const p = visible.find((x) => x.name === name)
      if (!p) continue
      const cur = out[name]
      if (cur === null || cur === undefined || (Array.isArray(cur) && cur.length === 0 && target.params?.[name] == null)) out[name] = shownOptions(p, out.motion).map(String)
      else if (Array.isArray(cur)) out[name] = [...new Set(cur.map((c) => (c === 'turn_left' || c === 'turn_right' ? 'turn' : String(c))))]
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])
  const [values, setValues] = useState<Record<string, unknown>>(initial)
  useEffect(() => setValues(initial), [initial])

  const set = (k: string, v: unknown) => setValues((s) => ({ ...s, [k]: v }))
  const changed = visible.filter((p) => JSON.stringify(values[p.name] ?? null) !== JSON.stringify(initial[p.name] ?? null))

  const schemaHasFlag = visible.some((p) => p.simple !== undefined)
  const simple = visible.filter((p) => isSimpleParam(stage, p, schemaHasFlag))
  const advanced = visible.filter((p) => !simple.includes(p))

  // Tool focus: fill in the fix's params, open Advanced if the setting lives there, scroll to it and focus it.
  const formRef = useRef<HTMLFormElement>(null)
  const [advOpen, setAdvOpen] = useState(false)
  const [flash, setFlash] = useState<string | null>(null)
  useEffect(() => {
    if (!focus) return
    const known = Object.fromEntries(Object.entries(focus.params ?? {}).filter(([k]) => visible.some((p) => p.name === k)))
    if (Object.keys(known).length) setValues((cur) => ({ ...cur, ...known }))
    const knob = focus.knob && visible.some((p) => p.name === focus.knob) ? focus.knob : null
    if (!knob) return
    if (advanced.some((p) => p.name === knob)) setAdvOpen(true)
    const show = window.setTimeout(() => {
      const el = formRef.current?.querySelector<HTMLElement>(`[data-knob="${knob}"]`)
      if (!el) return
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      el.scrollIntoView({ block: 'center', behavior: reduce ? 'auto' : 'smooth' })
      const target = el.querySelector<HTMLElement>('input:checked') ?? el.querySelector<HTMLElement>('input, select, textarea')
      target?.focus({ preventScroll: true })
      setFlash(knob)
    })
    const t = window.setTimeout(() => setFlash(null), 2500)
    return () => {
      window.clearTimeout(show)
      window.clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce])
  const hasSize = stage === 'concept' && simple.some((p) => p.name === 'width') && simple.some((p) => p.name === 'height')
  const simpleShown = hasSize ? simple.filter((p) => p.name !== 'height') : simple

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!changed.length) return
    const params: Record<string, unknown> = {}
    for (const p of changed) params[p.name] = values[p.name]
    onSubmit(params)
  }

  const cat = refData?.categories.find((c) => c.id === asset.category)
  const plat = refData?.platforms.find((p) => p.id === asset.platform_target)
  const budget = cat?.budgets?.[asset.platform_target]?.triangles ?? suggestion?.budgets.triangles ?? plat?.triangles_character ?? 20000

  const renderField = (p: AdjustParam, adv: boolean) => (
    <div key={p.name} data-knob={p.name} className={'knob' + (flash === p.name ? ' knob-flash' : '') + (p.name === 'face_yaw' ? ' knob-wide' : '')}>
      {renderControl(p, adv)}
    </div>
  )

  const renderControl = (p: AdjustParam, adv: boolean) => {
    if (hasSize && p.name === 'width') {
      const w = Number(values.width ?? 1024)
      const opts = SIZE_OPTIONS.filter((o) => (p.min === undefined || o >= p.min) && (p.max === undefined || o <= p.max))
      if (!opts.includes(w)) opts.push(w)
      return (
        <Field key="size" label="Size" term="term.size" hint="Width and height of the picture">
          <select
            value={w}
            disabled={disabled}
            onChange={(e) => {
              const n = Number(e.target.value)
              set('width', n)
              set('height', n)
            }}
          >
            {opts
              .sort((a, b) => a - b)
              .map((o) => (
                <option key={o} value={o}>
                  {o} px{o === 1024 ? ' (recommended)' : ''}
                </option>
              ))}
          </select>
        </Field>
      )
    }
    if (stage === 'mesh' && p.name === 'target_faces' && !adv) {
      const cur = values.target_faces
      const curN = typeof cur === 'number' ? cur : null
      const levels: { label: string; value: number | null }[] = [
        ...(p.default === null || p.default === undefined ? [{ label: `Automatic (${budget.toLocaleString('en-US')} for ${stagePlainPlatform(asset.platform_target)})`, value: null }] : []),
        { label: `Lower detail (${Math.round(budget / 2).toLocaleString('en-US')} triangles, smaller file)`, value: clamp(Math.round(budget / 2), p) },
        { label: `Standard (${budget.toLocaleString('en-US')} triangles, the platform budget)`, value: clamp(budget, p) },
        { label: `Higher detail (${(budget * 2).toLocaleString('en-US')} triangles, over budget)`, value: clamp(budget * 2, p) },
      ]
      if (curN !== null && !levels.some((l) => l.value === curN)) levels.push({ label: `Current (${curN.toLocaleString('en-US')} triangles)`, value: curN })
      return (
        <Field key={p.name} label="Detail level" term="param.target_faces" hint="More triangles mean more detail and a heavier model">
          <select value={cur === null || cur === undefined ? '' : String(cur)} disabled={disabled} onChange={(e) => set('target_faces', e.target.value === '' ? null : Number(e.target.value))}>
            {levels.map((l) => (
              <option key={String(l.value)} value={l.value === null ? '' : String(l.value)}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
      )
    }
    if (stage === 'concept' && p.name === 'model') {
      return (
        <Field key={p.name} label={paramPlain('model', p.label)} term="param.model" hint="Which AI model paints the picture.">
          <select value={String(values.model ?? '')} disabled={disabled} onChange={(e) => set('model', e.target.value)}>
            {(p.options ?? []).map((o) => (
              <option key={String(o)} value={String(o)}>
                {modelLabel(String(o))}
                {suggestion && String(o) === suggestion.model ? ' (suggested)' : ''}
              </option>
            ))}
          </select>
        </Field>
      )
    }
    // Size (3.2): the question and hint come from the category; the readout says what this version measures.
    if (stage === 'prep' && (p.name === 'size_m' || p.name === 'height_m')) {
      const now = measuredSize(target.metrics, asset.category)
      const own = p.by_category?.[asset.category]
      return (
        <AdjustField
          param={p}
          value={values[p.name]}
          onChange={(v) => set(p.name, v)}
          disabled={disabled}
          labelText={own?.label || sizeQuestion(cat ?? asset.category)}
          term="param.size_m"
          hintText={
            <>
              {own?.hint || p.hint || sizeHint(cat ?? asset.category)}
              {now && <span className="size-now"> Now: {aboutSize(now.value, now.word).replace(/^About/, 'about')}.</span>}
            </>
          }
          unit="m"
        />
      )
    }
    // Scenery on a grid: the footprint knob says what this version measures on the grid.
    if (stage === 'prep' && p.name === 'grid_footprint') {
      const now = gridReadout(target.metrics)
      return (
        <AdjustField
          param={p}
          value={values[p.name]}
          onChange={(v) => set(p.name, v)}
          disabled={disabled}
          hintText={
            <>
              {p.hint || sizeHint(cat ?? asset.category)}
              {now && <span className="size-now"> Now: {now.replace(/^On the grid: /, '')}.</span>}
            </>
          }
        />
      )
    }
    if (p.name === 'face_yaw') {
      const meshGlb = libraryUrl(selectedVersion(asset, 'mesh')?.files.glb)
      const m = target.metrics ?? {}
      // "chosen" marks the turn prep applied, only when this version asked for Automatic.
      const wasAuto = target.params?.face_yaw === undefined || target.params?.face_yaw === 'auto'
      const applied = wasAuto ? (typeof m.face_yaw_applied === 'number' ? m.face_yaw_applied : undefined) : undefined
      return (
        <FrontPicker
          value={values.face_yaw}
          onChange={(v) => set('face_yaw', v)}
          options={p.options}
          optionLabels={p.option_labels}
          label={p.label && !/yaw/i.test(p.label) ? p.label : undefined}
          hint={p.hint}
          glbUrl={meshGlb}
          chosenYaw={applied ?? null}
          disabled={disabled}
        />
      )
    }
    return <AdjustField param={p.name === 'moves' || p.name === 'hitbox_regions' ? { ...p, options: shownOptions(p, values.motion, values[p.name]) } : p} value={values[p.name]} onChange={(v) => set(p.name, v)} disabled={disabled} />
  }

  // The choices worth offering: the moves this way of moving can make, and the body parts this skeleton has.
  // Anything already chosen stays listed.
  function shownOptions(p: AdjustParam, motion: unknown, chosen?: unknown): NonNullable<AdjustParam['options']> {
    const all = p.options ?? []
    let keep: string[] | null = null
    if (p.name === 'moves') {
      const mo = typeof motion === 'string' && motion ? motion : (MOTION_BY_CATEGORY[asset.category] ?? '')
      keep = MOVES_BY_MOTION[mo] ?? null
    } else if (p.name === 'hitbox_regions') {
      const hb = target.metrics?.hitboxes as { regions_found?: ({ id?: string } | string)[] } | undefined
      const found = Array.isArray(hb?.regions_found) ? hb.regions_found.map((r) => (typeof r === 'string' ? r : r?.id)).filter((r): r is string => typeof r === 'string') : []
      keep = found.length ? found : null
    }
    if (!keep) return all
    const picked = Array.isArray(chosen) ? chosen.map(String) : []
    return all.filter((o) => keep!.includes(String(o)) || picked.includes(String(o)))
  }

  return (
    <form className="finetune" onSubmit={submit} ref={formRef}>
      <div className="row-between">
        <div className="field-label">
          Fine-tune
          <Info term="term.finetune" />
        </div>
        {stage === 'concept' && suggestion && (
          <span className="muted small">
            We suggest {suggestion.model_label}, because {suggestion.reason}; {fmtDuration(totalEstimate(suggestion.estimates, refData))} to a finished asset.
          </span>
        )}
      </div>
      {visible.length === 0 && <div className="muted small">Nothing to fine-tune in this step.</div>}
      {simpleShown.length > 0 && <div className="adjust-grid">{simpleShown.map((p) => renderField(p, false))}</div>}
      {advanced.length > 0 && (
        <Disclosure label={`Advanced (${advanced.length} more setting${advanced.length > 1 ? 's' : ''})`} open={advOpen} onToggle={setAdvOpen}>
          <div className="adjust-grid">{advanced.map((p) => renderField(p, true))}</div>
        </Disclosure>
      )}
      <div className="row-gap">
        <button type="submit" disabled={disabled || !changed.length}>
          Apply and re-run
        </button>
        <button type="button" disabled={!changed.length} onClick={() => setValues(initial)}>
          Reset
        </button>
        <span className="muted small">{changed.length ? `${changed.length} changed: ${changed.map((p) => paramPlain(p.name, p.label)).join(', ')}` : 'no changes yet'}</span>
      </div>
    </form>
  )
}

function stagePlainPlatform(id: string): string {
  return platformPlain(id).split(':')[0].toLowerCase() || id
}

function clamp(n: number, p: AdjustParam): number {
  if (p.min !== undefined) n = Math.max(p.min, n)
  if (p.max !== undefined) n = Math.min(p.max, n)
  return n
}

function defaultFor(p: AdjustParam): unknown {
  switch (p.type) {
    case 'number':
    case 'integer':
      return p.min ?? 0
    case 'boolean':
      return false
    case 'multiselect':
      return []
    case 'select':
      return p.options?.[0] ?? ''
    default:
      return ''
  }
}

function AdjustField({
  param: p,
  value,
  onChange,
  disabled,
  labelText,
  hintText,
  term: termOverride,
  unit,
}: {
  param: AdjustParam
  value: unknown
  onChange: (v: unknown) => void
  disabled: boolean
  labelText?: string
  hintText?: ReactNode
  term?: string
  unit?: string
}) {
  const label = labelText ?? paramPlain(p.name, p.label)
  const term = termOverride ?? `param.${p.name}`
  const hint = hintText ?? p.hint ?? p.help?.meaning ?? p.description
  const tech = <span className="mono muted">{p.name}</span>
  const hintNode = hint ? (
    <>
      {hint} <span className="muted">({tech})</span>
    </>
  ) : (
    tech
  )
  switch (p.type) {
    case 'number':
    case 'integer': {
      const empty = value === null || value === undefined || value === ''
      const num = empty ? NaN : typeof value === 'number' ? value : Number(value)
      const step = p.step ?? (p.type === 'integer' ? 1 : 0.01)
      const hasRange = p.min !== undefined && p.max !== undefined && p.max - p.min <= 100000
      return (
        <Field
          label={
            <span className="row-between">
              {label}
              <span className="mono">{Number.isFinite(num) ? `${num}${unit ? ' ' + unit : ''}` : 'auto'}</span>
            </span>
          }
          term={term}
          hint={hintNode}
        >
          <div className="slider-row">
            {hasRange && (
              <input
                type="range"
                min={p.min}
                max={p.max}
                step={step}
                value={Number.isFinite(num) ? num : (p.default as number) ?? p.min ?? 0}
                disabled={disabled}
                onChange={(e) => onChange(p.type === 'integer' ? Math.round(Number(e.target.value)) : Number(e.target.value))}
              />
            )}
            <input
              type="number"
              className="num"
              min={p.min}
              max={p.max}
              step={step}
              placeholder="auto"
              value={Number.isFinite(num) ? num : ''}
              disabled={disabled}
              onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
            />
          </div>
        </Field>
      )
    }
    case 'boolean':
      return (
        <Field label={label} inline term={term} hint={hintNode}>
          <input type="checkbox" checked={!!value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        </Field>
      )
    case 'select':
      return (
        <Field label={label} term={term} hint={hintNode}>
          <select value={String(value ?? '')} disabled={disabled} onChange={(e) => onChange(e.target.value === '' ? null : coerceOption(p, e.target.value))}>
            {(value === null || value === undefined) && <option value="">automatic</option>}
            {(p.options ?? []).map((o) => (
              <option key={String(o)} value={String(o)}>
                {optionLabel(p, o)}
              </option>
            ))}
          </select>
        </Field>
      )
    case 'multiselect': {
      const arr = Array.isArray(value) ? (value as unknown[]).map(String) : []
      return (
        <Field label={label} term={term} hint={hintNode}>
          <div className="check-row">
            {(p.options ?? []).map((o) => (
              <label key={String(o)} className="check-item">
                <input
                  type="checkbox"
                  checked={arr.includes(String(o))}
                  disabled={disabled}
                  onChange={(e) => {
                    const next = e.target.checked ? [...arr, String(o)] : arr.filter((x) => x !== String(o))
                    onChange((p.options ?? []).filter((x) => next.includes(String(x))))
                  }}
                />
                {optionLabel(p, o)}
              </label>
            ))}
          </div>
        </Field>
      )
    }
    default:
      return (
        <Field label={label} term={term} hint={hintNode}>
          <input value={Array.isArray(value) ? value.join(', ') : String(value ?? '')} placeholder={p.default === null ? 'automatic' : undefined} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
        </Field>
      )
  }
}

function coerceOption(p: AdjustParam, raw: string): unknown {
  const match = (p.options ?? []).find((o) => String(o) === raw)
  return match !== undefined ? match : raw
}
