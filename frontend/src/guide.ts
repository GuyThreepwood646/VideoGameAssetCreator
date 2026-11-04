// First-run guide state: shown once (localStorage flag) and re-openable from the header.
import { useEffect, useState } from 'react'

const KEY = 'vgac.guideSeen'
const listeners = new Set<(open: boolean) => void>()
let openNow = false

function safeGet(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return true
  }
}

function safeSet() {
  try {
    localStorage.setItem(KEY, '1')
  } catch {
    // storage blocked; the overlay simply shows again next load
  }
}

export function openGuide() {
  openNow = true
  for (const fn of listeners) fn(true)
}

export function closeGuide() {
  openNow = false
  safeSet()
  for (const fn of listeners) fn(false)
}

export function useGuide(): boolean {
  const [open, setOpen] = useState(() => {
    if (!openNow && !safeGet()) openNow = true
    return openNow
  })
  useEffect(() => {
    listeners.add(setOpen)
    return () => {
      listeners.delete(setOpen)
    }
  }, [])
  return open
}

// Per-asset preferred concept model chosen on the creation form (the create
// endpoint has no model field, so it is applied to the first concept run).
const MODEL_KEY = 'vgac.conceptModel.'

export function rememberConceptModel(assetId: string, model: string | null) {
  try {
    if (model) localStorage.setItem(MODEL_KEY + assetId, model)
    else localStorage.removeItem(MODEL_KEY + assetId)
  } catch {
    // ignore
  }
}

export function preferredConceptModel(assetId: string): string | null {
  try {
    return localStorage.getItem(MODEL_KEY + assetId)
  } catch {
    return null
  }
}
