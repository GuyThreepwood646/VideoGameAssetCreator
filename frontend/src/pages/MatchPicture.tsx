// "Match a picture" in a project's style lock: take the colours of a picture, or have the review engine describe its
// style in words. Nothing is applied until the person presses Apply; both results can be edited first.
import { useEffect, useRef, useState } from 'react'
import { api, type StyleLock, type Swatch } from '../api'
import { ImageDrop, type PickedImage } from '../components/ImageDrop'
import { Info } from '../components/Info'
import { ErrorText } from '../components/ui'

export function MatchPicture({ projectId, busy, onApply }: { projectId: string; busy: boolean; onApply: (change: Partial<StyleLock>) => void }) {
  const [picture, setPicture] = useState<PickedImage | null>(null)
  const [colors, setColors] = useState<8 | 16 | 32>(8)
  const [swatches, setSwatches] = useState<Swatch[] | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [waiting, setWaiting] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const poll = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (poll.current != null) window.clearTimeout(poll.current)
    },
    [],
  )
  // A new picture forgets the old results.
  useEffect(() => {
    setSwatches(null)
    setText(null)
    setNote(null)
    setWaiting(null)
    setError(null)
  }, [picture])

  const takeColours = async () => {
    if (!picture) return
    setWorking(true)
    setError(null)
    try {
      const r = await api.paletteFromImage(picture.dataUrl, colors)
      setSwatches(r.swatches)
    } catch (e) {
      setError(e)
    } finally {
      setWorking(false)
    }
  }

  const waitFor = (jobId: string) => {
    api.getJob(jobId).then(
      (j) => {
        if (j.status === 'done' && j.result?.description) {
          setWaiting(null)
          setText(j.result.description)
          return
        }
        if (j.status === 'error' || j.status === 'cancelled') {
          setWaiting(null)
          setNote(j.status === 'error' ? 'The vision model could not describe this picture. Try again, or write the style prompt yourself.' : 'Stopped.')
          return
        }
        setWaiting(j.message && j.status === 'queued' ? j.message : 'The vision model is describing the style. It takes about a minute.')
        poll.current = window.setTimeout(() => waitFor(jobId), 2000)
      },
      (e) => {
        setWaiting(null)
        setError(e)
      },
    )
  }

  const describe = async () => {
    if (!picture) return
    setWorking(true)
    setError(null)
    setNote(null)
    try {
      const r = await api.styleFromImage(projectId, picture.dataUrl)
      if (!swatches) setSwatches(r.swatches)
      if (r.job) {
        setWaiting(r.message ?? 'The vision model is describing the style.')
        waitFor(r.job.id)
      } else {
        setNote(r.message ?? 'Describing the style needs the vision model (review engine). The colours are ready.')
      }
    } catch (e) {
      setError(e)
    } finally {
      setWorking(false)
    }
  }

  return (
    <div className="match-picture col">
      <div className="field-label">
        Match a picture
        <Info term="term.match_picture" />
      </div>
      <div className="muted small">Use a picture you like to set this project's colours or look. Nothing changes until you press Apply.</div>
      <ImageDrop value={picture} onChange={setPicture} label="Picture to match" disabled={busy || working} />
      <div className="row-gap">
        <button type="button" disabled={!picture || busy || working} onClick={takeColours}>
          Take the colours
        </button>
        <select value={colors} onChange={(e) => setColors(Number(e.target.value) as 8 | 16 | 32)} aria-label="How many colours" disabled={busy || working}>
          {[8, 16, 32].map((n) => (
            <option key={n} value={n}>
              {n} colours
            </option>
          ))}
        </select>
        <Info term="term.take_colours" />
        <button type="button" disabled={!picture || busy || working || !!waiting} onClick={describe}>
          Describe the style
        </button>
        <Info term="term.describe_style" />
      </div>
      <ErrorText error={error} />
      {swatches && (
        <div className="col">
          <div className="small">Colours, most used first. Remove any you do not want.</div>
          <div className="palette-editor">
            {swatches.map((s) => (
              <span key={s.hex} className="palette-item" title={`${s.hex}, ${Math.round(s.share * 100)}% of the picture`}>
                <span className="swatch" style={{ background: s.hex }} />
                <span className="mono small">{s.hex}</span>
                <span className="muted small">{Math.max(1, Math.round(s.share * 100))}%</span>
                <button type="button" className="chip-x" aria-label={`remove ${s.hex}`} onClick={() => setSwatches(swatches.filter((x) => x.hex !== s.hex))}>
                  x
                </button>
              </span>
            ))}
          </div>
          <div className="row-gap">
            <button type="button" className="primary" disabled={busy || swatches.length === 0} onClick={() => { onApply({ palette: swatches.map((s) => s.hex) }); setSwatches(null) }}>
              Apply as the palette
            </button>
            <button type="button" className="link small" onClick={() => setSwatches(null)}>
              Discard
            </button>
          </div>
        </div>
      )}
      {waiting && <div className="muted small">{waiting}</div>}
      {note && <div className="muted small">{note}</div>}
      {text !== null && (
        <div className="col">
          <label className="field">
            <span className="field-label">
              Style description
              <Info term="term.describe_style" />
            </span>
            <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <div className="muted small">This guides the look in words: it becomes the project's style prompt (style words), which the image model reads before every prompt. Edit it freely.</div>
          <div className="row-gap">
            <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => { onApply({ prompt_block: text.trim() }); setText(null) }}>
              Apply
            </button>
            <button type="button" className="link small" onClick={() => setText(null)}>
              Discard
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
