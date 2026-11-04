import { useState } from 'react'
import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { modelLabel } from '../../help'

export function ConceptView({ version }: { version: Version }) {
  const [cutout, setCutout] = useState(false)
  const image = libraryUrl(version.files.image)
  const cut = libraryUrl(version.files.cutout)
  const src = cutout && cut ? cut : image
  return (
    <div className="concept-view">
      <div className="viewer-toolbar">
        <button type="button" aria-pressed={!cutout} onClick={() => setCutout(false)}>
          Picture
        </button>
        <button type="button" aria-pressed={cutout} disabled={!cut} onClick={() => setCutout(true)}>
          Cutout
        </button>
        <Info term="term.cutout" />
        <span className="viewer-status">
          {version.params.model ? (
            <>
              painted by {modelLabel(String(version.params.model))}
              <Info term={'model.' + String(version.params.model)} />
            </>
          ) : null}
          {version.params.seed !== undefined && version.params.seed !== null ? (
            <>
              , seed {String(version.params.seed)}
              <Info term="term.seed" />
            </>
          ) : null}
          {version.params.instruction ? `, change: "${String(version.params.instruction)}"` : ''}
        </span>
      </div>
      <div className="concept-body single">
        <div className={'concept-image' + (cutout ? ' checker' : '')}>{src ? <img src={src} alt="concept" /> : <span className="muted">no image</span>}</div>
      </div>
    </div>
  )
}
