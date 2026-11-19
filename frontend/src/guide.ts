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

// The "Build it automatically after creating" checkbox on the asset form (default on).
const AUTOPILOT_KEY = 'vgac.autopilotDefault'

export function autopilotDefault(): boolean {
  try {
    return localStorage.getItem(AUTOPILOT_KEY) !== '0'
  } catch {
    return true
  }
}

export function rememberAutopilotDefault(on: boolean) {
  try {
    localStorage.setItem(AUTOPILOT_KEY, on ? '1' : '0')
  } catch {
    // ignore
  }
}

// How many pictures step 1 (concept) draws: 2 by default, 1 when the person chose it on the empty step 1 card.
// Autopilot always makes two candidates and does not read this.
const CONCEPT_COUNT_KEY = 'vgac.conceptCount'

export function conceptCountDefault(): number {
  try {
    return localStorage.getItem(CONCEPT_COUNT_KEY) === '1' ? 1 : 2
  } catch {
    return 2
  }
}

export function rememberConceptCount(n: number) {
  try {
    localStorage.setItem(CONCEPT_COUNT_KEY, n === 1 ? '1' : '2')
  } catch {
    // ignore
  }
}

// The start button's words for step 1: "Draw 2 pictures" or "Draw 1 picture".
export function conceptCountLabel(n: number): string {
  return n === 1 ? 'Draw 1 picture' : 'Draw 2 pictures'
}
