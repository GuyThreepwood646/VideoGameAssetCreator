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
