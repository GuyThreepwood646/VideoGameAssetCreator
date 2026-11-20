// Touch up (WP-22, saving WP-23): paint on the model's colour picture in step 4. Save makes a new clean-up version with
// the paint; until then the paint stays in this page.
import { BRUSH_MAX, BRUSH_MIN, type PaintMode, type PaintSettings } from '../../paint/paintSession'
import type { PaintState } from '../../paint/usePaint'

function hexOf(rgb: [number, number, number]): string {
  return '#' + rgb.map((c) => c.toString(16).padStart(2, '0')).join('')
}
function rgbOf(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

const MODES: { id: PaintMode; label: string }[] = [
  { id: 'paint', label: 'Brush' },
  { id: 'erase', label: 'Eraser' },
  { id: 'pick', label: 'Eyedropper' },
]

function edgeWord(hardness: number): string {
  if (hardness >= 0.95) return 'hard'
  if (hardness <= 0.05) return 'soft'
  return 'medium'
}

export function TouchUp({ paint, painting, onStart, onStop, onSave, saving, saveNote }: { paint: PaintState; painting: boolean; onStart: () => void; onStop: () => void; onSave: () => void; saving: boolean; saveNote: string | null }) {
  const s: PaintSettings = paint.settings
  const cm = Math.round(s.radius * 100)
  return (
    <div className="touch-up" data-testid="touch-up">
      <div className="field-label">Touch up</div>
      <div className="small muted">
        Paint on the colour picture of the model. Save as a new version to keep it: the new version carries the paint on to the rig and the export. Until you save, the paint stays in this page and is lost when you leave it.
      </div>
      {paint.warnings.map((w) => (
        <div key={w.text} className={w.blocks ? 'warn-text' : 'small'}>
          {w.text}
        </div>
      ))}
      {!paint.blocked && (
        <>
          <div className="touch-up-row">
            {!painting ? (
              <button
                type="button"
                className="btn primary"
                onClick={() => {
                  paint.start()
                  onStart()
                }}
              >
                Start painting
              </button>
            ) : (
              <button type="button" className="btn" onClick={onStop}>
                Stop painting
              </button>
            )}
            {painting && <span className="small muted">Click or drag on the model. Right-drag turns it. Press Escape to stop.</span>}
          </div>
          <div className="touch-up-row">
            <span className="segmented" role="radiogroup" aria-label="What the brush does">
              {MODES.map((m) => (
                <button key={m.id} type="button" role="radio" aria-checked={s.mode === m.id} className={s.mode === m.id ? 'active' : ''} onClick={() => paint.setSettings({ mode: m.id })}>
                  {m.label}
                </button>
              ))}
            </span>
            {s.mode !== 'erase' && (
              <label className="small">
                Colour{' '}
                <input type="color" value={hexOf(s.rgb)} onChange={(e) => paint.setSettings({ rgb: rgbOf(e.target.value) })} aria-label="Brush colour" />
              </label>
            )}
          </div>
          <div className="touch-up-row">
            <label className="small">
              Size {cm} cm on the model{' '}
              <input type="range" min={Math.round(BRUSH_MIN * 100)} max={Math.round(BRUSH_MAX * 100)} value={cm} onChange={(e) => paint.setSettings({ radius: Number(e.target.value) / 100 })} aria-label="Brush size in centimetres" />
            </label>
            <label className="small">
              Edge {edgeWord(s.hardness)}{' '}
              <input type="range" min={0} max={100} value={Math.round(s.hardness * 100)} onChange={(e) => paint.setSettings({ hardness: Number(e.target.value) / 100 })} aria-label="Edge: soft to hard" />
            </label>
            <label className="small">
              Strength {Math.round(s.strength * 100)}%{' '}
              <input type="range" min={10} max={100} value={Math.round(s.strength * 100)} onChange={(e) => paint.setSettings({ strength: Number(e.target.value) / 100 })} aria-label="Strength" />
            </label>
          </div>
          <div className="touch-up-row small">
            <label>
              <input type="checkbox" checked={s.faceOnly} onChange={(e) => paint.setSettings({ faceOnly: e.target.checked })} /> Only the part I click (one triangle)
            </label>
            <label>
              <input type="checkbox" checked={s.mirror} onChange={(e) => paint.setSettings({ mirror: e.target.checked })} /> Mirror across the middle (left and right)
            </label>
          </div>
          <div className="touch-up-row">
            <button type="button" className="btn" disabled={!paint.canUndo} onClick={paint.undo} title={paint.undoLabel ? `Undo: ${paint.undoLabel} (Ctrl+Z)` : 'Nothing to undo'}>
              Undo{paint.undoCount ? ` (${paint.undoCount})` : ''}
            </button>
            <button type="button" className="btn" disabled={!paint.canRedo} onClick={paint.redo} title={paint.redoLabel ? `Redo: ${paint.redoLabel} (Ctrl+Y)` : 'Nothing to redo'}>
              Redo
            </button>
            {(paint.undoLabel || paint.redoLabel) && (
              <span className="small muted">
                {paint.undoLabel ? `Undo: ${paint.undoLabel}.` : ''} {paint.redoLabel ? `Redo: ${paint.redoLabel}.` : ''}
              </span>
            )}
            <button type="button" className="btn" onClick={paint.resetAll}>
              Back to the original picture
            </button>
          </div>
          <div className="touch-up-row">
            <button type="button" className="btn primary" disabled={!paint.saveable || !paint.canUndo || saving} onClick={onSave}>
              {saving ? 'Saving the paint' : 'Save as a new version'}
            </button>
            {!paint.saveable && <span className="small muted">Save is not available yet for this model: some of its parts have pictures of their own.</span>}
            {saveNote && <span className="small muted">{saveNote}</span>}
          </div>
          {paint.notice && <div className="small muted">{paint.notice}</div>}
        </>
      )}
    </div>
  )
}
