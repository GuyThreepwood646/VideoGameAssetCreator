// Fine-tune (the Adjust form) with a simple layer and an Advanced disclosure.
// Applying it posts /api/versions/{vid}/adjust: a new version with these settings.
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { type AdjustParam, type Asset, type StageId, type Suggestion, type Version } from '../api'
import { Info } from '../components/Info'
import { Disclosure, Field } from '../components/ui'
import { fmtDuration, isSimpleParam, modelLabel, paramPlain, platformPlain, totalEstimate } from '../help'
import { type RefData } from '../refdata'

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
}: {
  stage: StageId
  schema: AdjustParam[]
  target: Version
  asset: Asset
  refData: RefData | null
  suggestion: Suggestion | null
  disabled: boolean
  onSubmit: (params: Record<string, unknown>) => void
}) {
  // Reset the form only when its content changes: another version, another schema or
  // other stored settings. Asset reloads hand over new objects with the same content,
  // and those must not wipe what the person is editing.
  const resetKey = `${target.id}|${JSON.stringify(schema)}|${JSON.stringify(target.params ?? {})}`
  const initial = useMemo(() => {
    const out: Record<string, unknown> = {}
    for (const p of schema) {
      const current = target.params?.[p.name]
      out[p.name] = current !== undefined ? current : p.default !== undefined ? p.default : defaultFor(p)
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])
  const [values, setValues] = useState<Record<string, unknown>>(initial)
  useEffect(() => setValues(initial), [initial])

  const set = (k: string, v: unknown) => setValues((s) => ({ ...s, [k]: v }))
  const changed = schema.filter((p) => JSON.stringify(values[p.name] ?? null) !== JSON.stringify(initial[p.name] ?? null))

  const schemaHasFlag = schema.some((p) => p.simple !== undefined)
  const simple = schema.filter((p) => isSimpleParam(stage, p, schemaHasFlag))
  const advanced = schema.filter((p) => !simple.includes(p))
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

  const renderField = (p: AdjustParam, adv: boolean) => {
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
    return <AdjustField key={p.name} param={p} value={values[p.name]} onChange={(v) => set(p.name, v)} disabled={disabled} />
  }

  return (
    <form className="finetune" onSubmit={submit}>
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
      {schema.length === 0 && <div className="muted small">Nothing to fine-tune in this step.</div>}
      {simpleShown.length > 0 && <div className="adjust-grid">{simpleShown.map((p) => renderField(p, false))}</div>}
      {advanced.length > 0 && (
        <Disclosure label={`Advanced (${advanced.length} more setting${advanced.length > 1 ? 's' : ''})`}>
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

function AdjustField({ param: p, value, onChange, disabled }: { param: AdjustParam; value: unknown; onChange: (v: unknown) => void; disabled: boolean }) {
  const label = paramPlain(p.name, p.label)
  const term = `param.${p.name}`
  const hint = p.help?.meaning ?? p.description
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
              <span className="mono">{Number.isFinite(num) ? num : 'auto'}</span>
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
                {p.name === 'model' ? modelLabel(String(o)) : String(o)}
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
                {String(o)}
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
