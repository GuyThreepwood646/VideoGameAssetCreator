// "Which way is the front?" (plan 3.2): the face_yaw knob as four picture buttons plus Automatic. Each picture is a
// still of the built 3D model seen from the front camera, turned by that option's angle; the person picks the one
// where the front faces them. While a still is rendering, or when there is no model to draw, a top-down drawing of
// the turn stands in (SVG, no emoji). When Automatic is chosen, the angle prep actually used carries a "chosen" mark.
import { useId } from 'react'
import { Info } from '../components/Info'
import { useGlbYawSnapshots } from '../glbSnapshot'
import { FACE_YAW_HINT, optionLabel } from '../help'

const TURNS = [0, 90, 180, 270]

type Props = {
  value: unknown
  onChange: (v: unknown) => void
  // The options the backend offers (auto and the four turns); numbers or strings.
  options?: (string | number)[]
  optionLabels?: Record<string, string>
  label?: string
  hint?: string
  // The mesh version's GLB: the model as built, before prep turned it.
  glbUrl?: string
  // The turn prep applied when Automatic was chosen (from the prep version's metrics), if known.
  chosenYaw?: number | null
  disabled?: boolean
}

export function FrontPicker({ value, onChange, options, optionLabels, label = 'Which way is the front?', hint = FACE_YAW_HINT, glbUrl, chosenYaw, disabled }: Props) {
  const name = useId()
  const offered = options && options.length ? options : ['auto', ...TURNS]
  const autoOpt = offered.find((o) => String(o) === 'auto')
  const turnOpts = offered.filter((o) => TURNS.includes(Number(o)))
  const { srcs } = useGlbYawSnapshots(glbUrl, turnOpts.map(Number))
  const cur = value === null || value === undefined || value === '' ? 'auto' : String(value)
  const p = { name: 'face_yaw', option_labels: optionLabels }

  const pick = (o: string | number) => onChange(o)

  return (
    <fieldset className="front-picker" disabled={disabled}>
      <legend className="field-label">
        {label}
        <Info term="param.face_yaw" />
      </legend>
      <div className="front-options" role="radiogroup" aria-label={label}>
        {autoOpt !== undefined && (
          <label className={'front-option front-auto' + (cur === 'auto' ? ' on' : '')}>
            <input type="radio" name={name} checked={cur === 'auto'} onChange={() => pick(autoOpt)} />
            <span className="front-pic front-pic-auto" aria-hidden="true">
              <AutoIcon />
            </span>
            <span className="front-label">{optionLabel(p, 'auto')}</span>
          </label>
        )}
        {turnOpts.map((o) => {
          const yaw = Number(o)
          const on = cur === String(o)
          const chosen = cur === 'auto' && chosenYaw !== null && chosenYaw !== undefined && ((chosenYaw % 360) + 360) % 360 === yaw
          const src = srcs[yaw]
          return (
            <label key={String(o)} className={'front-option' + (on ? ' on' : '') + (chosen ? ' chosen' : '')}>
              <input type="radio" name={name} checked={on} onChange={() => pick(o)} />
              <span className="front-pic">
                {src ? <img src={src} alt={`The model ${optionLabel(p, o).toLowerCase()}, seen from the front`} /> : <TurnIcon yaw={yaw} />}
                {chosen && <span className="front-chosen">chosen</span>}
              </span>
              <span className="front-label">{optionLabel(p, o)}</span>
            </label>
          )
        })}
      </div>
      <span className="field-hint">{hint}</span>
    </fieldset>
  )
}

// Top-down drawing: the model as a round body with a pointed nose, you at the bottom. The nose shows where the
// built front ends up after the turn (a quarter to the left turns it toward its own left, your right).
function TurnIcon({ yaw }: { yaw: number }) {
  return (
    <svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true" className="turn-icon">
      <g transform={`rotate(${-yaw} 32 28)`}>
        <circle cx="32" cy="28" r="13" fill="none" stroke="currentColor" strokeWidth="2.5" />
        <path d="M24 37 L32 48 L40 37 Z" fill="currentColor" />
      </g>
      {yaw !== 0 && (
        <path d={yaw === 180 ? 'M14 28 A18 18 0 0 1 50 28' : yaw === 90 ? 'M18 40 A18 18 0 0 0 50 28' : 'M46 40 A18 18 0 0 1 14 28'} fill="none" stroke="var(--accent)" strokeWidth="2" strokeDasharray="3 3" />
      )}
      <rect x="27" y="58" width="10" height="3" rx="1.5" fill="var(--muted)" />
    </svg>
  )
}

function AutoIcon() {
  return (
    <svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true" className="turn-icon">
      <path d="M48 32 A16 16 0 1 1 41 18.7" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M36 12 L44 17 L37 24 Z" fill="currentColor" />
      <circle cx="32" cy="32" r="3.5" fill="var(--accent)" />
    </svg>
  )
}
