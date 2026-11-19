// Settings for each step of an automatic build, chosen before it starts. Pure functions only (the form is
// StepSettingsChoice): which steps show, what each setting starts at, and which values are sent.
import type { AdjustParam, StageId, StageInfo } from '../api'
import { knobShown } from '../help'
import { isEditKnob } from '../editLoras'

// What the autopilot start sends: only the settings that differ from their starting values, per step.
export type StepParams = Partial<Record<StageId, Record<string, unknown>>>

// The steps a category runs: the category's skips (material skips turnaround, prep and rig) are left out, and so is
// a step with no settings to choose for this category.
export function stepsWithSettings(stages: StageInfo[], category: string, skips: readonly string[] = []): StageInfo[] {
  return stages.filter((s) => !skips.includes(s.id) && shownSettings(s.adjust_schema ?? [], category).length > 0)
}

// The value a setting starts at for this kind of asset (the same rule as the Fine-tune form): a per-category default
// wins, then the knob's own default, then a plain empty value for its type.
export function defaultFor(p: AdjustParam): unknown {
  if (isEditKnob(p.type)) return {}
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

export function startingValue(p: AdjustParam, category: string): unknown {
  const catDefault = p.by_category?.[category]?.default
  if (catDefault !== undefined) return catDefault
  return p.default !== undefined ? p.default : defaultFor(p)
}

// The settings of a step that this category shows (add-ons are chosen in the concept step's own form, not here).
export function shownSettings(schema: AdjustParam[], category: string): AdjustParam[] {
  return schema.filter((p) => p.type !== 'addons' && knobShown(p, category))
}

// Only the values the person changed, per step. A value changed and then set back to its start is not sent.
// `edits` holds what the person typed or picked, by step and setting name; a null edit means "automatic".
export function changedStepParams(steps: StageInfo[], category: string, edits: StepParams): StepParams {
  const out: StepParams = {}
  for (const step of steps) {
    const chosen = edits[step.id as StageId]
    if (!chosen) continue
    for (const p of shownSettings(step.adjust_schema ?? [], category)) {
      if (!Object.prototype.hasOwnProperty.call(chosen, p.name)) continue
      const value = chosen[p.name]
      if (JSON.stringify(value ?? null) === JSON.stringify(startingValue(p, category) ?? null)) continue
      out[step.id as StageId] = { ...(out[step.id as StageId] ?? {}), [p.name]: value }
    }
  }
  return out
}

// The settings of a step that are still at the person's saved default (shown in the form and not changed since), by
// name. `applied` is what the backend says to use for this look and platform (saved values brought down to its limits).
export function savedInUse(steps: StageInfo[], category: string, edits: StepParams, applied: StepParams): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const step of steps) {
    const mine = applied[step.id as StageId]
    if (!mine) continue
    for (const p of shownSettings(step.adjust_schema ?? [], category)) {
      if (!Object.prototype.hasOwnProperty.call(mine, p.name)) continue
      const current = Object.prototype.hasOwnProperty.call(edits[step.id as StageId] ?? {}, p.name) ? edits[step.id as StageId]?.[p.name] : startingValue(p, category)
      if (JSON.stringify(current ?? null) === JSON.stringify(mine[p.name] ?? null)) (out[step.id] ??= []).push(p.name)
    }
  }
  return out
}

// Two sets of step settings as one: the second one wins for a setting both name.
export function mergeStepParams(...parts: (StepParams | undefined | null)[]): StepParams {
  const out: StepParams = {}
  for (const part of parts) {
    if (!part) continue
    for (const [stage, params] of Object.entries(part) as [StageId, Record<string, unknown>][]) {
      out[stage] = { ...(out[stage] ?? {}), ...params }
    }
  }
  return out
}
