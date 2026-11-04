// A row of ready-made looks (style presets): label, description, palette swatches
// and "Use this look". The current one is highlighted.
import type { LookPreset } from '../api'
import { modelLabel } from '../help'
import { Info } from './Info'

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
    <div className={'preset-row' + (compact ? ' compact' : '')} role="list" aria-label="Ready-made looks">
      {presets.map((p) => {
        const current = p.id === currentId
        return (
          <div key={p.id} role="listitem" className={'preset-card' + (current ? ' current' : '')}>
            <div className="preset-head">
              <span className="preset-label">{p.label}</span>
              {current && <span className="chip chip-ok">{edited ? 'using, edited' : 'using'}</span>}
            </div>
            {!compact && <div className="preset-desc small">{p.description}</div>}
            <div className="swatches" aria-label="palette">
              {(p.style_lock.palette ?? []).map((c, i) => (
                <span key={i} className="swatch" style={{ background: c }} title={c} />
              ))}
            </div>
            <div className="muted small preset-meta">
              {modelLabel(p.style_lock.model)}
              <Info term={'model.' + p.style_lock.model} />
            </div>
            <button type="button" className={current ? 'small-btn' : 'small-btn preset-use'} disabled={busy || (current && !edited)} onClick={() => onPick(p)} title={p.style_lock.prompt_block}>
              {current ? (edited ? 'Reset to this look' : 'Using this look') : 'Use this look'}
            </button>
          </div>
        )
      })}
    </div>
  )
}
