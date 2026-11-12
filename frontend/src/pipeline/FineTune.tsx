// Fine-tune (the Adjust form) with a simple layer and an Advanced disclosure.
// Applying it posts /api/versions/{vid}/adjust: a new version with these settings.
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { libraryUrl, selectedVersion, type AddonChoice, type AdjustParam, type Asset, type StageId, type Suggestion, type TurnMethod, type Version } from '../api'
import { Info } from '../components/Info'
import { Disclosure, Field } from '../components/ui'
import { aboutSize, fmtDuration, plainJargon, gridReadout, isSimpleParam, knobApplies, measuredSize, modelLabel, MOTION_BY_CATEGORY, MOVES_BY_MOTION, optionLabel, paramPlain, platformPlain, sizeHint, sizeQuestion, totalEstimate } from '../help'
import { type RefData } from '../refdata'
import { useRecipe } from '../recipe'
import { addonRows, AddonsHint, AddonsList, addonsNotUsed, TurnaroundAddons } from './Addons'
import { useMyAddons, withMyAddons } from './AddMyAddon'
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
  onPending,
  describedChange = '',
  resetNonce = 0,
  submitLabel,
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
  // The changed settings as they would be sent (null when nothing changed), so Make the change can take them too.
  onPending?: (params: Record<string, unknown> | null, names: string[]) => void
  // Text waiting in Describe the change: Apply and re-run sends it along.
  describedChange?: string
  // Bumped by the panel after it sent these settings with a described change: the form goes back to the version's own.
  resetNonce?: number
  // The first build of a step: its own button words, and it may run with nothing changed (the recipe's defaults).
  submitLabel?: string
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
    // Add-ons: only the choices this version made ({id: off | subtle | normal | strong}); the rest follow the recipe.
    for (const p of visible) if (p.type === 'addons') out[p.name] = addonChoices(target.params?.[p.name])
    // A character gets Three-quarter (other side) whenever Three-quarter is drawn (turnaround with_second_quarter), so
    // the ticks show it unless this version left it out (second_quarter false).
    if (stage === 'turnaround' && asset.category === 'humanoid' && Array.isArray(out.views)) {
      const v = (out.views as unknown[]).map(String)
      if (v.includes('quarter') && !v.includes('quarter2') && target.params?.second_quarter !== false) {
        const p = visible.find((x) => x.name === 'views')
        out.views = (p?.options ?? []).filter((o) => v.includes(String(o)) || String(o) === 'quarter2')
      }
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])
  const [values, setValues] = useState<Record<string, unknown>>(initial)
  useEffect(() => setValues(initial), [initial])

  const set = (k: string, v: unknown) =>
    setValues((s) => {
      const next = { ...s, [k]: v }
      // A setting that only works with another one goes off with it, so a stale "on" is never sent.
      for (const [dep, on] of Object.entries(DEPENDS_ON)) if (on.clear && on.param === k && !on.isOn(v) && dep in next && next[dep]) next[dep] = false
      return next
    })
  // Why a setting cannot be used right now (it needs another setting, or an earlier step already did its job).
  const step2Posed = stage === 'mesh' && poseReadyUpstream(asset, target)
  const blockedReason = (name: string): string | null => {
    const dep = DEPENDS_ON[name]
    if (dep && visible.some((p) => p.name === dep.param) && !dep.isOn(values[dep.param])) return dep.reason
    if (name === 'hand_edit' && step2Posed) return 'Step 2 already redrew this character with open hands (Pose for 3D first), so this is skipped.'
    return null
  }
  const changed = visible.filter((p) => JSON.stringify(values[p.name] ?? null) !== JSON.stringify(initial[p.name] ?? null))

  // Add-ons (concept) follow the recipe for this asset's look; the turnaround lists its own helpers read-only.
  const recipe = useRecipe(asset.style_preset, asset.category, asset.platform_target, stage === 'concept' || stage === 'turnaround')
  const addonsParam = visible.find((p) => p.type === 'addons')
  const painter = String(values.model ?? target.params?.model ?? suggestion?.model ?? recipe?.concept.model ?? '')
  // The tested add-ons from the schema plus your own from the live list (GET /api/my-addons).
  const myAddons = useMyAddons(stage === 'concept' && !!addonsParam)
  const rowsFor = (chosen: Record<string, AddonChoice>) => (addonsParam ? addonRows(withMyAddons(addonsParam.catalog ?? [], myAddons), recipe, painter, chosen) : [])
  const rows = rowsFor(addonChoices(values[addonsParam?.name ?? 'addons']))
  const setAddon = (id: string, choice: AddonChoice) => {
    if (!addonsParam) return
    const cur = addonChoices(values[addonsParam.name])
    const next = { ...cur }
    const plain = rowsFor({}).find((r) => r.entry.id === id)
    const recipeChoice: AddonChoice | null = plain ? (plain.on ? plain.strength : 'off') : null
    // Back to what the recipe gives and not chosen before: drop it, so only rows the person changed are sent.
    if (choice === recipeChoice && addonChoices(initial[addonsParam.name])[id] === undefined) delete next[id]
    else next[id] = choice
    set(addonsParam.name, next)
  }
  const turnaroundAddons = stage === 'turnaround' ? (recipe?.addon_entries ?? []).filter((e) => e.stage === 'turnaround') : []
  // How the views are turned (turn_method): the LoRA list and the angle strength follow the choice on screen.
  const turnMethod = (String(values.turn_method ?? target.params?.turn_method ?? 'standard') || 'standard') as TurnMethod

  const schemaHasFlag = visible.some((p) => p.simple !== undefined)
  // The concept step always shows its add-ons section (it says when nothing is tested and offers your own).
  const shown = visible.filter(
    (p) =>
      (p.type !== 'addons' || rows.length > 0 || (stage === 'concept' && !!painter)) &&
      // The angle strength belongs to the standard way of turning the views; the other two load their own LoRA at 1.0.
      !(stage === 'turnaround' && p.name === 'lora_strength' && turnMethod !== 'standard'),
  )
  const simple = shown.filter((p) => p.type !== 'addons' && isSimpleParam(stage, p, schemaHasFlag))
  const advanced = shown.filter((p) => !simple.includes(p))

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

  // The changed settings as they are sent.
  const collect = (): Record<string, unknown> => {
    const params: Record<string, unknown> = {}
    for (const p of changed) params[p.name] = values[p.name]
    // Three-quarter (other side) is added for a character unless second_quarter is false: say which the ticks mean.
    if (stage === 'turnaround' && asset.category === 'humanoid' && Array.isArray(params.views)) params.second_quarter = (params.views as unknown[]).map(String).includes('quarter2')
    // Add-ons: only the rows shown for this painter.
    if (addonsParam && addonsParam.name in params) {
      const ids = new Set(rows.map((r) => r.entry.id))
      params[addonsParam.name] = Object.fromEntries(Object.entries(addonChoices(params[addonsParam.name])).filter(([k]) => ids.has(k)))
    }
    return params
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!changed.length && !submitLabel) return
    onSubmit(changed.length ? collect() : {})
  }
  const pendingKey = changed.length ? JSON.stringify(collect()) : ''
  useEffect(() => {
    onPending?.(pendingKey ? (JSON.parse(pendingKey) as Record<string, unknown>) : null, changed.map((p) => paramPlain(p.name, p.label)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingKey])
  useEffect(() => {
    if (resetNonce) setValues(initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetNonce])
  // "Both" only when there are settings to apply as well as the text.
  const withText = changed.length > 0 && describedChange.trim().length > 0

  const cat = refData?.categories.find((c) => c.id === asset.category)
  const plat = refData?.platforms.find((p) => p.id === asset.platform_target)
  const budget = cat?.budgets?.[asset.platform_target]?.triangles ?? suggestion?.budgets.triangles ?? plat?.triangles_character ?? 20000

  const renderField = (p: AdjustParam, adv: boolean) => (
    <div key={p.name} data-knob={p.name} className={'knob' + (flash === p.name ? ' knob-flash' : '') + (p.name === 'face_yaw' || p.type === 'addons' ? ' knob-wide' : '')}>
      {renderControl(p, adv)}
    </div>
  )

  const renderControl = (p: AdjustParam, adv: boolean) => {
    if (p.type === 'addons') return <AddonsList rows={rows} disabled={disabled} onChange={setAddon} notUsed={addonsNotUsed(target, p.catalog)} painter={stage === 'concept' ? painter : undefined} />
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
      // Each label names the count it sends (after the knob's limits), and two levels that land on the same count
      // show once: the label used to say 15,000 while the limits sent 50,000.
      const lower = clamp(Math.round(budget / 2), p)
      const standard = clamp(budget, p)
      const higher = clamp(budget * 2, p)
      const tris = (n: number) => `${n.toLocaleString('en-US')} triangles`
      const levels: { label: string; value: number | null }[] = [
        ...(p.default === null || p.default === undefined ? [{ label: `Automatic (${budget.toLocaleString('en-US')} for ${stagePlainPlatform(asset.platform_target)})`, value: null }] : []),
        { label: `Lower detail (${tris(lower)}, smaller file)`, value: lower },
        { label: `Standard (${tris(standard)}${standard === budget ? ', the platform budget' : ''})`, value: standard },
        { label: `Higher detail (${tris(higher)}${higher > budget ? ', over budget' : ''})`, value: higher },
      ].filter((l, i, all) => all.findIndex((o) => o.value === l.value) === i)
      // A first build has no current model yet: its starting count is the usual one.
      if (curN !== null && !levels.some((l) => l.value === curN)) levels.push({ label: `${submitLabel ? 'Usual' : 'Current'} (${curN.toLocaleString('en-US')} triangles)`, value: curN })
      return (
        <Field key={p.name} label="Triangle count (detail level)" term="param.target_faces" hint="More triangles mean more detail and a heavier model">
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
    if (stage === 'turnaround' && p.name === 'turn_method') {
      return (
        <AdjustField
          param={{ ...p, options: shownOptions(p, values.motion, values[p.name]) }}
          value={values[p.name]}
          onChange={(v) => set(p.name, v)}
          disabled={disabled}
          hintText={turnMethodHint(turnMethod)}
        />
      )
    }
    // A yes-or-no setting whose empty value means "decided for you" shows that choice as Automatic, not as an unticked box.
    const auto = AUTO_BOOLEANS[p.name]
    if (auto && p.type === 'boolean') {
      const v = values[p.name]
      const now = auto.now?.(target)
      return (
        <Field label={paramPlain(p.name, p.label)} term={`param.${p.name}`} hint={<>{p.hint ?? p.help?.meaning ?? p.description}{now ? <span className="size-now"> {now}</span> : null} <span className="muted">(<span className="mono muted">{p.name}</span>)</span></>}>
          <select value={v === true ? 'on' : v === false ? 'off' : ''} disabled={disabled} onChange={(e) => set(p.name, e.target.value === 'on' ? true : e.target.value === 'off' ? false : null)}>
            <option value="">{auto.label}</option>
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </Field>
      )
    }
    const blocked = blockedReason(p.name)
    return (
      <AdjustField
        param={p.name === 'moves' || p.name === 'hitbox_regions' ? { ...p, options: shownOptions(p, values.motion, values[p.name]) } : p}
        value={blocked && p.type === 'boolean' && p.name === 'hand_edit' ? false : values[p.name]}
        onChange={(v) => set(p.name, v)}
        disabled={disabled || !!blocked}
        blockedReason={blocked}
      />
    )
  }

  // The choices worth offering: the moves this way of moving can make, and the body parts this skeleton has.
  // Anything already chosen stays listed.
  function shownOptions(p: AdjustParam, motion: unknown, chosen?: unknown): NonNullable<AdjustParam['options']> {
    const all = p.options ?? []
    let keep: string[] | null = null
    if (p.options_by_category?.[asset.category]) {
      keep = p.options_by_category[asset.category].map(String)
    } else if (p.name === 'moves') {
      const mo = typeof motion === 'string' && motion ? motion : (MOTION_BY_CATEGORY[asset.category] ?? '')
      keep = (asset.category === 'humanoid' && mo === 'legs' ? MOVES_BY_MOTION.human : MOVES_BY_MOTION[mo]) ?? null
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
            We suggest {suggestion.model_label}, because {plainJargon(suggestion.reason)}; {fmtDuration(totalEstimate(suggestion.estimates, refData))} to a finished asset.
          </span>
        )}
      </div>
      {visible.length === 0 && <div className="muted small">Nothing to fine-tune in this step.</div>}
      {simpleShown.length > 0 && <div className="adjust-grid">{simpleShown.map((p) => renderField(p, false))}</div>}
      {stage === 'concept' && addonsParam && painter && (
        <AddonsHint
          rows={rows}
          painter={painter}
          onShow={() => {
            setAdvOpen(true)
            window.setTimeout(() => formRef.current?.querySelector<HTMLElement>(`[data-knob="${addonsParam.name}"]`)?.scrollIntoView({ block: 'center' }))
          }}
        />
      )}
      {(advanced.length > 0 || turnaroundAddons.length > 0) && (
        <Disclosure label={advanced.length ? `Advanced (${advanced.length} more setting${advanced.length > 1 ? 's' : ''})` : 'Advanced'} open={advOpen} onToggle={setAdvOpen}>
          {advanced.length > 0 && <div className="adjust-grid">{advanced.map((p) => renderField(p, true))}</div>}
          <TurnaroundAddons entries={turnaroundAddons} method={turnMethod} />
        </Disclosure>
      )}
      <div className="row-gap">
        <button type="submit" disabled={disabled || (!changed.length && !submitLabel)} title={submitLabel ? 'Builds this step with the settings shown' : withText ? 'Re-runs with these settings and the change you described' : 'Re-runs with these settings'}>
          {submitLabel ?? (withText ? 'Apply both and re-run' : 'Apply and re-run')}
        </button>
        <button type="button" disabled={!changed.length} onClick={() => setValues(initial)}>
          Reset
        </button>
        <span className="muted small">{withText && changed.length ? 'Your described change goes with it. ' : ''}{changed.length ? `${changed.length} changed: ${changed.map((p) => paramPlain(p.name, p.label)).join(', ')}` : submitLabel ? 'the usual settings for your look and platform' : 'no changes yet'}</span>
      </div>
    </form>
  )
}

// Settings that only work while another setting is on (the backend ignores them otherwise). `clear` turns the
// dependent one off with it; without it the dependent keeps its tick and is shown greyed with the reason.
const DEPENDS_ON: Record<string, { param: string; isOn: (v: unknown) => boolean; reason: string; clear?: boolean }> = {
  neutral_mouth: { param: 'pose_ready', isOn: (v) => v === true, reason: 'Turn on Pose for 3D first to use this.', clear: true },
  hand_triangles: { param: 'head_triangles', isOn: (v) => v !== false, reason: 'Works only while Weighted decimation (more triangles on the head) is on.' },
}

// Yes-or-no settings whose empty value means "decided for you" (the backend picks by category or look), shown as
// Automatic / On / Off so an empty value never looks like Off. `now` says what the version shown actually did.
const AUTO_BOOLEANS: Record<string, { label: string; now?: (v: Version) => string | null }> = {
  source_project: {
    label: 'Automatic (on for characters on smooth looks)',
    now: (v) => (v.stage !== 'prep' || typeof v.metrics?.srcproject_applied !== 'boolean' ? null : v.metrics.srcproject_applied ? 'This version: painted from your pictures.' : 'This version: not painted from your pictures.'),
  },
  head_build: { label: 'Automatic (on with hair from the library)' },
  pbr_fix: {
    label: 'Automatic (on for realistic looks)',
    now: (v) => (v.stage !== 'prep' || typeof v.metrics?.pbr_fix_applied !== 'boolean' ? null : v.metrics.pbr_fix_applied ? 'This version: colours were kept in the real-material range.' : 'This version: colours were left as drawn.'),
  },
  remesh: {
    label: 'Automatic (on, except for scenery)',
    now: (v) => (v.stage !== 'mesh' || typeof v.params?.remesh !== 'boolean' ? null : v.params.remesh ? 'This version: the surface was rebuilt as one closed shell.' : 'This version: the surface was left as built.'),
  },
}

// Whether the side views this 3D step builds on were made with Pose for 3D first: the turnaround above `target` in its
// lineage, or the kept one for a first build. The 3D step skips its own hand redraw then (stages/mesh.py).
function poseReadyUpstream(asset: Asset, target: Version): boolean {
  const all = new Map<string, Version>()
  for (const list of Object.values(asset.stages ?? {})) for (const v of list ?? []) all.set(v.id, v)
  let v: Version | undefined = target
  for (let guard = 0; v && guard < 100; guard++) {
    if (v.stage === 'turnaround') return v.params?.pose_ready === true
    v = v.parent_version_id ? all.get(v.parent_version_id) : undefined
  }
  return selectedVersion(asset, 'turnaround')?.params?.pose_ready === true
}

// A stored add-ons value as {id: choice}, keeping only the four known choices.
function addonChoices(raw: unknown): Record<string, AddonChoice> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: Record<string, AddonChoice> = {}
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) if (v === 'off' || v === 'subtle' || v === 'normal' || v === 'strong') out[k] = v
  return out
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
    case 'addons':
      return {}
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
  blockedReason,
}: {
  param: AdjustParam
  value: unknown
  onChange: (v: unknown) => void
  disabled: boolean
  labelText?: string
  hintText?: ReactNode
  term?: string
  unit?: string
  // Why the setting cannot be used right now; shown before the hint.
  blockedReason?: string | null
}) {
  const label = labelText ?? paramPlain(p.name, p.label)
  const term = termOverride ?? `param.${p.name}`
  const hint = hintText ?? p.hint ?? p.help?.meaning ?? p.description
  const tech = <span className="mono muted">{p.name}</span>
  const hintNode = (
    <>
      {blockedReason ? <span className="knob-blocked">{blockedReason} </span> : null}
      {hint ? (
        <>
          {hint} <span className="muted">({tech})</span>
        </>
      ) : (
        tech
      )}
    </>
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

// What the chosen way of turning the views means for this asset, under the choice.
function turnMethodHint(method: TurnMethod): string {
  if (method === 'sheet') return 'Draws all five sides in one picture so the clothes match. It looks more photographic, so it suits the Photoreal look best. Redrawing one side later uses the standard way.'
  if (method === 'shadow_free') return 'Turns the camera around your picture on a clean white background, with no shadows. Colours match the picture a little less closely.'
  return 'Draws one side at a time and keeps your look. Works for everything.'
}
