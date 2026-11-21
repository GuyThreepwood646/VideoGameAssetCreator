// Touch up (WP-22, saving WP-23): paint on the model's colour picture in step 4. Save makes a new clean-up version with
// the paint; until then the paint stays in this page.
import { useState } from 'react'
import { api } from '../../api'
import { BRUSH_MAX, BRUSH_MIN, type PaintMode, type PaintSettings } from '../../paint/paintSession'
import type { PaintState } from '../../paint/usePaint'

function hexOf(rgb: [number, number, number]): string {
  return '#' + rgb.map((c) => c.toString(16).padStart(2, '0')).join('')
}
function rgbOf(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

const MODES: { id: PaintMode; label: string; hint: string }[] = [
  { id: 'paint', label: 'Brush', hint: 'Paints the colour you pick.' },
  { id: 'smudge', label: 'Smudge', hint: 'Drags the colour under the brush along your stroke, so it blends into what is there.' },
  { id: 'fill', label: 'Fill area', hint: 'One click fills the surface you click where the colour is close to the colour under the click (Match). One undo step.' },
  { id: 'erase', label: 'Eraser', hint: 'Puts back the model\'s own picture under the brush.' },
  { id: 'pick', label: 'Eyedropper', hint: 'Click the model to take its colour for the brush.' },
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
          {(paint.maps.shine || paint.maps.metal) && (
            <div className="touch-up-row">
              <span className="segmented" role="radiogroup" aria-label="What to paint">
                <button type="button" role="radio" aria-checked={s.target === 'colour'} className={'seg' + (s.target === 'colour' ? ' seg-on' : '')} onClick={() => paint.setSettings({ target: 'colour' })}>
                  Colour
                </button>
                {paint.maps.shine && (
                  <button type="button" role="radio" aria-checked={s.target === 'shine'} className={'seg' + (s.target === 'shine' ? ' seg-on' : '')} onClick={() => paint.setSettings({ target: 'shine' })}>
                    Shine
                  </button>
                )}
                {paint.maps.metal && (
                  <button type="button" role="radio" aria-checked={s.target === 'metal'} className={'seg' + (s.target === 'metal' ? ' seg-on' : '')} onClick={() => paint.setSettings({ target: 'metal' })}>
                    Metal
                  </button>
                )}
              </span>
              {s.target === 'shine' && (
                <label className="small">
                  Shine {Math.round(s.shine * 100)}%{' '}
                  <input type="range" min={0} max={100} value={Math.round(s.shine * 100)} onChange={(e) => paint.setSettings({ shine: Number(e.target.value) / 100 })} aria-label="Shine: matt to shiny" />
                </label>
              )}
              {s.target === 'shine' && <span className="small muted">0 is matt, 100 is shiny.</span>}
              {s.target === 'metal' && (
                <label className="small">
                  Metal {Math.round(s.metal * 100)}%{' '}
                  <input type="range" min={0} max={100} value={Math.round(s.metal * 100)} onChange={(e) => paint.setSettings({ metal: Number(e.target.value) / 100 })} aria-label="Metal: not metal to metal" />
                </label>
              )}
              {s.target === 'metal' && <span className="small muted">0 is not metal, 100 is metal.</span>}
            </div>
          )}
          <div className="touch-up-row">
            <span className="segmented" role="radiogroup" aria-label="What the brush does">
              {MODES.map((m) => (
                <button key={m.id} type="button" role="radio" aria-checked={s.mode === m.id} className={'seg' + (s.mode === m.id ? ' seg-on' : '')} onClick={() => paint.setSettings({ mode: m.id })}>
                  {m.label}
                </button>
              ))}
            </span>
            {s.target === 'colour' && s.mode !== 'erase' && s.mode !== 'smudge' && (
              <label className="small">
                Colour{' '}
                <input type="color" value={hexOf(s.rgb)} onChange={(e) => paint.setSettings({ rgb: rgbOf(e.target.value) })} aria-label="Brush colour" />
              </label>
            )}
          </div>
          <div className="small muted">{MODES.find((m) => m.id === s.mode)?.hint}</div>
          <div className="touch-up-row">
            {s.mode !== 'fill' && (
              <>
                <label className="small">
                  Size {cm} cm on the model{' '}
                  <input type="range" min={Math.round(BRUSH_MIN * 100)} max={Math.round(BRUSH_MAX * 100)} value={cm} onChange={(e) => paint.setSettings({ radius: Number(e.target.value) / 100 })} aria-label="Brush size in centimetres" />
                </label>
                <label className="small">
                  Edge {edgeWord(s.hardness)}{' '}
                  <input type="range" min={0} max={100} value={Math.round(s.hardness * 100)} onChange={(e) => paint.setSettings({ hardness: Number(e.target.value) / 100 })} aria-label="Edge: soft to hard" />
                </label>
              </>
            )}
            {s.mode === 'fill' && (
              <label className="small">
                Match {Math.round(s.match * 100)}%{' '}
                <input type="range" min={0} max={100} value={Math.round(s.match * 100)} onChange={(e) => paint.setSettings({ match: Number(e.target.value) / 100 })} aria-label="Match: how close a colour must be to fill" />
              </label>
            )}
            <label className="small">
              {s.mode === 'smudge' ? 'How far the colour drags' : 'Strength'} {Math.round(s.strength * 100)}%{' '}
              <input type="range" min={10} max={100} value={Math.round(s.strength * 100)} onChange={(e) => paint.setSettings({ strength: Number(e.target.value) / 100 })} aria-label={s.mode === 'smudge' ? 'How far the colour drags' : 'Strength'} />
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
            {paint.saveable && !paint.canUndo && !saving && <span className="small muted">Nothing is painted yet. Paint on the model, then save.</span>}
            {saveNote && <span className="small muted">{saveNote}</span>}
          </div>
          {paint.notice && <div className="small muted">{paint.notice}</div>}
        </>
      )}
    </div>
  )
}

