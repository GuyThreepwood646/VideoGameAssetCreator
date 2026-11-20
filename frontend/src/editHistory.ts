import { useCallback, useEffect, useState } from 'react'

// Undo and redo for the hand editors (body marks, item attachments, hanging points, touch-up painting). Each editor
// keeps its own history of steps. A step says what it was called ("move the left knee") and how to put the earlier
// state back (undo) and the later one (redo). The editor saves through its own existing calls; this file only keeps
// the order and the names.

export const HISTORY_LIMIT = 50
// Small repeated changes to one item (arrow-key nudges) within this many milliseconds become one step.
export const MERGE_MS = 1000

export type EditStepInput = {
  label: string
  // Steps with the same key that follow each other within MERGE_MS merge into one.
  key?: string
  undo: () => Promise<void> | void
  redo: () => Promise<void> | void
}

type Step = EditStepInput & { at: number }

export class EditHistory {
  private done: Step[] = []
  private undone: Step[] = []
  private inFlight = false
  private listeners = new Set<() => void>()
  lastError: unknown = null
  now: () => number

  constructor(now: () => number = () => Date.now()) {
    this.now = now
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private emit() {
    for (const fn of this.listeners) fn()
  }

  get canUndo(): boolean {
    return this.done.length > 0 && !this.inFlight
  }

  get canRedo(): boolean {
    return this.undone.length > 0 && !this.inFlight
  }

  get busy(): boolean {
    return this.inFlight
  }

  get undoLabel(): string | null {
    return this.done[this.done.length - 1]?.label ?? null
  }

  get redoLabel(): string | null {
    return this.undone[this.undone.length - 1]?.label ?? null
  }

  get size(): number {
    return this.done.length
  }

  // Record a change that has already been saved. Returns false when a save is in flight (no step is made then).
  record(input: EditStepInput): boolean {
    if (this.inFlight) return false
    const at = this.now()
    const top = this.done[this.done.length - 1]
    if (input.key && top && top.key === input.key && at - top.at < MERGE_MS) {
      // Same item, moved again straight away: one step that goes from the first earlier state to the latest.
      top.redo = input.redo
      top.label = input.label
      top.at = at
    } else {
      this.done.push({ ...input, at })
      if (this.done.length > HISTORY_LIMIT) this.done.splice(0, this.done.length - HISTORY_LIMIT)
    }
    // A new change cancels the redo steps.
    this.undone = []
    this.emit()
    return true
  }

  // Undo the latest step. If the put-back fails, the step stays where it was (not undone), the error is kept for the
  // panel to show, and the editor should reload what is saved.
  async undo(): Promise<boolean> {
    if (this.inFlight || this.done.length === 0) return false
    const step = this.done[this.done.length - 1]
    this.inFlight = true
    this.lastError = null
    this.emit()
    try {
      await step.undo()
      this.done.pop()
      this.undone.push(step)
      return true
    } catch (e) {
      this.lastError = e
      return false
    } finally {
      this.inFlight = false
      this.emit()
    }
  }

  async redo(): Promise<boolean> {
    if (this.inFlight || this.undone.length === 0) return false
    const step = this.undone[this.undone.length - 1]
    this.inFlight = true
    this.lastError = null
    this.emit()
    try {
      await step.redo()
      this.undone.pop()
      this.done.push(step)
      return true
    } catch (e) {
      this.lastError = e
      return false
    } finally {
      this.inFlight = false
      this.emit()
    }
  }

  clear() {
    this.done = []
    this.undone = []
    this.lastError = null
    this.emit()
  }
}

// Anything with an undo and a redo can be the editor the keys act on: a history, or the paint session's own undo.
export type KeyTarget = { undo: () => unknown; redo: () => unknown }

// The editor the keys act on: the one most recently used on the page.
let active: KeyTarget | null = null
export function setActiveEditor(h: KeyTarget | null) {
  active = h
}
// Clears the active editor when it is this one (on unmount).
export function clearActiveEditor(h: KeyTarget) {
  if (active === h) active = null
}
export function activeEditor(): KeyTarget | null {
  return active
}

// Keys: Ctrl+Z undo; Ctrl+Y or Ctrl+Shift+Z redo (Cmd on a Mac). Never inside a text field, select or contenteditable,
// so the browser's own undo stays there.
export function isTextTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  return !!el && (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || !!el.isContentEditable)
}

let keysInstalled = 0
function onKey(e: KeyboardEvent) {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return
  if (isTextTarget(e.target)) return
  const k = e.key.toLowerCase()
  const h = active
  if (!h) return
  if (k === 'z' && !e.shiftKey) {
    e.preventDefault()
    void h.undo()
  } else if (k === 'y' || (k === 'z' && e.shiftKey)) {
    e.preventDefault()
    void h.redo()
  }
}

function installKeys() {
  if (keysInstalled++ === 0) window.addEventListener('keydown', onKey)
}
function removeKeys() {
  if (--keysInstalled === 0) window.removeEventListener('keydown', onKey)
}

// The editor's history for a React panel. Re-renders when steps change. Mark the editor active with
// `activate` on pointer down or focus inside its panel.
// The keys, for an editor that keeps its own undo (the paint session): they act on whichever editor was used last.
export function useEditKeys() {
  useEffect(() => {
    installKeys()
    return () => removeKeys()
  }, [])
}

export function useEditHistory() {
  const [hist] = useState(() => new EditHistory())
  const [, bump] = useState(0)
  useEffect(() => hist.subscribe(() => bump((n) => n + 1)), [hist])
  useEffect(() => {
    installKeys()
    return () => {
      if (active === hist) active = null
      removeKeys()
    }
  }, [hist])
  const activate = useCallback(() => setActiveEditor(hist), [hist])
  return { history: hist, activate }
}
