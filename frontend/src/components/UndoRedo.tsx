import type { EditHistory } from '../editHistory'

// Undo and Redo buttons for one editor. The tooltip names the step; they are disabled when there is nothing to undo or
// redo, or while a save is in flight.
export function UndoRedoButtons({ history }: { history: EditHistory }) {
  return (
    <span className="undo-redo" role="group" aria-label="Undo and redo">
      <button
        type="button"
        disabled={!history.canUndo}
        title={history.undoLabel ? `Undo: ${history.undoLabel} (Ctrl+Z)` : 'Nothing to undo'}
        onClick={() => void history.undo()}
      >
        Undo
      </button>
      <button
        type="button"
        disabled={!history.canRedo}
        title={history.redoLabel ? `Redo: ${history.redoLabel} (Ctrl+Y)` : 'Nothing to redo'}
        onClick={() => void history.redo()}
      >
        Redo
      </button>
    </span>
  )
}