// Smooth the paint (WP-24): on a painted clean-up, a new version with the paint smoothed inside its own mask. The
// backend refuses it for any other version; Adjust, Refine and Try again stay refused on the painted version.
const SMOOTH_CHOICES: { id: 'light' | 'strong'; label: string; hint: string }[] = [
  { id: 'light', label: 'Light', hint: 'takes off the speckle and the hardest brush edges' },
  { id: 'strong', label: 'Strong', hint: 'softer again: three passes' },
]

export function SmoothPaint({ versionId, smoothed, onDone }: { versionId: string; smoothed: string | null; onDone?: () => void }) {
  const [strength, setStrength] = useState<'light' | 'strong'>('light')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const run = async () => {
    setBusy(true)
    setNote(null)
    try {
      await api.smooth(versionId, { strength })
      setNote('Saved. The smoothed version appears here when it is made. Keep it to carry the paint on to the rig and the export.')
      onDone?.()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not smooth the paint. Try again.')
    } finally {
      setBusy(false)
    }
  }
  const chosen = SMOOTH_CHOICES.find((c) => c.id === strength)
  return (
    <div className="touch-up" data-testid="smooth-paint">
      <div className="field-label">Smooth the paint</div>
      <div className="small muted">
        Evens out the speckle and rough colour inside the painted area only. Colours outside it, and the edges between different painted colours, stay as they are. Saves as a new version.
        {smoothed ? ` This version is already smoothed (${smoothed}).` : ''}
      </div>
      <div className="small muted">Smooth evens out the painted colour. To blend the paint's edge into the picture, paint with a softer edge.</div>
      <div className="touch-up-row">
        <span className="segmented" role="radiogroup" aria-label="How much to smooth">
          {SMOOTH_CHOICES.map((c) => (
            <button key={c.id} type="button" role="radio" aria-checked={strength === c.id} className={'seg' + (strength === c.id ? ' seg-on' : '')} onClick={() => setStrength(c.id)}>
              {c.label}
            </button>
          ))}
        </span>
        <span className="small muted">{chosen?.hint}</span>
      </div>
      <div className="touch-up-row">
        <button type="button" className="btn primary" disabled={busy} onClick={run}>
          {busy ? 'Smoothing the paint' : 'Smooth the paint'}
        </button>
        {note && <span className="small muted">{note}</span>}
      </div>
    </div>
  )
}
