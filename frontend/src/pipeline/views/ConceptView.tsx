import { useState } from 'react'
import { libraryUrl, type Version } from '../../api'
import { Info } from '../../components/Info'
import { PictureViewer, Zoomable } from '../../components/PictureViewer'
import { modelLabel } from '../../help'
import { useRefData } from '../../refdata'
import { addonsNotUsed } from '../Addons'
import { PromptSent } from '../Brief'

export function ConceptView({ version }: { version: Version }) {
  const [cutout, setCutout] = useState(false)
  const [full, setFull] = useState(false)
  const image = libraryUrl(version.files.image)
  const cut = libraryUrl(version.files.cutout)
  const src = cutout && cut ? cut : image
  // Add-ons this run asked for that the painter could not use, or that the app does not know.
  const { ref } = useRefData()
  const catalog = ref?.stages.find((s) => s.id === 'concept')?.adjust_schema.find((p) => p.type === 'addons')?.catalog
  const notUsed = addonsNotUsed(version, catalog)
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
          {version.params.source === 'upload' || version.params.source === 'upload_edit' ? (
            <>
              {version.params.source === 'upload' ? 'Your picture' : 'Your picture, changed'}
              <Info term="term.your_picture" />
            </>
          ) : version.params.source === 'from_model' ? (
            <>
              a picture of your 3D model
              <Info term="term.own_model" />
            </>
          ) : version.params.model ? (
            <>
              painted by {modelLabel(String(version.params.model))}
              <Info term={'model.' + String(version.params.model)} />
            </>
          ) : null}
          {version.params.source !== 'upload' && version.params.source !== 'from_model' && version.params.seed !== undefined && version.params.seed !== null ? (
            <>
              , picture number {String(version.params.seed)} <small className="muted">(seed)</small>
              <Info term="term.seed" />
            </>
          ) : null}
          {version.params.instruction ? `, change: "${String(version.params.instruction)}"` : ''}
        </span>
      </div>
      <div className="concept-body single">
        {src ? (
          <Zoomable className={'concept-image' + (cutout ? ' checker' : '')} label={cutout ? 'the cutout' : 'the picture'} onOpen={() => setFull(true)}>
            <img src={src} alt="concept" />
          </Zoomable>
        ) : (
          <div className="concept-image">
            <span className="muted">no image</span>
          </div>
        )}
      </div>
      {full && src && <PictureViewer items={[{ src, label: cutout ? 'Cutout' : 'Picture' }]} checker={cutout} onClose={() => setFull(false)} />}
      <PromptSent version={version} />
      {notUsed && (
        <div className="muted small">
          {notUsed}
          <Info term="term.addons" />
        </div>
      )}
    </div>
  )
}
