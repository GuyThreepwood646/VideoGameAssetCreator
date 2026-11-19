// "Start this step over": the plain rules behind its button and form. No React here, so the rules can be tested alone.
import type { AdjustParam } from '../api'
import type { GuideState } from './GuidePanel'

// The button's shape in each state. `reason` is shown next to a button that cannot be used, in plain words.
export type StartOverState = { kind: 'open' } | { kind: 'stop' } | { kind: 'disabled'; reason: string } | { kind: 'link'; reason: string }

export function startOverState(o: { guide: GuideState; autopilotActive: boolean; canRun: boolean; prevName: string | null }): StartOverState {
  // An automatic build owns the steps while it runs: it ignores a new request, so the button waits for it.
  if (o.autopilotActive) return { kind: 'disabled', reason: 'An automatic build is running. Stop it first, then start this step over.' }
  // The step before has no kept result yet: go there instead of running this step on nothing.
  if (!o.canRun) return { kind: 'link', reason: `${o.prevName ? `First keep a result of "${o.prevName}".` : 'First keep a result of the step before.'}` }
  // A run is going: it has to stop first (the stopped version stays in the history, not kept).
  if (o.guide === 'running') return { kind: 'stop' }
  return { kind: 'open' }
}

// A setting that picks a random start (a seed, or a seed for one part such as a face) is never copied from a version:
// the run then draws a new one.
export function isSeedName(name: string): boolean {
  return /(^|_)seed$/.test(name)
}

// The settings "Try again" sends: the version's own values, only for settings in this step's schema, with every seed
// left out. Stored params also hold internal markers (refine_of, instruction, supplied_views) that are not settings.
export function freshParams(params: Record<string, unknown> | null | undefined, schema: AdjustParam[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (!params) return out
  for (const p of schema) {
    if (isSeedName(p.name) || !Object.prototype.hasOwnProperty.call(params, p.name)) continue
    out[p.name] = params[p.name]
  }
  return out
}
