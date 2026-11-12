// "Your library" on the System page: everything the person added themselves (add-ons, hairstyles, moves) in one place,
// each with where it came from. Read-only: adding, changing and removing happen where the thing is used (the painter
// add-ons in Describe, hairstyles in Build the 3D model, moves in Make it move).
import { useEffect, useState } from 'react'
import { api, hairApi, movesApi, type AddonEntry, type MyHairStyle, type MyMoveFile } from './api'

type Lib = { addons: AddonEntry[]; hair: MyHairStyle[]; moves: MyMoveFile[] }

export default function YourLibrary() {
  const [lib, setLib] = useState<Lib | null>(null)
  useEffect(() => {
    let live = true
    Promise.all([
      api.myAddons().then((r) => r.addons, () => [] as AddonEntry[]),
      hairApi.mine().then((r) => r.styles, () => [] as MyHairStyle[]),
      movesApi.mine().then((r) => r.files, () => [] as MyMoveFile[]),
    ]).then(([addons, hair, moves]) => {
      if (live) setLib({ addons, hair, moves })
    })
    return () => {
      live = false
    }
  }, [])
  if (!lib) return null
  const none = lib.addons.length + lib.hair.length + lib.moves.length === 0
  const from = (source: string, licence?: string) => [source ? `Where it came from: ${source}` : 'Where it came from: not written down', licence && licence !== 'Not stated' ? `licence: ${licence}` : null].filter(Boolean).join(', ')
  return (
    <section className="your-library" aria-labelledby="your-library-title">
      <h2 id="your-library-title">Your library</h2>
      <p className="muted small">What you added yourself, next to the ready-made ones. You add and remove these where they are used: painter add-ons in step 1, hairstyles in step 3, moves in step 5.</p>
      {none && <p className="small">You have not added anything yet.</p>}
      {lib.addons.length > 0 && (
        <div className="your-library-group">
          <div className="field-label">Painter add-ons ({lib.addons.length})</div>
          <ul className="small">
            {lib.addons.map((a) => (
              <li key={a.id}>
                <strong>{a.label}</strong> <span className="muted">{from(a.source, a.licence)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {lib.hair.length > 0 && (
        <div className="your-library-group">
          <div className="field-label">Hairstyles ({lib.hair.length})</div>
          <ul className="small">
            {lib.hair.map((h) => (
              <li key={h.id}>
                <strong>{h.name}</strong> <span className="muted">{h.family_label.toLowerCase()}, {from(h.source, h.licence)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {lib.moves.length > 0 && (
        <div className="your-library-group">
          <div className="field-label">Moves ({lib.moves.length} {lib.moves.length === 1 ? 'file' : 'files'})</div>
          <ul className="small">
            {lib.moves.map((m) => (
              <li key={m.id}>
                <strong>{m.label}</strong> <span className="muted">{m.clips.length} {m.clips.length === 1 ? 'move' : 'moves'}, {from(m.source)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
