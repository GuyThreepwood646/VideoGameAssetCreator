// Unsaved paint (WP-71): the paint on a model that has not been saved as a version. Any place that would leave the
// picture (another step, another version, closing the tab) asks first. Each painting panel says whether it has
// unsaved paint; the registry is the one place that answers.

const owners = new Set<string>()

export function setUnsavedPaint(owner: string, unsaved: boolean): void {
  if (unsaved) owners.add(owner)
  else owners.delete(owner)
}

export function hasUnsavedPaint(): boolean {
  return owners.size > 0
}

export const UNSAVED_PAINT_MESSAGE = 'The paint on this model is not saved. Leave anyway? Paint that is not saved as a new version is lost.'

// True when it is fine to leave: nothing unsaved, or the person says leave anyway.
export function confirmLeaveUnsavedPaint(): boolean {
  if (!hasUnsavedPaint()) return true
  return window.confirm(UNSAVED_PAINT_MESSAGE)
}
