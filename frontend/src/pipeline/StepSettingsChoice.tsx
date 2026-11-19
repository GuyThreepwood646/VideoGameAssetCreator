// "Choose each step's settings": the settings of every step an automatic build runs, collapsed under the choice to
// build automatically. Only what the person changes is reported (onChange gets the changed values per step). The form
// starts from the person's saved defaults for this platform and look, and says so; "Save as my defaults" keeps what
// is in the form for next time.
import { useEffect, useRef, useState } from 'react'
import { api, STAGES, type AdjustParam, type StageId, type StageInfo, type StepDefaults } from '../api'
import { Disclosure } from '../components/ui'
import { isSimpleParam, paramPlain, stagePlain } from '../help'
import { useRefData } from '../refdata'
import { AdjustField } from './FineTune'
import { changedStepParams, savedInUse, shownSettings, startingValue, stepsWithSettings, type StepParams } from './stepSettings'

export function StepSettingsChoice({ category, platform, look, stages, disabled = false, onChange }: { category: string; platform: string; look: string; stages: StageInfo[] | null; disabled?: boolean; onChange: (params: StepParams) => void }) {
  const { ref } = useRefData()
  const [edits, setEdits] = useState<StepParams>({})
  const [saved, setSaved] = useState<StepDefaults | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // The buttons wait until the saved defaults have been read, so a save never starts from a form that has not loaded them.
  const [loaded, setLoaded] = useState(false)
  const keyNow = useRef('')
  keyNow.current = `${category}|${platform}|${look}`
  // The saved defaults for this platform and look fill the form; a different platform or look starts over.
  useEffect(() => {
    setSaved(null)
    setNote(null)
    setEdits({})
    setLoaded(false)
    if (!category || !platform || !look) return
    let alive = true
    api.getStepDefaults(platform, look, category).then(
      (d) => {
        if (!alive) return
        setSaved(d)
        setEdits(d.applied as StepParams)
        setLoaded(true)
      },
      () => setNote('Your saved defaults could not be read, so saving is off for now.'),
    )
    return () => {
      alive = false
    }
  }, [category, platform, look])
  // The steps this category does not run (material: turnaround, prep, rig) are left out, as the backend leaves them out.
  const skips = ref?.categories.find((c) => c.id === category)?.skips ?? []
  const steps = stepsWithSettings(stages ?? [], category, skips)
  const changed = changedStepParams(steps, category, edits)
  const key = JSON.stringify(changed)
  // Reports the changed values whenever they change; the key keeps this from repeating on every render.
  useEffect(() => {
    onChange(changed)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (steps.length === 0) return null
  const set = (stage: StageId, name: string, value: unknown) =>
    setEdits((cur) => ({ ...cur, [stage]: { ...(cur[stage] ?? {}), [name]: value } }))
  const hasSaved = Object.keys(saved?.saved ?? {}).length > 0
  const inUse = savedInUse(steps, category, edits, (saved?.applied ?? {}) as StepParams)
  const run = async (work: () => Promise<StepDefaults>, done: string) => {
    const key = keyNow.current
    setSaving(true)
    setNote(null)
    try {
      const d = await work()
      if (key !== keyNow.current) return
      setSaved(d)
      setEdits(d.applied as StepParams)
      setNote(done)
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'That did not work. Try again.')
    } finally {
      setSaving(false)
    }
  }
  const saveDefaults = () => {
    // One setting at a time: what is still at its saved (or usual) value is left alone, what went back to the usual
    // value is removed (null), the rest is set. Settings this kind of asset does not show are never touched.
    const stagesToSave: Partial<Record<StageId, Record<string, unknown>>> = {}
    for (const step of steps) {
      const id = step.id as StageId
      const out: Record<string, unknown> = {}
      for (const p of shownSettings(step.adjust_schema ?? [], category)) {
        const has = Object.prototype.hasOwnProperty
        const usual = startingValue(p, category)
        const was = has.call(saved?.applied?.[id] ?? {}, p.name) ? saved?.applied?.[id]?.[p.name] : usual
        const now = has.call(edits[id] ?? {}, p.name) ? edits[id]?.[p.name] : usual
        if (JSON.stringify(now ?? null) === JSON.stringify(was ?? null)) continue
        out[p.name] = JSON.stringify(now ?? null) === JSON.stringify(usual ?? null) ? null : now
      }
      if (Object.keys(out).length > 0) stagesToSave[id] = out
    }
    return run(() => api.saveStepDefaults(platform, look, category, stagesToSave), 'Saved. New assets with this platform and look start from these settings.')
  }
  const clearDefaults = () => run(() => api.clearStepDefaults(platform, look, category), 'Your saved defaults for this platform and look are cleared.')

  return (
    <Disclosure label="Choose each step's settings" className="step-settings">
      <div className="col">
        <p className="muted small">
          Each step starts at its usual settings{hasSaved ? ', or at your saved defaults for this platform and look' : ''}. Change only what you want. A step that already has a kept result keeps it, so its settings are not used.
        </p>
        {(saved?.notes ?? []).map((n) => (
          <p key={n} className="muted small">
            {n}
          </p>
        ))}
        {steps.map((step) => {
          const stage = step.id as StageId
          const schema: AdjustParam[] = shownSettings(step.adjust_schema, category)
          const flagged = step.adjust_schema.some((p) => p.simple !== undefined)
          const simple = schema.filter((p) => isSimpleParam(stage, p, flagged))
          const more = schema.filter((p) => !simple.includes(p))
          const valueOf = (p: AdjustParam) => (Object.prototype.hasOwnProperty.call(edits[stage] ?? {}, p.name) ? edits[stage]?.[p.name] : startingValue(p, category))
          const field = (p: AdjustParam) => <AdjustField key={p.name} param={p} value={valueOf(p)} disabled={disabled} onChange={(v) => set(stage, p.name, v)} />
          return (
            <section key={stage} className="step-settings-group">
              <strong>
                Step {STAGES.indexOf(stage) + 1}: {stagePlain(stage, step)}
              </strong>
              {(inUse[stage]?.length ?? 0) > 0 && (
                <p className="muted small">
                  Your saved default: {inUse[stage].map((n) => paramPlain(n, schema.find((p) => p.name === n)?.label)).join(', ')}.
                </p>
              )}
              {simple.length > 0 && <div className="adjust-grid">{simple.map(field)}</div>}
              {more.length > 0 && (
                <Disclosure label="More settings">
                  <div className="adjust-grid">{more.map(field)}</div>
                </Disclosure>
              )}
            </section>
          )
        })}
        <div className="row">
          <button type="button" className="small" disabled={disabled || saving || !loaded} onClick={saveDefaults} title="Keeps these settings for new assets with this platform and look">
            Save as my defaults
          </button>
          {hasSaved && (
            <button type="button" className="small" disabled={disabled || saving || !loaded} onClick={clearDefaults}>
              Clear my defaults
            </button>
          )}
        </div>
        {note && (
          <p className="muted small" role="status">
            {note}
          </p>
        )}
      </div>
    </Disclosure>
  )
}
