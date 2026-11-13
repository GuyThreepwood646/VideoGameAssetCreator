// Ready-made looks (style presets) from GET /api/style-presets, fetched once and
// shared. A backend without the endpoint (404) marks them unavailable; the
// screens then show the raw style lock fields with a note.
import { useEffect, useState } from 'react'
import { api, ApiError, type LookPreset, type StyleLock } from './api'

export type PresetsState = { presets: LookPreset[] | null; unavailable: boolean; error: unknown }

let cached: Promise<LookPreset[]> | null = null

export function loadPresets(): Promise<LookPreset[]> {
  if (!cached) {
    cached = api.stylePresets()
    cached.catch(() => {
      cached = null
    })
  }
  return cached
}

export function usePresets(): PresetsState {
  const [state, setState] = useState<PresetsState>({ presets: null, unavailable: false, error: null })
  useEffect(() => {
    let alive = true
    loadPresets().then(
      (presets) => alive && setState({ presets, unavailable: false, error: null }),
      (e) => alive && setState({ presets: null, unavailable: e instanceof ApiError && e.status === 404, error: e instanceof ApiError && e.status === 404 ? null : e }),
    )
    return () => {
      alive = false
    }
  }, [])
  return state
}

export function presetById(presets: LookPreset[] | null | undefined, id: string | null | undefined): LookPreset | null {
  if (!presets || !id) return null
  return presets.find((p) => p.id === id) ?? null
}

// The style lock a preset fills in (keeps the preset id on the lock).
export function lockFromPreset(p: LookPreset): StyleLock {
  return {
    prompt_block: p.style_lock.prompt_block ?? '',
    model: p.style_lock.model,
    loras: [...(p.style_lock.loras ?? [])],
    palette: [...(p.style_lock.palette ?? [])],
    negative: p.style_lock.negative ?? '',
    preset: p.id,
  }
}

// Whether the lock still matches its preset (false once edited by hand).
export function lockMatchesPreset(lock: StyleLock, p: LookPreset | null): boolean {
  if (!p) return false
  const a = lockFromPreset(p)
  return (
    a.prompt_block.trim() === lock.prompt_block.trim() &&
    a.model === lock.model &&
    a.negative.trim() === lock.negative.trim() &&
    JSON.stringify(a.loras) === JSON.stringify(lock.loras) &&
    JSON.stringify(a.palette.map((c) => c.toLowerCase())) === JSON.stringify(lock.palette.map((c) => c.toLowerCase()))
  )
}

// The look name for a style id from the asset form's list. Looks and styles are one idea: the person only ever
// sees the look name. A style no look maps to is shown as a custom look, never as a model name (UI-01).
export function lookLabelForStyle(presets: LookPreset[] | null | undefined, styleId: string | null | undefined, fallback?: string): string {
  const hit = (presets ?? []).find((p) => p.style_preset === styleId)
  return hit?.label ?? fallback ?? 'Custom look'
}

// The look a project's pipeline settings come from: its own look, else the one a custom look is based on, else the look
// whose painter it uses, else the first look. One id, because a look and a style are one list (UI-01).
export function baseLookId(presets: LookPreset[] | null | undefined, lock: StyleLock): string | null {
  const list = presets ?? []
  if (lock.preset && list.some((p) => p.id === lock.preset)) return lock.preset
  if (lock.based_on && list.some((p) => p.id === lock.based_on)) return lock.based_on
  return (list.find((p) => p.style_lock.model === lock.model) ?? list[0])?.id ?? null
}

// The look the new asset form should start with: the project's, once the looks have loaded (or are known not to be
// coming). Before that it is null, so the form never holds a look the person did not choose (a list's first entry).
export function projectStyleFor(presets: LookPreset[] | null, settled: boolean, lock: StyleLock, firstStyle: string | null | undefined): string | null {
  if (presets) return baseLookId(presets, lock) ?? firstStyle ?? null
  return settled ? (lock.preset ?? firstStyle ?? null) : null
}

// The look on the new asset form and whether the person picked it themselves.
export type LookChoice = { style: string; chosen: boolean }

// What the form's look should be now: the person's own pick stays; otherwise it follows the project's look, whenever
// that becomes known or changes.
export function syncLook(cur: LookChoice, projectStyleId: string | null): LookChoice {
  if (cur.chosen || !projectStyleId || cur.style === projectStyleId) return cur
  return { style: projectStyleId, chosen: false }
}

// Whether the asset is being made in a look other than its project's, on the person's choice.
export function differentLook(cur: LookChoice, projectStyleId: string | null): boolean {
  return cur.chosen && !!projectStyleId && !!cur.style && cur.style !== projectStyleId
}
