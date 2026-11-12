// Step 0.5: a character from several of your own pictures. The person drops a face, a body, an outfit and anything
// else; the backend (vgac/compose.py) draws new full-body pictures from them in the project's look, and each one
// arrives in step 1 as a normal first picture to keep.
import { PanelClose } from '../components/ui'
import { useState } from 'react'
import { api, type Asset, type ComposeRole } from '../api'
import { FaceCrop } from '../components/FaceCrop'
import { ImageDrop, type PickedImage } from '../components/ImageDrop'
import { Info } from '../components/Info'

export const COMPOSE_CATEGORIES = ['humanoid', 'quadruped', 'multileg', 'winged', 'serpent']
const MAX_PICTURES = 6

type Slot = { key: number; role: ComposeRole; picture: PickedImage | null; note: string }

const ROLE_LABEL: Record<ComposeRole, string> = { face: 'Face', body: 'Body', outfit: 'Outfit', other: 'Something else' }
const ROLE_HINT: Record<ComposeRole, string> = {
  face: 'The face, head and hair to use. A clear, front-on face works best.',
  body: 'The build and proportions: height, shoulders, how heavy or slim.',
  outfit: 'The clothes, armour or gear to dress the character in.',
  other: 'Anything else to include: a cape, a tattoo, a colour, a creature part.',
}

let nextKey = 1
const slot = (role: ComposeRole): Slot => ({ key: nextKey++, role, picture: null, note: '' })

export function ComposePanel({ asset, onClose, onCreated, onError }: { asset: Asset; onClose: () => void; onCreated: () => void; onError: (e: unknown) => void }) {
  const [slots, setSlots] = useState<Slot[]>(() => [slot('face'), slot('body'), slot('outfit')])
  const [words, setWords] = useState('')
  // The Realistic look gets more tries: the likeness varies a lot from one result to the next.
  const photoreal = asset.style_preset === 'realistic_pbr'
  const [count, setCount] = useState(photoreal ? 4 : 2)
  const [cropFrom, setCropFrom] = useState<PickedImage | null>(null)
  const hasFace = slots.some((s) => s.role === 'face' && s.picture)
  // The crop goes into the first empty Face slot, else a new one at the front.
  const addFace = (face: PickedImage) =>
    setSlots((list) => {
      const empty = list.find((s) => s.role === 'face' && !s.picture)
      if (empty) return list.map((s) => (s.key === empty.key ? { ...s, picture: face } : s))
      return [{ ...slot('face'), picture: face }, ...list].slice(0, MAX_PICTURES)
    })
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState<number | null>(null)
  const filled = slots.filter((s) => s.picture)
  const set = (key: number, patch: Partial<Slot>) => setSlots((list) => list.map((s) => (s.key === key ? { ...s, ...patch } : s)))

  const send = async () => {
    if (!filled.length) return
    setBusy(true)
    try {
      const r = await api.composeConcept(asset.id, {
        pictures: filled.map((s) => ({ role: s.role, image: s.picture!.dataUrl, ...(s.note.trim() ? { note: s.note.trim() } : {}) })),
        ...(words.trim() ? { words: words.trim() } : {}),
        count,
      })
      setSent(r.versions.length)
      onCreated()
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="variant-panel compose-panel" aria-label="Step 0.5: a character from your pictures">
      <div className="row-between">
        <div className="field-label">
          Step 0.5: make the character from your pictures
          <Info term="term.compose" />
        </div>
        <PanelClose onClose={onClose} />
      </div>
      <div className="muted small">
        Add a picture for each part you want to use. The app draws one new full-body character from them, standing ready for 3D and in this project's look. Each result appears in step 1, where you keep the one you like; the other steps then carry on as usual. The drawing model looks at three pictures at most: with more, the extra ones share one sheet and come through less exactly.
      </div>
      <div className="muted small">Your pictures stay on this computer.</div>
      {!hasFace && filled.length > 0 && (
        <div className="tool-note small">
          For a likeness, give the face its own picture. With a photo of the whole person, use "Crop the face from it" under the picture: a close crop of the face carries the person's features much better than the whole photo does.
        </div>
      )}
      {cropFrom && (
        <FaceCrop
          picture={cropFrom}
          onClose={() => setCropFrom(null)}
          onDone={(face) => {
            addFace(face)
            setCropFrom(null)
          }}
        />
      )}
      <div className="compose-slots">
        {slots.map((s) => (
          <div key={s.key} className="compose-slot">
            <div className="row-between">
              <select value={s.role} aria-label="What this picture is for" disabled={busy} onChange={(e) => set(s.key, { role: e.target.value as ComposeRole })}>
                {(Object.keys(ROLE_LABEL) as ComposeRole[]).map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
              {slots.length > 1 && (
                <button type="button" className="link small" disabled={busy} onClick={() => setSlots((list) => list.filter((x) => x.key !== s.key))}>
                  remove
                </button>
              )}
            </div>
            <ImageDrop compact value={s.picture} onChange={(p) => set(s.key, { picture: p })} label={ROLE_LABEL[s.role]} hint={ROLE_HINT[s.role]} disabled={busy} />
            {s.picture && s.role !== 'face' && (
              <button type="button" className="link small" disabled={busy} onClick={() => setCropFrom(s.picture)} title="Drag a box around the face; the crop is added as the Face picture">
                Crop the face from it
              </button>
            )}
            <input value={s.note} maxLength={200} disabled={busy} onChange={(e) => set(s.key, { note: e.target.value })} placeholder="What to take from it (optional)" aria-label={`What to take from the ${ROLE_LABEL[s.role].toLowerCase()} picture`} />
          </div>
        ))}
      </div>
      {slots.length < MAX_PICTURES && (
        <div className="row-gap">
          <button type="button" className="small-btn" disabled={busy} onClick={() => setSlots((list) => [...list, slot('other')])}>
            Add another picture
          </button>
        </div>
      )}
      <label className="field">
        <span className="field-label">Describe the character (optional)</span>
        <textarea rows={2} value={words} maxLength={1000} disabled={busy} onChange={(e) => setWords(e.target.value)} placeholder={asset.prompt.trim() || 'e.g. a tall ranger with a green hooded cloak'} />
      </label>
      <div className="row-gap">
        <button type="button" className="primary" disabled={busy || !filled.length} onClick={() => void send()} title={filled.length ? 'Draw the character from these pictures' : 'Add at least one picture first'}>
          {busy ? 'Sending...' : `Make the character from ${filled.length || 'your'} picture${filled.length === 1 ? '' : 's'}`}
        </button>
        <select value={count} onChange={(e) => setCount(Number(e.target.value))} aria-label="How many results" disabled={busy}>
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={n}>
              {n} result{n > 1 ? 's' : ''}
            </option>
          ))}
        </select>
        {sent !== null && <span className="muted small">{sent === 1 ? 'One picture is' : `${sent} pictures are`} being drawn in step 1.</span>}
      </div>
    </section>
  )
}
