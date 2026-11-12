import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { itemsApi, libraryUrl, type Asset, type Attachment, type ItemsPanel as PanelData, type ItemStatus, type NudgeBody } from '../api'
import { useConfirm } from '../components/Confirm'
import { Info } from '../components/Info'
import { Disclosure, ErrorText } from '../components/ui'
import { useEvents } from '../events'
import Viewer, { type ViewerAttachment } from '../Viewer'

// "Add an item" on a finished character (docs/CHARACTER-PARTS-RESEARCH.md 6.2, backend vgac/items.py): pick or make
// an item, say where it goes in plain words, nudge it, and watch it in the clips before saving it into the files.

const CLIPS: { label: string; clip: string }[] = [
  { label: 'Stand', clip: 'idle' },
  { label: 'Walk', clip: 'walk' },
  { label: 'Run', clip: 'run' },
  { label: 'Attack', clip: 'attack' },
]

// One press in the character's own axes: +x its left, +y up, +z forward.
const MOVES: { label: string; title: string; move: [number, number, number] }[] = [
  { label: 'Up', title: 'Move it up', move: [0, 1, 0] },
  { label: 'Down', title: 'Move it down', move: [0, -1, 0] },
  { label: 'Forward', title: 'Move it toward the front of the character', move: [0, 0, 1] },
  { label: 'Back', title: 'Move it toward the back of the character', move: [0, 0, -1] },
  { label: 'To their left', title: "Move it toward the character's own left", move: [1, 0, 0] },
  { label: 'To their right', title: "Move it toward the character's own right", move: [-1, 0, 0] },
]
const TURNS: { label: string; title: string; turn: [number, number, number] }[] = [
  { label: 'Tip forward', title: 'Tilt the top of the item toward the front', turn: [1, 0, 0] },
  { label: 'Tip back', title: 'Tilt the top of the item toward the back', turn: [-1, 0, 0] },
  { label: 'Turn left', title: 'Turn it to the left, seen from above', turn: [0, 1, 0] },
  { label: 'Turn right', title: 'Turn it to the right, seen from above', turn: [0, -1, 0] },
]

function itemStatusText(s: ItemStatus | undefined): string {
  if (!s) return 'Not found.'
  switch (s.status) {
    case 'done':
      return 'Made.'
    case 'making':
      return s.stage ? `Being made (${s.stage === 'export' ? 'last step' : s.stage}).` : 'Being made.'
    case 'paused':
      return `Stopped: ${s.reason ?? 'it needs you'}.`
    case 'error':
      return `Failed: ${s.reason ?? 'see its page'}.`
    case 'started':
      return 'Started, not finished.'
    default:
      return 'Not made yet.'
  }
}

