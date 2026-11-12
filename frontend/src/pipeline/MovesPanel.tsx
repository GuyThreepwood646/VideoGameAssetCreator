// The moves of a character in the rig step ("Make it move"), with "Add a move", "Remove them", "Use the same moves
// as ..." and "Use my own animation file" (backend moves.py: GET/POST /api/assets/{aid}/moves, /api/moves/library,
// /api/my-moves). Adding or removing makes only the moves again on the kept skeleton; the asset page picks up the
// new rig version through the usual job events.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { movesApi, type CharacterMove, type CharacterMoves, type MoveClip, type MoveLibrary, type MyMoveFile, type MyMovesListing, type Version } from '../api'
import { Info } from '../components/Info'
import { Disclosure, ErrorText } from '../components/ui'
import { joinAnd } from '../help'
import { MovePreview } from './MovePreview'
import { MyMoveForm, MyMoveUpload, RESPONSIBILITY } from './MyMoves'

const SOURCE_WORD: Record<CharacterMove['source'], string> = { library: 'hand-made', mine: 'yours', 'built-in': 'built in' }
const BUSY_POLL_MS = 4000

// previewClips: the clips the viewer can play (from the file); a move outside it says so. onPlay plays one in the viewer.
export function MovesPanel({ version, previewClips = null, onPlay }: { version: Version; previewClips?: string[] | null; onPlay?: (clip: string) => void }) {
  const aid = version.asset_id
  const [data, setData] = useState<CharacterMoves | null>(null)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [picking, setPicking] = useState(false)
  const [sending, setSending] = useState(false)
  const [sameAs, setSameAs] = useState('')

  const load = useCallback(async () => {
    try {
      const d = await movesApi.ofAsset(aid)
      setData(d)
      setMissing(false)
    } catch (e) {
      // An older backend has no moves list: the panel stays out of the way.
      if ((e as { status?: number }).status === 404) setMissing(true)
      else setError(e)
    }
  }, [aid])

  useEffect(() => {
    void load()
  }, [load, version.id, version.status])

  useEffect(() => {
    if (!data?.busy) return
    const t = window.setInterval(() => void load(), BUSY_POLL_MS)
    return () => window.clearInterval(t)
  }, [data?.busy, load])

  const change = async (body: { add?: string[]; remove?: string[]; copy_from?: string }) => {
    setSending(true)
    setError(null)
    try {
      await movesApi.change(aid, body)
        setPicking(false)
      await load()
    } catch (e) {
      setError(e)
    } finally {
      setSending(false)
    }
  }

  if (missing || !data || version.stage !== 'rig') return null
  const busy = data.busy || sending
  const kept = data.rig_version === version.id
  return (
    <section className="moves-panel">
      <div className="row-between">
        <div className="field-label">
          Moves in your files ({data.moves.length})
          <Info term="term.add_move" />
        </div>
        {data.can_add && kept && (
          <button type="button" className="primary" disabled={busy} onClick={() => setPicking(true)}>
            Add a move
          </button>
        )}
      </div>
      {!data.can_add && (
        <p className="small moves-instead">
          {data.reason}
          {data.instead.length > 0 && <> Creatures get these instead: {joinAnd(data.instead)}.</>}
        </p>
      )}
      {data.can_add && !kept && data.rig_version && (
        <p className="muted small">
          {busy ? 'This version is still being made. ' : 'This version is not the kept one. '}
          Moves are added to the kept version of this step: keep this version first (above) to add or remove its moves. The list below is the kept version's.
        </p>
      )}
      {busy && (
        <div className="small" role="status">
          Making the moves again... Only the moves are made again; the skeleton stays as it is. This takes about 15 seconds.
        </div>
      )}
      <ul className="moves-list">
        {data.moves.map((m) => (
          <li key={m.name} className={m.made ? '' : 'move-not-made'}>
            {onPlay && m.clip && previewClips?.includes(m.clip) && (
              <button type="button" className="small-btn" aria-label={`Play ${m.label} in the viewer`} title="Play this move in the viewer above" onClick={() => onPlay(m.clip!)}>
                Play
              </button>
            )}
            <span className="move-label">{m.label}</span>
            <span className="muted small">
              {m.loop ? 'loops' : 'plays once'}, {SOURCE_WORD[m.source]}
              {m.added ? ', added by you' : ''}
            </span>
            {previewClips && previewClips.length > 0 && (!m.clip || !previewClips.includes(m.clip)) && <span className="muted small">preview not available</span>}
            {data.can_add && kept && (
              <button type="button" className="link small move-remove" disabled={busy} aria-label={`Remove ${m.label} from your files`} onClick={() => change({ remove: [m.name] })}>
                Remove
              </button>
            )}
            {!m.made && (
              <span className="small">
                <span className="warn-mark">CHECK</span>could not be made
              </span>
            )}
          </li>
        ))}
      </ul>
      {data.can_add && kept && (
        <div className="row-gap">
          {data.others.length > 0 && (
            <span className="row-gap moves-same">
              <select value={sameAs} disabled={busy} aria-label="Character to copy the moves from" onChange={(e) => setSameAs(e.target.value)}>
                <option value="">Use the same moves as...</option>
                {data.others.map((o) => (
                  <option key={o.asset_id} value={o.asset_id}>
                    {o.name} ({o.added} added{o.removed ? `, ${o.removed} taken off` : ''})
                  </option>
                ))}
              </select>
              <button type="button" disabled={busy || !sameAs} onClick={() => change({ copy_from: sameAs })}>
                Use these moves
              </button>
            </span>
          )}
        </div>
      )}
      {(data.added.length > 0 || data.removed.length > 0) && (
        <Disclosure label="Your changes to the standard moves">
          <div className="small muted">
            {data.added.length > 0 && <div>Added: {data.added.map((a) => a.name).join(', ')}</div>}
            {data.removed.length > 0 && <div>Taken off: {data.removed.join(', ')}</div>}
            <div className="mono">added_moves, removed_moves in the rig version's settings</div>
          </div>
        </Disclosure>
      )}
      <ErrorText error={error} />
      {picking && <MovePicker have={data.moves} busy={busy} onAdd={(ids) => change({ add: ids })} onClose={() => setPicking(false)} />}
    </section>
  )
}

