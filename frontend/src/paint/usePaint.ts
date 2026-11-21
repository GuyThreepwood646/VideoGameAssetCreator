// The paint state for one loaded model (WP-22): the settings, the warnings, the session with its picture layers and the
// viewer tool. The session is made when painting starts (or the first undo), so opening step 4 costs nothing extra.
import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import type { ViewerModel, ViewerTool } from '../viewerTool'
import { checkPaint, DEFAULT_PAINT, PaintSession, paintableMaps, type PaintSettings, type PaintWarning, type SaveMaps } from './paintSession'

export type PaintState = {
  settings: PaintSettings
  setSettings: (next: Partial<PaintSettings>) => void
  warnings: PaintWarning[]
  blocked: boolean
  // Makes the session for the model (once). Call before painting starts.
  start: () => void
  // The viewer tool for this model, or null before start. The same object each time for the same model.
  toolFor: (model: ViewerModel) => ViewerTool | null
  undo: () => void
  redo: () => void
  resetAll: () => void
  canUndo: boolean
  canRedo: boolean
  undoCount: number
  // The names of the next undo and redo steps (for the buttons and the keys), or null.
  undoLabel: string | null
  redoLabel: string | null
  // True while the picture differs from the last saved one (or from the model's own when nothing was saved).
  dirty: boolean
  // Called after a save: the picture as it is now counts as saved.
  markSaved: () => void
  notice: string | null
  // The pictures a save sends (colour, and shine and metal when the model has them), or null when not saveable yet.
  saveMaps: () => SaveMaps | null
  saveable: boolean
  // Which pictures this model has to paint: shine and metal need the model's metallic-roughness picture (WP-32).
  maps: { shine: boolean; metal: boolean }
}

type Active = { model: ViewerModel; session: PaintSession; tool: ViewerTool; baseline: string }

export function usePaint(model: ViewerModel | null, look: string | null): PaintState {
  const [settings, setSettingsState] = useState<PaintSettings>(DEFAULT_PAINT)
  const settingsRef = useRef(settings)
  useEffect(() => {
    settingsRef.current = settings
  }, [settings])
  const [, bump] = useReducer((n: number) => n + 1, 0)
  const [notice, setNotice] = useState<string | null>(null)
  const [active, setActive] = useState<Active | null>(null)
  const warnings = useMemo(() => (model ? checkPaint(model, look) : []), [model, look])
  const blocked = warnings.some((w) => w.blocks)
  const maps = useMemo(() => (model ? paintableMaps(model) : { shine: false, metal: false }), [model])

  const start = () => {
    if (!model || active?.model === model) return
    const session = new PaintSession(model)
    const tool = session.tool(() => settingsRef.current, {
      picked: (rgb) => {
        setSettingsState((s) => ({ ...s, rgb, mode: 'paint', target: 'colour' }))
        setNotice('Colour picked. Back to the brush.')
      },
      changed: () => bump(),
      renderer: () => model.renderer,
      notice: (text) => setNotice(text),
    })
    setActive({ model, session, tool, baseline: session.editMark })
  }

  return {
    settings,
    setSettings: (next) => setSettingsState((s) => ({ ...s, ...next })),
    warnings,
    blocked,
    start,
    toolFor: (m) => (active && active.model === m ? active.tool : null),
    undo: () => {
      if (!active) return
      active.session.undo(active.model.renderer)
      bump()
    },
    redo: () => {
      if (!active) return
      active.session.redo(active.model.renderer)
      bump()
    },
    resetAll: () => {
      if (!active) return
      active.session.resetAll(active.model.renderer)
      bump()
    },
    canUndo: active?.session.canUndo ?? false,
    canRedo: active?.session.canRedo ?? false,
    undoCount: active?.session.undoCount ?? 0,
    undoLabel: active?.session.undoLabel ?? null,
    redoLabel: active?.session.redoLabel ?? null,
    dirty: !!active && active.session.editMark !== active.baseline,
    markSaved: () => setActive((a) => (a ? { ...a, baseline: a.session.editMark } : a)),
    notice,
    saveMaps: () => active?.session.saveableMaps() ?? null,
    saveable: !!active && active.session.saveableMaps() !== null,
    maps,
  }
}
