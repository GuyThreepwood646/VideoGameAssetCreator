// A row of ready-made looks (style presets): label, description, palette swatches
// The whole card is the button; the chosen one shows Selected. The model name lives in Advanced.
import type { LookPreset } from '../api'

export function PresetRow({
  presets,
  currentId,
  edited = false,
  busy = false,
  compact = false,
  onPick,
}: {
  presets: LookPreset[]
  currentId: string | null
  edited?: boolean
  busy?: boolean
  compact?: boolean
  onPick: (p: LookPreset) => void
}) {
  return (
    <div className={'preset-row' + (compact ? ' compact' : '')} role="radiogroup" aria-label="Ready-made looks">
      {presets.map((p) => {
        const current = p.id === currentId
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={current}
            className={'preset-card' + (current ? ' current' : '')}
            disabled={busy}
            onClick={() => onPick(p)}
            title={current && edited ? 'Edited by hand. Click to go back to this look as it was made.' : p.style_lock.prompt_block}
          >
            <span className="preset-head">
              <span className="preset-label">{p.label}</span>
              {current && <span className="chip chip-ok">{edited ? 'Selected, edited' : 'Selected'}</span>}
            </span>
            {!compact && <span className="preset-desc small">{p.description}</span>}
            <span className="swatches" aria-label="palette">
              {(p.style_lock.palette ?? []).map((c, i) => (
                <span key={i} className="swatch" style={{ background: c }} title={c} />
              ))}
            </span>
          </button>
        )
      })}
    </div>
  )
}
