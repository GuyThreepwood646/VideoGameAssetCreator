// Gallery: every finished asset across projects (GET /api/gallery), newest first.
// Each card opens the asset and shows the folder its files were saved to.
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { api, ApiError, libraryUrl, type GalleryItem } from '../api'
import { Info } from '../components/Info'
import { ErrorText, timeAgo, useAsync } from '../components/ui'
import { useGlbSnapshot } from '../glbSnapshot'
import { CATEGORY_HELP, platformPlain, STAGE_TEXT, STYLE_HELP } from '../help'
import { categoryLabel, useRefData } from '../refdata'
import { lookLabelForStyle, usePresets } from '../presets'

export default function GalleryPage() {
  const { data: items, error, loading } = useAsync(() => api.gallery(), [])
  const { ref } = useRefData()
  const [project, setProject] = useState('')

  const projects = useMemo(() => {
    const seen = new Map<string, string>()
    for (const it of items ?? []) if (!seen.has(it.project_id)) seen.set(it.project_id, it.project_name)
    return [...seen.entries()]
  }, [items])
  const shown = (items ?? []).filter((it) => !project || it.project_id === project)
  const missing = error instanceof ApiError && error.status === 404

  return (
    <div className="gallery-page">
      <div className="row-between">
        <h1 className="page-title">
          Gallery
          <Info term="term.gallery" />
        </h1>
        {projects.length > 1 && (
          <label className="row-gap small">
            <span className="muted">Project</span>
            <select value={project} onChange={(e) => setProject(e.target.value)} aria-label="Show assets from this project">
              <option value="">All projects</option>
              {projects.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {missing ? <div className="placeholder-box">This backend does not have a gallery yet. Finished assets are still on each project's page.</div> : <ErrorText error={error} />}
      {loading && !items && <div className="muted">Loading...</div>}
      {items && items.length === 0 && <EmptyGallery />}
      {shown.length > 0 && (
        <>
          <p className="muted small">
            {shown.length} finished asset{shown.length > 1 ? 's' : ''}, newest first. Click one to open it, or copy the folder its files were saved to.
          </p>
          <div className="gallery-grid">
            {shown.map((it) => (
              <GalleryCard key={it.asset_id} item={it} categoryName={ref?.categories.some((c) => c.id === it.category) ? categoryLabel(ref, it.category) : (CATEGORY_HELP[it.category]?.plain ?? it.category)} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function EmptyGallery() {
  return (
    <div className="empty-state">
      <h2 className="card-title">Nothing finished yet</h2>
      <p className="muted">
        An asset shows up here once its last step, "{STAGE_TEXT.export.plain}", has finished. That step packs the model, textures, skeleton and animations into files your game
        can load and copies them to a folder on this computer.
      </p>
      <p className="muted">
        To get there: open a <Link to="/">project</Link>, describe an asset, and let it run to the end (the "Build it automatically" box does every step for you).
      </p>
    </div>
  )
}

function GalleryCard({ item, categoryName }: { item: GalleryItem; categoryName: string }) {
  const { presets } = usePresets()
  const thumbUrl = libraryUrl(item.thumbnail)
  // No picture: fall back to a still of the finished model.
  const { src: snap } = useGlbSnapshot(thumbUrl ? undefined : libraryUrl(item.preview ?? item.glb))
  const img = thumbUrl ?? snap
  const when = item.finished_at ?? item.created_at
  const to = `/assets/${item.asset_id}/export`
  const style = lookLabelForStyle(presets, item.style_preset, STYLE_HELP[item.style_preset]?.plain ?? item.style_preset)
  return (
    <article className="gallery-card">
      <Link to={to} className="gallery-open" aria-label={`Open ${item.asset_name}`}>
        <div className="asset-thumb checker">{img ? <img src={img} alt="" loading="lazy" /> : <span className="muted small">no picture</span>}</div>
        <div className="asset-name">{item.asset_name}</div>
      </Link>
      <div className="muted small">
        <Link to={`/projects/${item.project_id}`}>{item.project_name}</Link>
      </div>
      <div className="chips">
        <span className="chip" title={item.category}>
          {categoryName}
        </span>
        <span className="chip" title={item.platform_target}>
          {platformPlain(item.platform_target).split(':')[0]}
        </span>
        {style && (
          <span className="chip" title={item.style_preset}>
            {style}
          </span>
        )}
      </div>
      <div className="muted small" title={when ? new Date(when).toLocaleString() : undefined}>
        Finished {when ? `${new Date(when).toLocaleDateString()} (${timeAgo(when)})` : 'at an unknown time'}
      </div>
      <DeliveredRow path={item.delivered_to} />
      {(item.fbx || item.glb) && (
        <div className="files-row small">
          <span className="muted">files:</span>
          {item.fbx && (
            <a href={libraryUrl(item.fbx)} download={item.fbx.split('/').pop()} title={item.fbx}>
              FBX
            </a>
          )}
          {item.glb && (
            <a href={libraryUrl(item.glb)} download={item.glb.split('/').pop()} title={item.glb}>
              GLB
            </a>
          )}
        </div>
      )}
    </article>
  )
}

function DeliveredRow({ path }: { path: string | null }) {
  const [copied, setCopied] = useState<'ok' | 'fail' | null>(null)
  useEffect(() => {
    if (!copied) return
    const t = window.setTimeout(() => setCopied(null), 2000)
    return () => window.clearTimeout(t)
  }, [copied])
  if (!path) return <div className="muted small">Not copied to a folder (the backend did not report one).</div>
  return (
    <div className="delivered">
      <div className="field-label small">
        Saved to
        <Info term="term.delivered_to" />
      </div>
      <div className="delivered-row">
        <code className="delivered-path mono small" title={path}>
          {path}
        </code>
        <button type="button" className="small-btn" onClick={() => copyText(path).then((ok) => setCopied(ok ? 'ok' : 'fail'))} aria-label={`Copy the folder path ${path}`}>
          {copied === 'ok' ? 'Copied' : copied === 'fail' ? 'Copy failed' : 'Copy'}
        </button>
      </div>
    </div>
  )
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API blocked (plain http on another host): fall back to a hidden textarea.
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}
