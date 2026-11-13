// What the person asked for, kept in view (owner, 2026-11-12: "after I enter the initial image, or description,
// there's not a way for me to see or interact with the prompt I provided"). Spec: docs/PROMPT-AND-IMPORT-SPEC.md.
//   - DescriptionEdit: the asset's name or description, editable in place. A changed description offers a new step 1
//     picture. Used by the left rail (AssetPage) and by the step 1 card below, so both behave the same.
//   - BriefCard: the top of step 1. The description in full (editable), and the starting picture when the asset was
//     started from one.
//   - PromptSent: under a step 1 picture, the full prompt the painter was given for that version (the description
//     plus what the app added: the look, the pose for 3D, hair, palette, parts, face), with a Copy button.
import { useState } from 'react'
import { api, libraryUrl, versionsOf, type Asset, type Version } from '../api'
import { Info } from '../components/Info'
import { PictureViewer, Zoomable } from '../components/PictureViewer'
import { Disclosure } from '../components/ui'

export function DescriptionEdit({
  asset,
  field,
  onSaved,
  onError,
  emptyText,
  rows = 4,
}: {
  asset: Asset
  field: 'name' | 'prompt'
  onSaved: () => Promise<unknown> | void
  onError: (e: unknown) => void
  emptyText?: string
  rows?: number
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [asked, setAsked] = useState(false)
  const [busy, setBusy] = useState(false)
  const current = (field === 'name' ? asset.name : asset.prompt) ?? ''
  const isName = field === 'name'
  const save = async (thenRun: boolean) => {
    setBusy(true)
    try {
      await api.updateAsset(asset.id, { [field]: text.trim() })
      if (thenRun) await api.runStage(asset.id, 'concept', {})
      await onSaved()
      setEditing(false)
      setAsked(false)
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }
  if (!editing) {
    const empty = !current.trim()
    return (
      <div className={isName ? 'asset-title rail-edit' : 'small rail-edit'}>
        {empty && emptyText ? <span className="muted">{emptyText}</span> : <span className="brief-text">{current}</span>}
        <button
          type="button"
          className="link small rail-edit-btn"
          aria-label={isName ? 'Rename this asset' : empty ? 'Add a description' : 'Edit the description'}
          onClick={() => {
            setText(current)
            setEditing(true)
          }}
        >
          {empty && !isName ? 'Add' : 'Edit'}
        </button>
      </div>
    )
  }
  if (asked) {
    return (
      <div className="col-tight small" role="group" aria-label="Save the description">
        <div>Make a new step 1 picture from this description?</div>
        <div className="row-gap">
          <button type="button" className="primary small-btn" disabled={busy} onClick={() => save(true)}>
            Make it
          </button>
          <button type="button" className="small-btn" disabled={busy} onClick={() => save(false)}>
            Just save the text
          </button>
        </div>
      </div>
    )
  }
  // A name cannot be empty; a description can be cleared (a picture-started asset may have none).
  const ok = isName ? text.trim().length > 0 : true
  const submit = () => {
    if (!ok || text.trim() === current.trim()) return setEditing(false)
    if (isName || !text.trim()) void save(false)
    else setAsked(true)
  }
  return (
    <div className="col-tight">
      {isName ? (
        <input
          value={text}
          autoFocus
          aria-label="Asset name"
          maxLength={200}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <textarea
          value={text}
          rows={rows}
          autoFocus
          aria-label="Description"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setEditing(false)
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submit()
          }}
        />
      )}
      <div className="row-gap">
        <button type="button" className="primary small-btn" disabled={busy || !ok} onClick={submit}>
          Save
        </button>
        <button type="button" className="small-btn" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// The picture the asset was started from: the oldest step 1 version made from the person's own picture.
function startingPicture(asset: Asset): Version | undefined {
  const own = versionsOf(asset, 'concept').filter((v) => (v.params?.source === 'upload' || v.params?.source === 'from_model') && (v.files.original || v.files.image))
  return own.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))[0]
}

export function BriefCard({ asset, onSaved, onError }: { asset: Asset; onSaved: () => Promise<unknown> | void; onError: (e: unknown) => void }) {
  const start = startingPicture(asset)
  const [full, setFull] = useState(false)
  const src = start ? libraryUrl(start.files.original || start.files.image) : null
  const fromModel = start?.params?.source === 'from_model'
  const startLabel = fromModel ? 'Your 3D model' : 'Your starting picture'
  return (
    <section className="brief-card" aria-labelledby="brief-title">
      {src && (
        <div className="brief-picture">
          <Zoomable className="brief-thumb checker" label={startLabel.toLowerCase()} onOpen={() => setFull(true)}>
            <img src={src} alt={fromModel ? 'A picture of the 3D model you started from' : 'The picture you started from'} />
          </Zoomable>
          <span className="muted small">{startLabel}</span>
          {full && <PictureViewer items={[{ src, label: startLabel }]} checker onClose={() => setFull(false)} />}
        </div>
      )}
      <div className="brief-body">
        <h3 id="brief-title" className="field-label">
          What you asked for
          <Info term="term.prompt" />
        </h3>
        <DescriptionEdit
          asset={asset}
          field="prompt"
          rows={5}
          onSaved={onSaved}
          onError={onError}
          emptyText={src ? 'No description. Adding one helps later steps name the items, hair and moving parts.' : 'No description yet.'}
        />
        <span className="muted small">
          {fromModel
            ? 'You started from your own 3D model; the pictures here are taken of it. A painted picture shows the prompt the app sent for it.'
            : 'Each picture below shows the full prompt the app sent for it, under "Prompt sent to the painter".'}
        </span>
      </div>
    </section>
  )
}

export function PromptSent({ version }: { version: Version }) {
  const [copied, setCopied] = useState(false)
  const sent = String(version.metrics?.prompt_final ?? version.params?.prompt ?? '').trim()
  const negative = String(version.params?.negative ?? '').trim()
  const note = String(version.params?.note ?? '').trim()
  const instruction = String(version.params?.instruction ?? '').trim()
  if (version.params?.source === 'from_model') return null
  if (version.params?.source === 'upload') {
    return note ? (
      <div className="muted small prompt-sent">
        Your note with this picture: <span className="brief-text">{note}</span>
      </div>
    ) : null
  }
  if (!sent && !instruction) return null
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sent)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <Disclosure label="Prompt sent to the painter" className="prompt-sent">
      {instruction && (
        <div className="small">
          Change you asked for: <span className="brief-text">{instruction}</span>
        </div>
      )}
      {sent && (
        <>
          <pre className="prompt-text" aria-label="Prompt sent to the painter">
            {sent}
          </pre>
          {negative && (
            <div className="muted small">
              Told to avoid: <span className="brief-text">{negative}</span>
            </div>
          )}
          <div className="row-gap">
            <button type="button" className="small-btn" onClick={() => void copy()}>
              {copied ? 'Copied' : 'Copy'}
            </button>
            <span className="muted small" role="status">
              Your description comes first; the app adds the look, the pose for 3D and the other lines after it. To change it, edit "What you asked for" at the top of this step.
            </span>
          </div>
        </>
      )}
    </Disclosure>
  )
}
