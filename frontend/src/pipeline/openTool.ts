// A Fix that opens a tool (plan 2.5). Instead of re-running a stage, the page goes to the fix's stage, opens
// "Change something" and focuses the named editor or Fine-tune setting, with the fix's params filled in.
// Nothing runs until the person applies it there. The request travels in the router's location state, so it
// works from any card (checks, the autopilot's paused card, the finish card) and across stages.
import type { StageId } from '../api'
import type { FixAction } from '../help'

export type ToolFocus = { ui?: string; knob?: string; params: Record<string, unknown>; nonce: number }
type ToolState = { openTool: ToolFocus & { stage: StageId } }

export function toolPath(assetId: string, fix: FixAction): { to: string; state: ToolState } {
  return { to: `/assets/${assetId}/${fix.stage}`, state: { openTool: { stage: fix.stage, ui: fix.ui, knob: fix.knob, params: fix.params ?? {}, nonce: Date.now() } } }
}

export function readToolState(state: unknown, stage: StageId): ToolFocus | null {
  const t = (state as Partial<ToolState> | null)?.openTool
  if (!t || t.stage !== stage) return null
  return { ui: t.ui, knob: t.knob, params: t.params ?? {}, nonce: t.nonce ?? Date.now() }
}
