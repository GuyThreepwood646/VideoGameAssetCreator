// "Choose each step's settings": the settings of every step an automatic build runs, collapsed under the choice to
// build automatically. Only what the person changes is reported (onChange gets the changed values per step).
import { useEffect, useState } from 'react'
import { STAGES, type AdjustParam, type StageId, type StageInfo } from '../api'
import { Disclosure } from '../components/ui'
import { isSimpleParam, stagePlain } from '../help'
import { useRefData } from '../refdata'
import { AdjustField } from './FineTune'
import { changedStepParams, shownSettings, startingValue, stepsWithSettings, type StepParams } from './stepSettings'

export function StepSettingsChoice({ category, stages, disabled = false, onChange }: { category: string; stages: StageInfo[] | null; disabled?: boolean; onChange: (params: StepParams) => void }) {
  const { ref } = useRefData()
  const [edits, setEdits] = useState<StepParams>({})
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

  return (
    <Disclosure label="Choose each step's settings" className="step-settings">
      <div className="col">
        <p className="muted small">Each step starts at its usual settings. Change only what you want. A step that already has a kept result keeps it, so its settings are not used.</p>
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
              {simple.length > 0 && <div className="adjust-grid">{simple.map(field)}</div>}
              {more.length > 0 && (
                <Disclosure label="More settings">
                  <div className="adjust-grid">{more.map(field)}</div>
                </Disclosure>
              )}
            </section>
          )
        })}
      </div>
    </Disclosure>
  )
}
