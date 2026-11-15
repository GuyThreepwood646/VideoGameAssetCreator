// "Your library" on the System page: everything the person added themselves (add-ons, hairstyles, moves) in one place,
// each with where it came from, and a button in every group to add more at any time. Adding works here the same as where
// the thing is used (painter add-ons in Describe, hairstyles in Build the 3D model, moves in Make it move); changing and
// removing stay where it is used.
import { useCallback, useEffect, useState } from 'react'
import { api, hairApi, movesApi, type AddonEntry, type MyHairStyle, type MyMoveFile } from './api'
import { AddMyAddon } from './pipeline/AddMyAddon'
import { AddMyHairstyle } from './pipeline/AddMyHairstyle'
import { MyMoveUpload } from './pipeline/MyMoves'

type Lib = { addons: AddonEntry[]; hair: MyHairStyle[]; moves: MyMoveFile[]; maxMb: number }
type Adding = 'addon' | 'hair' | 'move' | null

export default function YourLibrary() {
  const [lib, setLib] = useState<Lib | null>(null)
  const [adding, setAdding] = useState<Adding>(null)
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
    void load()
  }
  const none = lib.addons.length + lib.hair.length + lib.moves.length === 0
  const from = (source: string, licence?: string) => [source ? `Where it came from: ${source}` : 'Where it came from: not written down', licence && licence !== 'Not stated' ? `licence: ${licence}` : null].filter(Boolean).join(', ')
  const addButton = (kind: Exclude<Adding, null>, text: string) => (
    <button type="button" className="small-btn" disabled={adding !== null} onClick={() => setAdding(kind)}>
      {text}
    </button>
  )
  return (
    <section className="your-library" aria-labelledby="your-library-title">
      <h2 id="your-library-title">Your library</h2>
      <p className="muted small">
        What you added yourself, next to the ready-made ones. You can add more of each kind here at any time, or where they are used: painter add-ons in step 1, hairstyles in step 3, moves in step 5. Change or remove them where they are used.
      </p>
      {none && <p className="small">You have not added anything yet.</p>}

      <div className="your-library-group">
        <div className="row-between">
          <div className="field-label">Painter add-ons ({lib.addons.length})</div>
          {addButton('addon', 'Add a painter add-on')}
        </div>
        {adding === 'addon' && <AddMyAddon painter="" onAdded={done} onClose={() => setAdding(null)} />}
        {lib.addons.length > 0 && (
          <ul className="small">
            {lib.addons.map((a) => (
              <li key={a.id}>
                <strong>{a.label}</strong> <span className="muted">{from(a.source, a.licence)}</span>
              </li>
            ))}
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
            {lib.hair.map((h) => (
              <li key={h.id}>
                <strong>{h.name}</strong> <span className="muted">{h.family_label.toLowerCase()}, {from(h.source, h.licence)}</span>
              </li>
            ))}
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
            {lib.moves.map((m) => (
              <li key={m.id}>
                <strong>{m.label}</strong> <span className="muted">{m.clips.length} {m.clips.length === 1 ? 'move' : 'moves'}, {from(m.source)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
