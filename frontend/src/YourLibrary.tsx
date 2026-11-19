// "Your library" on the System page: everything the person added themselves (add-ons, hairstyles, moves) in one place,
// each with where it came from, and in every group a button to add more and, on each item, Change and Remove. The forms and
// calls are the ones used where the thing is used (painter add-ons in Describe, hairstyles in Build the 3D model, moves in
// Make it move), so nothing about your own resources is possible only inside a step.
import { useCallback, useEffect, useState } from 'react'
import { api, hairApi, movesApi, type AddonEntry, type MyHairStyle, type MyMoveFile } from './api'
import { useConfirm } from './components/Confirm'
import { addonRemoveWords } from './help'
import { ErrorText } from './components/ui'
import { isEditLora, warningOf } from './editLoras'
import { AddMyAddon, ChangeMyAddon } from './pipeline/AddMyAddon'
import { AddMyHairstyle } from './pipeline/AddMyHairstyle'
import { MyMoveForm, MyMoveUpload } from './pipeline/MyMoves'

type Lib = { addons: AddonEntry[]; hair: MyHairStyle[]; moves: MyMoveFile[]; maxMb: number }
type Adding = 'addon' | 'hair' | 'move' | null
type Editing = { kind: 'addon' | 'hair' | 'move'; id: string } | null