// --- The picker ----------------------------------------------------------------------------------------------------

function MovePicker({ have, busy, onAdd, onClose }: { have: CharacterMove[]; busy: boolean; onAdd: (ids: string[]) => void; onClose: () => void }) {
  const [lib, setLib] = useState<MoveLibrary | null>(null)
  const [mine, setMine] = useState<MyMovesListing | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [query, setQuery] = useState('')
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const [own, setOwn] = useState<{ mode: 'upload' } | { mode: 'edit'; file: MyMoveFile } | null>(null)

  const loadLib = useCallback(async () => {
    try {
      const [l, m] = await Promise.all([movesApi.library(), movesApi.mine().catch(() => null)])
      setLib(l)
      setMine(m)
    } catch (e) {
      setError(e)
    }
  }, [])
  useEffect(() => {
    void loadLib()
  }, [loadLib])
  // While previews are still being made, look again now and then.
  useEffect(() => {
    if (!lib?.previews.making) return
    const t = window.setInterval(() => void loadLib(), 8000)
    return () => window.clearInterval(t)
  }, [lib?.previews.making, loadLib])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !own) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, own])

  const haveClips = useMemo(() => new Set(have.map((m) => m.clip).filter((c): c is string => !!c)), [have])
  const groupLabel = useMemo(() => Object.fromEntries((lib?.groups ?? []).map((g) => [g.id, g.label])), [lib])
  const q = query.trim().toLowerCase()
  const shown = useMemo(() => {
    if (!lib) return []
    const words = q.split(/\s+/).filter(Boolean)
    return lib.clips.filter((c) => {
      if (!words.length) return true
      const hay = `${c.label} ${groupLabel[c.group] ?? ''} ${c.action} ${c.name}`.toLowerCase()
      return words.every((w) => hay.includes(w))
    })
  }, [lib, q, groupLabel])
  const byGroup = useMemo(() => {
    const m = new Map<string, MoveClip[]>()
    for (const c of shown) m.set(c.group, [...(m.get(c.group) ?? []), c])
    return m
  }, [shown])

  const tick = (id: string, on: boolean) =>
    setTicked((s) => {
      const n = new Set(s)
      if (on) n.add(id)
      else n.delete(id)
      return n
    })

  const fileOf = (clipId: string) => mine?.files.find((f) => clipId.startsWith(f.id + ':'))
  const ownSaved = async () => {
    setOwn(null)
    await loadLib()
  }

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="move-picker-title" onClick={() => !own && onClose()}>
      <div className="overlay-card move-picker" onClick={(e) => e.stopPropagation()}>
        <div className="row-between">
          <h2 id="move-picker-title" className="overlay-title">
            Add a move
          </h2>
          <button type="button" className="link" onClick={onClose}>
            Close
          </button>
        </div>
        {own ? (
          own.mode === 'upload' ? (
            <MyMoveUpload maxMb={mine?.max_mb ?? 0} onDone={ownSaved} onCancel={() => void ownSaved()} />
          ) : (
            <MyMoveForm file={own.file} onSaved={ownSaved} onCancel={() => setOwn(null)} />
          )
        ) : (
          <>
            <p className="muted small">
              Hand-made moves from a free library (fine for commercial games). Tick the ones you want and press Add them: only the moves are made again, the skeleton stays as it is.
            </p>
            <div className="row-gap">
              <input className="move-search" type="search" value={query} placeholder="Search, for example sit, sword, swim" aria-label="Search the moves" onChange={(e) => setQuery(e.target.value)} />
              {lib?.previews.making && (
                <span className="muted small" role="status">
                  Making the previews: {lib.previews.ready} of {lib.previews.total} ready.
                </span>
              )}
            </div>
            {!lib && !error && <div className="muted small">Loading the moves...</div>}
            <div className="move-groups">
              {lib?.groups.map((g) => {
                const list = byGroup.get(g.id) ?? []
                const isMine = g.id === 'mine'
                if (!list.length && !(isMine && !q)) return null
                return (
                  <div key={g.id} className="move-group">
                    <div className="field-label">{g.label}</div>
                    {isMine && (
                      <div className="row-gap small">
                        {(mine?.files ?? []).map((f) => (
                          <span key={f.id} className="row-gap">
                            <span>{f.label}</span>
                            <button type="button" className="link" onClick={() => setOwn({ mode: 'edit', file: f })}>
                              Change names
                            </button>
                            <button
                              type="button"
                              className="link"
                              onClick={async () => {
                                try {
                                  await movesApi.removeMine(f.id)
                                  await loadLib()
                                } catch (e) {
                                  setError(e)
                                }
                              }}
                            >
                              Take off the list
                            </button>
                          </span>
                        ))}
                        <button type="button" onClick={() => setOwn({ mode: 'upload' })}>
                          Use my own animation file
                        </button>
                        <span className="muted">{mine?.responsibility || RESPONSIBILITY}</span>
                      </div>
                    )}
                    <div className="move-cards">
                      {list.map((c) => {
                        const already = haveClips.has(c.id)
                        const on = ticked.has(c.id)
                        return (
                          <label key={c.id} className={'move-card' + (on ? ' move-card-on' : '') + (already ? ' move-card-have' : '')} title={c.action}>
                            <MovePreview src={c.preview} frames={c.frames} still={c.still} seconds={c.seconds} label={c.label} />
                            <span className="move-card-text">
                              <span className="row-gap">
                                <input type="checkbox" checked={on || already} disabled={already || busy} onChange={(e) => tick(c.id, e.target.checked)} />
                                <span className="move-card-label">{c.label}</span>
                              </span>
                              <span className="muted small">
                                {already ? 'already has it' : c.loop ? 'loops' : 'plays once'}
                                {c.moves_character ? ', moves the character' : ''}
                                {c.pack === 'mine' && fileOf(c.id) ? `, from ${fileOf(c.id)!.label}` : ''}
                              </span>
                            </span>
                          </label>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
              {lib && shown.length === 0 && q && <div className="muted small">No move matches "{query}". Try another word, such as walk, jump or sword.</div>}
            </div>
            <ErrorText error={error} />
            <div className="row-gap move-picker-foot">
              <button type="button" className="primary" disabled={busy || ticked.size === 0} onClick={() => onAdd([...ticked])}>
                {ticked.size > 1 ? `Add them (${ticked.size})` : 'Add them'}
              </button>
              <button type="button" onClick={onClose}>
                Cancel
              </button>
              {lib && (
                <Disclosure label="Where these come from">
                  <ul className="small muted">
                    {lib.packs.map((p) => (
                      <li key={p.id}>
                        {p.label}: {p.count} moves. {p.licence}
                      </li>
                    ))}
                  </ul>
                </Disclosure>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
