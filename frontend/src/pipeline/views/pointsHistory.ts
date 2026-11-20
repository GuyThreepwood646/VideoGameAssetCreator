import type { PlacedPart } from '../../api'
import type { EditHistory } from '../../editHistory'

// Undo and redo for the hanging and stiff points (WP-71). One saved change is one step, named for the person. Undo
// saves the list from before the change, and redo saves the list the change kept, both through the existing parts PUT
// (the `save` function). `show` puts the server's answer on the screen.
export async function savePointsStep(opts: {
  history: EditHistory
  label: string
  before: PlacedPart[]
  next: PlacedPart[]
  save: (parts: PlacedPart[]) => Promise<PlacedPart[]>
  show: (parts: PlacedPart[]) => void
}): Promise<void> {
  const saved = await opts.save(opts.next)
  opts.show(saved)
  opts.history.record({
    label: opts.label,
    undo: async () => opts.show(await opts.save(opts.before)),
    redo: async () => opts.show(await opts.save(saved)),
  })
}