export default function YourLibrary() {
  const [lib, setLib] = useState<Lib | null>(null)
  const [adding, setAdding] = useState<Adding>(null)
  const [editing, setEditing] = useState<Editing>(null)
  const [error, setError] = useState<unknown>(null)
  const { ask, dialog } = useConfirm()
  const load = useCallback(async () => {
    const [addons, hair, moves] = await Promise.all([
      api.myAddons().then((r) => r.addons, () => [] as AddonEntry[]),
      hairApi.mine().then((r) => r.styles, () => [] as MyHairStyle[]),
      movesApi.mine().then((r) => ({ files: r.files, maxMb: r.max_mb ?? 0 }), () => ({ files: [] as MyMoveFile[], maxMb: 0 })),
    ])
    setLib({ addons, hair, moves: moves.files, maxMb: moves.maxMb })
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  if (!lib) return null
  const done = () => {
    setAdding(null)
    setEditing(null)
    void load()
  }
  // Remove: the app's own confirm, with what happens to characters already made (the words the steps use; the backend
  // refuses a hairstyle that a character has picked right now and says which one).
  const remove = async (what: { title: string; body: string; label: string }, call: () => Promise<unknown>) => {
    if (!(await ask({ title: what.title, body: what.body, confirmLabel: what.label, danger: true }))) return
    setError(null)
    try {
      await call()
      done()
    } catch (e) {
      setError(e)
    }
  }
  const busy = adding !== null || editing !== null
  const itemButtons = (kind: 'addon' | 'hair' | 'move', id: string, name: string, onRemove: () => void) => (
    <span className="your-library-actions">
      <button type="button" className="link small" disabled={busy} aria-label={`Change ${name}`} onClick={() => setEditing({ kind, id })}>
        Change
      </button>
      <button type="button" className="link small" disabled={busy} aria-label={`Remove ${name}`} onClick={onRemove}>
        Remove
      </button>
    </span>
  )
  const none = lib.addons.length + lib.hair.length + lib.moves.length === 0
  // Picture-edit LoRAs are not painter add-ons: each list counts only its own entries.
  const painterAddons = lib.addons.filter((a) => !isEditLora(a))
  const editAddons = lib.addons.filter((a) => isEditLora(a))
  const from = (source: string, licence?: string) => [source ? `Where it came from: ${source}` : 'Where it came from: not written down', licence && licence !== 'Not stated' ? `licence: ${licence}` : null].filter(Boolean).join(', ')
  const addButton = (kind: Exclude<Adding, null>, text: string) => (
    <button type="button" className="small-btn" disabled={busy} onClick={() => setAdding(kind)}>
      {text}
    </button>
  )
  return (
    <section className="your-library" aria-labelledby="your-library-title">
      <h2 id="your-library-title">Your library</h2>
      <p className="muted small">
        What you added yourself, next to the ready-made ones. Add, change or remove them here at any time, or where they are used: painter add-ons in step 1, hairstyles in step 3, moves in step 5.
      </p>
      <ErrorText error={error} />
      {none && <p className="small">You have not added anything yet.</p>}

      <div className="your-library-group">
        <div className="row-between">
          <div className="field-label">Painter add-ons ({painterAddons.length})</div>
          {addButton('addon', 'Add a painter add-on')}
        </div>
        {adding === 'addon' && <AddMyAddon painter="" onAdded={done} onClose={() => setAdding(null)} />}
        {painterAddons.length > 0 && (
          <ul className="small">
            {painterAddons.map((a) =>
              editing?.kind === 'addon' && editing.id === a.id ? (
                <li key={a.id}>
                  <ChangeMyAddon entry={a} onSaved={done} onCancel={() => setEditing(null)} />
                </li>
              ) : (
                <li key={a.id}>
                  <strong>{a.label}</strong> <span className="muted">{from(a.source, a.licence)}</span>
                  {a.placement?.note && <div className={a.placement.file_missing || a.placement.source_missing ? 'error small' : 'muted small'}>{a.placement.note}</div>}
                  {itemButtons('addon', a.id, a.label, () =>
                    void remove(
                      { title: `Remove "${a.label}"?`, body: `${addonRemoveWords(a.placement?.mode)} Characters already made are not changed.`, label: 'Remove add-on' },
                      () => api.removeMyAddon(a.id),
                    ),
                  )}
                </li>
              ),
            )}
          </ul>
        )}
      </div>

      <div className="your-library-group">
        <div className="row-between">
          <div className="field-label">LoRAs for picture edits ({editAddons.length})</div>
        </div>
        {editAddons.length > 0 && (
          <ul className="small">
            {editAddons.map((a) =>
              editing?.kind === 'addon' && editing.id === a.id ? (
                <li key={a.id}>
                  <ChangeMyAddon entry={a} onSaved={done} onCancel={() => setEditing(null)} />
                </li>
              ) : (
                <li key={a.id}>
                  <strong>{a.label}</strong> <span className="muted">{from(a.source, a.licence)}</span>
                  <div className="muted small">Used for picture edits (Qwen-Image-Edit).</div>
                  {warningOf(a) && <div className="addon-note small">{warningOf(a)}</div>}
                  {a.placement?.note && <div className={a.placement.file_missing || a.placement.source_missing ? 'error small' : 'muted small'}>{a.placement.note}</div>}
                  {itemButtons('addon', a.id, a.label, () =>
                    void remove(
                      { title: `Remove "${a.label}"?`, body: `${addonRemoveWords(a.placement?.mode)} Characters already made are not changed.`, label: 'Remove add-on' },
                      () => api.removeMyAddon(a.id),
                    ),
                  )}
                </li>
              ),
            )}
          </ul>
        )}
      </div>

      <div className="your-library-group">
        <div className="row-between">
          <div className="field-label">Hairstyles ({lib.hair.length})</div>
          {addButton('hair', 'Add a hairstyle')}
        </div>
        {adding === 'hair' && <AddMyHairstyle onDone={done} onCancel={() => setAdding(null)} />}
        {lib.hair.length > 0 && (
          <ul className="small">
            {lib.hair.map((h) =>
              editing?.kind === 'hair' && editing.id === h.id ? (
                <li key={h.id}>
                  <AddMyHairstyle editing={h} onDone={done} onCancel={() => setEditing(null)} />
                </li>
              ) : (
                <li key={h.id}>
                  <strong>{h.name}</strong> <span className="muted">{h.family_label.toLowerCase()}, {from(h.source, h.licence)}</span>
                  {itemButtons('hair', h.id, h.name, () =>
                    void remove(
                      {
                        title: `Remove "${h.name}"?`,
                        body: 'It is taken out of your hairstyle list and its copy is deleted from the app. Characters already built with it keep their hair. A character that has it picked right now must pick another hairstyle first.',
                        label: 'Remove hairstyle',
                      },
                      () => hairApi.removeMine(h.id),
                    ),
                  )}
                </li>
              ),
            )}
          </ul>
        )}
      </div>

      <div className="your-library-group">
        <div className="row-between">
          <div className="field-label">Moves ({lib.moves.length} {lib.moves.length === 1 ? 'file' : 'files'})</div>
          {addButton('move', 'Add a move file')}
        </div>
        {adding === 'move' && <MyMoveUpload maxMb={lib.maxMb} onDone={done} onCancel={done} />}
        {lib.moves.length > 0 && (
          <ul className="small">
            {lib.moves.map((m) =>
              editing?.kind === 'move' && editing.id === m.id ? (
                <li key={m.id}>
                  <MyMoveForm file={m} onSaved={done} onCancel={() => setEditing(null)} />
                </li>
              ) : (
                <li key={m.id}>
                  <strong>{m.label}</strong> <span className="muted">{m.clips.length} {m.clips.length === 1 ? 'move' : 'moves'}, {from(m.source)}</span>
                  {itemButtons('move', m.id, m.label, () =>
                    void remove(
                      {
                        title: `Remove "${m.label}"?`,
                        body: 'The file is taken off your list and its copy is deleted from the app. Characters already built with its moves keep them. If you rebuild one of those characters later, those moves are left out.',
                        label: 'Remove file',
                      },
                      () => movesApi.removeMine(m.id),
                    ),
                  )}
                </li>
              ),
            )}
          </ul>
        )}
      </div>
      {dialog}
    </section>
  )
}
