// Reference tables (categories, styles, platforms, stages) fetched once and shared.
import { useEffect, useState } from 'react'
import { api, type Category, type Platform, type StageInfo, type StylePreset } from './api'
import { rememberCategories } from './help'

export type RefData = { categories: Category[]; styles: StylePreset[]; platforms: Platform[]; stages: StageInfo[] }

let cached: Promise<RefData> | null = null

export function loadRefData(): Promise<RefData> {
  if (!cached) {
    cached = Promise.all([api.categories(), api.styles(), api.platforms(), api.stages()]).then(([categories, styles, platforms, stages]) => {
      rememberCategories(categories)
      return { categories, styles, platforms, stages }
    })
    cached.catch(() => {
      cached = null
    })
  }
  return cached
}

export function useRefData(): { ref: RefData | null; error: unknown } {
  const [ref, setRef] = useState<RefData | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let alive = true
    loadRefData().then(
      (r) => alive && setRef(r),
      (e) => alive && setError(e),
    )
    return () => {
      alive = false
    }
  }, [])
  return { ref, error }
}

export function platformLabel(ref: RefData | null, id: string): string {
  return ref?.platforms.find((p) => p.id === id)?.label ?? id
}

export function categoryLabel(ref: RefData | null, id: string): string {
  return ref?.categories.find((c) => c.id === id)?.label ?? id
}
