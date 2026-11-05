// The finish card's beauty picture (PHOTOREAL-PLAN phase 1): the finished model rendered in Blender under the same
// outdoor light as the viewer's "Outdoors" choice. Shown only when the export made one.
import { libraryUrl, type Version } from '../api'
import { Info } from './Info'

export function BeautyPicture({ version, name }: { version: Version; name: string }) {
  const src = libraryUrl(version.files.beauty)
  if (!src) return null
  return (
    <figure className="beauty-picture">
      <a href={src} target="_blank" rel="noreferrer" title="Open the full-size picture">
        <img src={src} alt={`${name} in outdoor light`} loading="lazy" />
      </a>
      <figcaption className="muted small">
        A picture to show people, in outdoor light. Click to open it full size.
        <Info term="term.beauty_render" />
      </figcaption>
    </figure>
  )
}