export function ItemsPanel({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const confirm = useConfirm()
  const [data, setData] = useState<PanelData | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [current, setCurrent] = useState<string | null>(null)
  const [clip, setClip] = useState<{ name: string; n: number } | null>({ name: 'idle', n: 0 })
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const shiftRef = useRef(false)

  const load = useCallback(() => {
    itemsApi.panel(asset.id).then(
      (d) => {
        setData(d)
        setCurrent((c) => (c && d.equipment.attachments.some((a) => a.id === c) ? c : (d.equipment.attachments[0]?.id ?? null)))
      },
      setError,
    )
  }, [asset.id])
  useEffect(load, [load])
  // Items being made, or the files being rebuilt: look again now and then, and on any finished step.
  const busyItems = !!data && (Object.values(data.items).some((s) => s.status === 'making') || data.build?.status === 'equipping')
  useEffect(() => {
    if (!busyItems) return
    const t = window.setInterval(load, 5000)
    return () => window.clearInterval(t)
  }, [busyItems, load])
  useEvents(
    (ev) => {
      if (!data) return
      const ids = new Set([asset.id, ...Object.keys(data.items)])
      if (ev.type === 'version' && ids.has(ev.version.asset_id) && ev.version.status !== 'running') load()
    },
    [data, asset.id, load],
  )
  useEffect(() => {
    const down = (e: KeyboardEvent) => (shiftRef.current = e.shiftKey)
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', down)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', down)
    }
  }, [])

  const att = data?.equipment.attachments.find((a) => a.id === current) ?? null
  const item = att ? data?.items[att.item_asset_id] : undefined

  const replaceAtt = (a: Attachment) =>
    setData((d) => (d ? { ...d, needs_update: true, saved: false, equipment: { ...d.equipment, attachments: d.equipment.attachments.map((x) => (x.id === a.id ? a : x)) } } : d))

  const press = async (body: NudgeBody) => {
    if (!att) return
    try {
      const r = await itemsApi.nudge(asset.id, att.id, { ...body, big: shiftRef.current })
      replaceAtt(r.attachment)
    } catch (e) {
      setError(e)
    }
  }
  const place = async (socket: string) => {
    if (!att) return
    try {
      const r = await itemsApi.update(asset.id, att.id, { socket })
      setData(r.panel)
    } catch (e) {
      setError(e)
    }
  }
  const stow = async (socket: string) => {
    if (!att) return
    try {
      const r = await itemsApi.update(asset.id, att.id, { stow_socket: socket })
      replaceAtt(r.attachment)
    } catch (e) {
      setError(e)
    }
  }
  const save = async () => {
    setSaving('Putting the items into the character files...')
    try {
      await itemsApi.apply(asset.id)
      setSaving('Updating the files. This takes a minute or two (Unity builds the equipped prefab).')
      load()
    } catch (e) {
      setSaving(null)
      setError(e)
    }
  }
  // Fit it for me: the character's files are rebuilt with this item fitted to the moves, even when placed by hand.
  const fitIt = async () => {
    if (!att) return
    setSaving(`Fitting the ${att.label} to the moves and rebuilding the files. This takes a minute or two.`)
    try {
      await itemsApi.fit(asset.id, att.id)
      load()
    } catch (e) {
      setSaving(null)
      setError(e)
    }
  }
  const keepPlace = async () => {
    if (!att) return
    try {
      const r = await itemsApi.update(asset.id, att.id, { keep_place: true })
      setData(r.panel)
    } catch (e) {
      setError(e)
    }
  }
  const dismissNotice = async () => {
    if (!att) return
    try {
      const r = await itemsApi.update(asset.id, att.id, { dismiss_notice: true })
      setData(r.panel)
    } catch (e) {
      setError(e)
    }
  }
  const takeOff = async () => {
    if (!att) return
    if (!(await confirm.ask({ title: `Take the ${att.label} off?`, body: `It comes off ${asset.name}. The item itself stays in the project.`, confirmLabel: 'Take it off', danger: true }))) return
    try {
      const r = await itemsApi.remove(asset.id, att.id)
      setData(r.panel)
      setCurrent(r.panel.equipment.attachments[0]?.id ?? null)
    } catch (e) {
      setError(e)
    }
  }

  const viewerAttachments: ViewerAttachment[] = (data?.equipment.attachments ?? [])
    .map((a) => ({ a, s: data?.items[a.item_asset_id] }))
    .filter(({ s }) => s?.status === 'done' && !!s.glb)
    .map(({ a, s }) => ({ id: a.id, url: libraryUrl(s!.glb) ?? '', socket: a.socket, position: a.position, rotation: a.rotation, scale: a.scale }))
  const charUrl = libraryUrl(data?.character_glb)
  const placeLabel = (socket: string | null | undefined) => data?.places.find((p) => p.socket === socket)?.label ?? socket ?? ''
  const unsure = item?.status === 'done' && item.grip && item.grip.found !== false && item.grip.sure === false
  const noGrip = item?.status === 'done' && item.grip && item.grip.found === false

  return (
    <section className="card items-panel" aria-label="Add an item">
      {confirm.dialog}
      <div className="row-between">
        <h2 className="card-title">
          Items on {asset.name}
          <Info term="term.items" />
        </h2>
        <button type="button" className="small-btn" onClick={onClose}>
          Close
        </button>
      </div>
      <ErrorText error={error} />
      {!data ? (
        <p className="muted small">Loading...</p>
      ) : (
        <>
          {data.reason && <p className="muted small">{data.reason}</p>}
          {data.build?.status === 'stopped' && <p className="warn-text small">{data.build.reason}</p>}
          {data.equipment.attachments.length > 0 && (
            <div className="segmented items-tabs" role="tablist" aria-label="Items">
              {data.equipment.attachments.map((a) => (
                <button key={a.id} type="button" role="tab" aria-selected={a.id === current} aria-pressed={a.id === current} onClick={() => setCurrent(a.id)}>
                  {a.label} <span className="muted small">({placeLabel(a.socket).toLowerCase()})</span>
                </button>
              ))}
            </div>
          )}
          {att && (
            <div className="items-work">
              <div className="items-view">
                {charUrl ? (
                  <Viewer url={charUrl} height={460} showBonesDefault={false} defaultClip="idle" attachments={viewerAttachments} clipRequest={clip} />
                ) : (
                  <p className="muted small">The character's finished file appears here when the last step is done.</p>
                )}
                <div className="row-gap items-clips" role="group" aria-label="Play a move">
                  {CLIPS.map((c) => (
                    <button key={c.clip} type="button" className="small-btn" onClick={() => setClip({ name: c.clip, n: Date.now() })}>
                      {c.label}
                    </button>
                  ))}
                </div>
                <p className="muted small">
                  Watch the {att.label} during Attack. If it cuts through the arm or body, nudge it away; if it points the wrong way, turn it.
                </p>
              </div>
              <div className="items-controls">
                <p className="small">
                  <Link to={`/assets/${att.item_asset_id}`}>{att.item_name}</Link>: {itemStatusText(item)}
                  {item?.status === 'done' && item.grip?.method && !unsure && <span className="muted"> Held at {item.grip.method}.</span>}
                </p>
                {unsure && (
                  <p className="warn-text small">
                    We were not sure where the hand holds this ({item?.grip?.method}). Check it in the preview and use "Hold it nearer the tip" or "nearer the end" until the hand is on the handle.
                  </p>
                )}
                {noGrip && <p className="warn-text small">We could not work out where the hand holds this, so its own centre is used. Move it until the hand is on the handle.</p>}
                {att.notice && (
                  <p className="small">
                    {att.notice}{' '}
                    <button type="button" className="small-btn" onClick={dismissNotice}>
                      OK
                    </button>
                  </p>
                )}
                {att.offer && (
                  <div className="small">
                    <p>
                      A better place for the {att.label}: {att.offer.place.toLowerCase()}. {att.offer.why}
                    </p>
                    <div className="row-gap">
                      <button type="button" className="small-btn" onClick={() => place(att.offer!.socket)}>
                        Move it there
                      </button>
                      <button type="button" className="small-btn" onClick={keepPlace}>
                        Keep it where it is
                      </button>
                    </div>
                  </div>
                )}
                <div className="field">
                  <span className="field-label">Where does it go?</span>
                  <div className="items-places">
                    {data.places.map((p) => (
                      <button key={p.socket} type="button" aria-pressed={att.socket === p.socket} onClick={() => place(p.socket)}>
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>
                {['blade', 'haft', 'pole', 'bow', 'shield'].includes(item?.held_item?.kind ?? '') && data.places.some((p) => p.socket === 'Socket_Hip_L') && (
                  <label className="field">
                    <span className="field-label">When it is put away</span>
                    <select value={att.stow_socket ?? ''} onChange={(e) => stow(e.target.value)}>
                      <option value="">Nowhere (always in use)</option>
                      {data.places
                        .filter((p) => p.socket !== att.socket && !p.socket.startsWith('Socket_Hand'))
                        .map((p) => (
                          <option key={p.socket} value={p.socket}>
                            {p.label}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
                <div className="field">
                  <span className="field-label">
                    Nudge it <span className="muted small">(each press {data.step_cm} cm or {data.step_deg} degrees; hold Shift for bigger steps)</span>
                  </span>
                  <div className="items-nudges" role="group" aria-label="Move">
                    <span className="muted small nudge-title">Move</span>
                    {MOVES.map((m) => (
                      <button key={m.label} type="button" title={m.title} onClick={() => press({ move: m.move })}>
                        {m.label}
                      </button>
                    ))}
                  </div>
                  <div className="items-nudges" role="group" aria-label="Turn">
                    <span className="muted small nudge-title">Turn</span>
                    {TURNS.map((t) => (
                      <button key={t.label} type="button" title={t.title} onClick={() => press({ turn: t.turn })}>
                        {t.label}
                      </button>
                    ))}
                  </div>
                  <div className="items-nudges" role="group" aria-label="Size and grip">
                    <span className="muted small nudge-title">Size and grip</span>
                    <button type="button" title="Slide the item so the hand sits closer to its tip or head" onClick={() => press({ slide: 1 })}>
                      Hold it nearer the tip
                    </button>
                    <button type="button" title="Slide the item so the hand sits closer to its end" onClick={() => press({ slide: -1 })}>
                      Hold it nearer the end
                    </button>
                    <button type="button" onClick={() => press({ scale_steps: -1 })}>
                      Smaller
                    </button>
                    <button type="button" onClick={() => press({ scale_steps: 1 })}>
                      Bigger
                    </button>
                    <button type="button" onClick={() => press({ reset: true })}>
                      Start again
                    </button>
                  </div>
                </div>
                <div className="field">
                  <span className="field-label" title={data.fit?.help}>
                    Fit it to the moves
                  </span>
                  {att.fit?.sentence ? <p className={att.fit.cleared ? 'small' : 'warn-text small'}>{att.fit.sentence}</p> : null}
                  <p className="muted small">
                    {att.touched
                      ? 'You placed this yourself, so we only fit it when you ask.'
                      : 'While you have not moved it yourself, we fit it to the moves each time the files are rebuilt.'}
                  </p>
                  <div className="row-gap">
                    <button type="button" className="small-btn" disabled={!!saving || data.build?.status === 'equipping'} onClick={fitIt} title={data.fit?.help}>
                      Fit it for me
                    </button>
                    <span className="muted small">
                      Tries a push of up to {data.fit?.push_max_cm ?? 5} cm out from the body and small tilts, or a turn about the handle for something held, and keeps the smallest change that clears the body.
                    </span>
                  </div>
                </div>
                <Disclosure label="Advanced">
                  <AdvancedNumbers att={att} onSave={async (body) => {
                    try {
                      const r = await itemsApi.update(asset.id, att.id, body)
                      replaceAtt(r.attachment)
                    } catch (e) {
                      setError(e)
                    }
                  }} />
                </Disclosure>
                <p className="muted small">{data.limit}</p>
                <div className="row-gap">
                  <button type="button" className="primary" disabled={!data.needs_update || !!saving} onClick={save} title="Rebuild the character's files with the items in place">
                    Save to the character's files
                  </button>
                  <button type="button" className="small-btn" onClick={takeOff}>
                    Take it off
                  </button>
                </div>
                {saving && <p className="muted small">{saving}</p>}
                {data.saved && !saving && <p className="ok-text small">Saved: the files and the "(equipped)" Unity prefab have the items in place.</p>}
                {data.build?.status === 'equipping' && <p className="muted small">Updating the character's files with the items...</p>}
              </div>
            </div>
          )}
          {data.can_add && <AddItem asset={asset} data={data} busy={busy} setBusy={setBusy} onAdded={(id) => { setCurrent(id); load() }} onError={setError} />}
        </>
      )}
    </section>
  )
}

function AdvancedNumbers({ att, onSave }: { att: Attachment; onSave: (b: { position: number[]; rotation_deg: number[]; scale: number }) => void }) {
  const [pos, setPos] = useState(att.position.map((v) => Math.round(v * 1000) / 10))
  const [rot, setRot] = useState(att.rotation_deg.map((v) => Math.round(v * 10) / 10))
  const [scale, setScale] = useState(att.scale)
  useEffect(() => {
    setPos(att.position.map((v) => Math.round(v * 1000) / 10))
    setRot(att.rotation_deg.map((v) => Math.round(v * 10) / 10))
    setScale(att.scale)
  }, [att])
  const num = (v: string) => (Number.isFinite(Number(v)) ? Number(v) : 0)
  return (
    <div className="col small">
      <p className="muted">Offsets from the attachment point, in its own axes (forward along the grip, up toward the thumb).</p>
      {(['x', 'y', 'z'] as const).map((k, i) => (
        <label key={k} className="row-gap">
          <span>Move {k} (cm)</span>
          <input type="number" step="0.5" value={pos[i]} onChange={(e) => setPos(pos.map((v, j) => (j === i ? num(e.target.value) : v)))} />
          <span>Turn {k} (degrees)</span>
          <input type="number" step="5" value={rot[i]} onChange={(e) => setRot(rot.map((v, j) => (j === i ? num(e.target.value) : v)))} />
        </label>
      ))}
      <label className="row-gap">
        <span>Size</span>
        <input type="number" step="0.05" min="0.2" max="5" value={scale} onChange={(e) => setScale(num(e.target.value) || 1)} />
      </label>
      <button type="button" className="small-btn" onClick={() => onSave({ position: pos.map((v) => v / 100), rotation_deg: rot, scale })}>
        Use these numbers
      </button>
    </div>
  )
}

function AddItem({ asset, data, busy, setBusy, onAdded, onError }: { asset: Asset; data: PanelData; busy: boolean; setBusy: (b: boolean) => void; onAdded: (attId: string) => void; onError: (e: unknown) => void }) {
  const [mode, setMode] = useState<'new' | 'mine' | null>(data.equipment.attachments.length ? null : 'new')
  const [text, setText] = useState('')
  const [type, setType] = useState('')
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim() && !type) return
    setBusy(true)
    try {
      const r = await itemsApi.add(asset.id, { description: text.trim() || undefined, type: type || undefined, build: true })
      setText('')
      onAdded(r.attachment.id)
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }
  const pickMine = async (id: string) => {
    setBusy(true)
    try {
      const r = await itemsApi.add(asset.id, { item_asset_id: id })
      onAdded(r.attachment.id)
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="items-add">
      <h3 className="card-subtitle">Add an item</h3>
      <div className="items-tiles">
        <button type="button" className="items-tile" aria-pressed={mode === 'new'} onClick={() => setMode('new')}>
          <strong>Make a new item</strong>
          <span className="muted small">One line is enough; the look is copied from {asset.name}.</span>
        </button>
        <button type="button" className="items-tile" aria-pressed={mode === 'mine'} disabled={!data.others.length} onClick={() => setMode('mine')} title={data.others.length ? undefined : 'No other items in this project yet'}>
          <strong>Use one you made</strong>
          <span className="muted small">{data.others.length ? `${data.others.length} in this project` : 'None yet'}</span>
        </button>
      </div>
      {mode === 'new' && (
        <form className="col" onSubmit={submit}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="a short sword with a leather grip" aria-label="Describe the item" />
          <label className="row-gap small">
            <span>What is it?</span>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">Work it out from my words</option>
              {data.types.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label} ({t.place.toLowerCase()})
                </option>
              ))}
            </select>
          </label>
          <div className="row-gap">
            <button type="submit" className="primary" disabled={busy || (!text.trim() && !type)}>
              Make it and put it on
            </button>
            <span className="muted small">It is made on its own (a few minutes), then shown here on {asset.name}.</span>
          </div>
        </form>
      )}
      {mode === 'mine' && (
        <div className="items-grid">
          {data.others.map((o) => (
            <button key={o.id} type="button" className="items-tile" disabled={busy} onClick={() => pickMine(o.id)}>
              {o.thumbnail && <img src={libraryUrl(o.thumbnail)} alt="" />}
              <strong>{o.name}</strong>
              <span className="muted small">{itemStatusText(o)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
