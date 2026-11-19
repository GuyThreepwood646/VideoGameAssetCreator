// Feature photos in the New asset form: the person's own, sharper pictures of a face, a body, an outfit or anything
// else. The backend (vgac/compose.py, POST /api/projects/{pid}/assets with `features`) makes ONE step 1 picture from
// all of them, and keeps a human's face picture as the face close-up of step 4. With a 3D model the person already has,
// nothing is drawn, so only the face is offered (it goes to the step 4 face pass).
// For a human, each photo that is not the Face picture is checked for people (POST /api/people): a box round the main
// person is suggested, the person can change it or use the whole photo, and the whole photo gets a warning when more
// than one person is in it. None of this ever blocks the form.
import { useEffect, useRef, useState } from 'react'
import { api, type FeatureRole, type PeopleResult, type PersonBox } from '../api'
import {
  cropsFace,
  featureEffect,
  featureRoles,
  looksForPeople,
  MAX_FEATURES,
  peopleLine,
  slot,
  usedFeatures,
  WHOLE_WARNING,
  wholeWithOthers,
  foundPeople,
  type FeatureSlot,
  type StartFrom,
} from '../featurePhotos'
import { FaceCrop } from './FaceCrop'
import { ImageDrop, type PickedImage } from './ImageDrop'
import { Info } from './Info'
import { PersonCrop } from './PersonCrop'

const ROLE_LABEL: Record<FeatureRole, string> = { face: 'Face', body: 'Body', outfit: 'Outfit', other: 'Something else' }
const ROLE_HINT: Record<FeatureRole, string> = {
  face: 'The face, head and hair to use. A clear, front-on face works best.',
  body: 'The build and proportions: height, shoulders, how heavy or slim.',
  outfit: 'The clothes, armour or gear to dress the character in.',
  other: 'Anything else to include: a cape, a tattoo, a colour, a creature part.',
}
const ADD_LABEL: Record<FeatureRole, string> = { face: 'A face', body: 'A body', outfit: 'An outfit', other: 'Something else' }

export function FeaturePhotos({ slots, onChange, startFrom, category, disabled = false }: { slots: FeatureSlot[]; onChange: (slots: FeatureSlot[]) => void; startFrom: StartFrom; category: string; disabled?: boolean }) {
  const [cropFrom, setCropFrom] = useState<PickedImage | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const roles = featureRoles(startFrom, category)
  const shown = slots.filter((s) => roles.includes(s.role))
  const used = usedFeatures(slots, startFrom, category)
  const set = (key: number, patch: Partial<FeatureSlot>) => onChange(slots.map((s) => (s.key === key ? { ...s, ...patch } : s)))
  // The crop goes into the first empty Face slot, else a new one at the front.
  const addFace = (face: PickedImage) => {
    const empty = slots.find((s) => s.role === 'face' && !s.picture)
    if (empty) onChange(slots.map((s) => (s.key === empty.key ? { ...s, picture: face } : s)))
    else onChange([slot('face', face), ...slots].slice(0, MAX_FEATURES))
  }
  const hasFace = used.some((s) => s.role === 'face')

  // Look for the people in each new photo of a human, once, and suggest the box round the main person. The latest slots
  // are read from a ref so a result is written back to the form as it is now, not as it was when the look began.
  const latest = useRef(slots)
  useEffect(() => {
    latest.current = slots
  })
  useEffect(() => {
    if (category !== 'humanoid' || startFrom === 'model') return
    slots
      .filter((s) => s.picture && looksForPeople(s.role, category) && s.people === undefined)
      .forEach((s) => {
        const write = (patch: Partial<FeatureSlot>) => onChange(latest.current.map((x) => (x.key === s.key ? { ...x, ...patch } : x)))
        write({ people: 'pending' })
        api.findPeople(s.picture!.dataUrl).then(
          (res: PeopleResult) => write({ people: res, box: res.suggested ?? null, whole: false }),
          () => write({ people: null, box: null }),
        )
      })
  }, [slots, category, startFrom, onChange])

  if (startFrom === 'model' && !roles.length) {
    return (
      <div className="field feature-photos">
        <span className="field-label">
          Feature photos
          <Info term="term.feature_photos" />
        </span>
        <span className="field-hint">Not used for a model you already have: nothing is drawn for it, so a photo of a body, an outfit or a face has nothing to change. For a human character, a face photo can sharpen the face.</span>
      </div>
    )
  }
  if (!roles.length) return null
  const editSlot = editing === null ? null : slots.find((s) => s.key === editing) ?? null
  return (
    <div className="field feature-photos" role="group" aria-label="Feature photos">
      <span className="field-label">
        Feature photos (optional)
        <Info term="term.feature_photos" />
      </span>
      <span className="field-hint">
        {startFrom === 'model'
          ? 'A sharper photo of the face. Your model is not redrawn; the face pass in step 4 paints the face from this photo. Body, outfit and other photos are not offered here: they change the drawing in step 1, which a model you already have skips.'
          : startFrom === 'picture'
            ? 'Sharper photos of parts of the character: a face, a body, an outfit. Step 1 then draws one picture from your picture and these together. A face photo alone leaves your picture as it is and sharpens the face in step 4.'
            : 'Your own photos of parts of the character: a face, a body, an outfit. Step 1 then draws one picture from all of them and your description, instead of painting from words alone.'}{' '}
        Your pictures stay on this computer.
      </span>
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
      {editSlot?.picture && editSlot.box && (
        <PersonCrop
          picture={editSlot.picture}
          box={editSlot.box}
          suggested={editSlot.people && editSlot.people !== 'pending' ? editSlot.people.suggested : null}
          onClose={() => setEditing(null)}
          onDone={(box: PersonBox) => {
            set(editSlot.key, { box, whole: false })
            setEditing(null)
          }}
        />
      )}
      {shown.length > 0 && (
        <div className="feature-slots">
          {shown.map((s) => (
            <div key={s.key} className="feature-slot">
              <div className="row-between">
                {roles.length > 1 ? (
                  <select value={s.role} aria-label="What this photo is for" disabled={disabled} onChange={(e) => set(s.key, { role: e.target.value as FeatureRole, people: undefined, box: null, whole: false })}>
                    {roles.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span />
                )}
                <button type="button" className="link small" disabled={disabled} onClick={() => onChange(slots.filter((x) => x.key !== s.key))}>
                  remove
                </button>
              </div>
              <ImageDrop
                compact
                value={s.picture}
                onChange={(p) => set(s.key, { picture: p, people: undefined, box: null, whole: false })}
                label={ROLE_LABEL[s.role]}
                hint={ROLE_HINT[s.role]}
                disabled={disabled}
              />
              {s.picture && s.role !== 'face' && roles.includes('face') && cropsFace(s.role, category) && (
                <button type="button" className="link small" disabled={disabled} onClick={() => setCropFrom(s.picture)} title="Drag a box around the face; the crop is added as the Face photo">
                  Crop the face from it
                </button>
              )}
              {s.picture && looksForPeople(s.role, category) && <PeopleNote slot={s} disabled={disabled} onEdit={() => setEditing(s.key)} onWhole={() => set(s.key, { whole: true })} onSuggested={() => set(s.key, { whole: false, box: s.people && s.people !== 'pending' ? s.people.suggested : null })} />}
              {startFrom !== 'model' && (
                <input value={s.note} maxLength={200} disabled={disabled} onChange={(e) => set(s.key, { note: e.target.value })} placeholder="What to take from it (optional)" aria-label={`What to take from the ${ROLE_LABEL[s.role].toLowerCase()} photo`} />
              )}
            </div>
          ))}
        </div>
      )}
      {shown.length < (startFrom === 'model' ? 1 : MAX_FEATURES) && (
        <div className="row-gap feature-add">
          <span className="muted small">Add:</span>
          {roles.map((r) => (
            <button key={r} type="button" className="small-btn" disabled={disabled} onClick={() => onChange([...slots, slot(r)])}>
              {ADD_LABEL[r]}
            </button>
          ))}
        </div>
      )}
      {!hasFace && used.length > 0 && roles.includes('face') && category === 'humanoid' && (
        <div className="tool-note small">
          For a likeness, give the face its own photo. With a photo of the whole person, use "Crop the face from it" under the photo: a close crop of the face carries the person's features much better than the whole photo does.
        </div>
      )}
      {used.length > 0 && <div className="muted small feature-effect">{featureEffect(used, startFrom, category)}</div>}
    </div>
  )
}

// The line under a photo of a human: what was found, the box to use, and the whole-photo warning when it applies.
function PeopleNote({ slot: s, disabled, onEdit, onWhole, onSuggested }: { slot: FeatureSlot; disabled: boolean; onEdit: () => void; onWhole: () => void; onSuggested: () => void }) {
  if (s.people === 'pending') return <div className="muted small">Looking for people in this photo...</div>
  if (!s.people || s.people.status !== 'ok') return null // none, unavailable or failed: the whole photo, with no message
  const found = foundPeople(s)
  if (!found.length) return null
  const suggested = s.people.suggested
  return (
    <div className="feature-people small">
      <div>{peopleLine(found)}</div>
      <div className="row-gap">
        {s.whole ? (
          suggested && (
            <button type="button" className="small-btn" disabled={disabled} onClick={onSuggested}>
              Use the suggested box
            </button>
          )
        ) : (
          <>
            <button type="button" className="small-btn" disabled={disabled} onClick={onEdit}>
              Edit the box
            </button>
            <button type="button" className="small-btn" disabled={disabled} onClick={onWhole}>
              Use the whole photo
            </button>
          </>
        )}
      </div>
      {wholeWithOthers(s, 'humanoid') && <div className="tool-note small">{WHOLE_WARNING}</div>}
    </div>
  )
}
