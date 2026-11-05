// Glossary: the words the app uses, in plain language. The text comes from the
// help registry (src/help.ts) so every tooltip and this page say the same thing.
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { STAGES, type StageId } from '../api'
import { helpFor, STAGE_TEXT, type HelpEntry } from '../help'

type Item = { id: string; term: string; title?: string; also?: string }
type Group = { title: string; intro: string; items: Item[] }

const GROUPS: Group[] = [
  {
    title: 'How your work is organised',
    intro: 'A project holds assets; each asset goes through six steps; every attempt at a step is a version.',
    items: [
      { id: 'project', term: 'term.project' },
      { id: 'asset', term: 'term.asset' },
      { id: 'version', term: 'term.version' },
      { id: 'variant', term: 'term.variant' },
      { id: 'style-lock', term: 'term.style_lock' },
      { id: 'platform', term: 'term.platform', title: 'Platform target' },
      { id: 'autopilot', term: 'term.autopilot' },
      { id: 'feedback', term: 'term.feedback', title: 'Thumbs up and down' },
    ],
  },
  {
    title: 'Painting the picture',
    intro: 'The first step paints a picture with one of the painters; add-ons push it toward a look.',
    items: [
      { id: 'addons', term: 'term.addons', title: 'Add-ons (LoRAs)' },
      { id: 'addon-mine', term: 'term.addon_mine' },
      { id: 'addon-painter', term: 'term.addon_painter' },
      { id: 'addon-trigger', term: 'term.addon_trigger' },
    ],
  },
  {
    title: 'The six steps',
    intro: 'Every asset goes through these in order. The technical name is the one in the page address.',
    items: [
      { id: 'stage', term: 'term.stage' },
      ...STAGES.map((s: StageId) => ({ id: 'stage-' + s, term: 'stage.' + s, title: `${STAGE_TEXT[s].plain} (${s})` })),
    ],
  },
  {
    title: '3D words',
    intro: 'The words you meet once there is a 3D model.',
    items: [
      { id: 'triangles', term: 'term.triangles' },
      { id: 'triangle-budget', term: 'term.triangle_budget' },
      { id: 'texture', term: 'term.texture' },
      { id: 'normal-map', term: 'term.normal_map' },
      { id: 'rig', term: 'term.rig' },
      { id: 'skeleton', term: 'term.skeleton' },
      { id: 'skin-weights', term: 'term.skin_weights' },
      { id: 'lod', term: 'term.lod', title: 'LOD (detail levels)' },
      { id: 'collider', term: 'term.collider', title: 'Collider (collision shape)' },
      { id: 'attachment-point', term: 'term.socket', title: 'Attachment point (socket)' },
      { id: 'items', term: 'term.items', title: 'Items (held and carried things)' },
      { id: 'moves-from', term: 'term.moves_from', title: 'Where moves come from (animation library)' },
      { id: 'preview-light', term: 'term.preview_light', title: 'Preview light (environment light)' },
      { id: 'beauty-render', term: 'term.beauty_render', title: 'Beauty picture (beauty render)' },
    ],
  },
]

type Row = Item & { entry: HelpEntry; heading: string }

function rowsOf(group: Group): Row[] {
  const out: Row[] = []
  for (const it of group.items) {
    const entry = helpFor(it.term)
    if (entry) out.push({ ...it, entry, heading: it.title ?? entry.plain })
  }
  return out
}

function matches(r: Row, q: string): boolean {
  if (!q) return true
  const hay = [r.heading, r.also, r.entry.plain, r.entry.meaning, r.entry.why, r.entry.technical].join(' ').toLowerCase()
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w))
}

export default function GlossaryPage() {
  const [q, setQ] = useState('')
  const groups = useMemo(() => GROUPS.map((g) => ({ ...g, rows: rowsOf(g).filter((r) => matches(r, q.trim())) })), [q])
  const total = groups.reduce((n, g) => n + g.rows.length, 0)

  return (
    <div className="page-narrow glossary">
      <h1 className="page-title">Glossary</h1>
      <p className="muted">
        The words this app uses, in plain language. The same explanations appear when you hover any "i" next to a label. Each entry ends with the technical name, in case you
        want to look it up.
      </p>
      <label className="field glossary-search">
        <span className="field-label">Find a word</span>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. texture, skeleton, budget" aria-label="Find a word in the glossary" />
      </label>
      {total === 0 && <div className="placeholder-box">No word matches "{q}". Try a shorter word.</div>}
      {!q && (
        <nav className="glossary-index small" aria-label="Glossary sections">
          {groups.map((g) => (
            <a key={g.title} href={'#' + slug(g.title)}>
              {g.title}
            </a>
          ))}
        </nav>
      )}
      {groups
        .filter((g) => g.rows.length > 0)
        .map((g) => (
          <section key={g.title} className="glossary-group" id={slug(g.title)}>
            <h2 className="section-title">{g.title}</h2>
            <p className="muted small">{g.intro}</p>
            <dl className="glossary-list">
              {g.rows.map((r) => (
                <div key={r.id} className="glossary-item" id={'term-' + r.id}>
                  <dt>
                    {r.heading}
                    {r.also && r.also !== r.heading && <span className="muted small"> (shown in the app as "{r.also}")</span>}
                  </dt>
                  <dd>
                    <div>{r.entry.meaning}</div>
                    <div className="muted">Why it matters: {r.entry.why}</div>
                    <div className="muted small">
                      Technical name: <span className="mono">{r.entry.technical}</span>
                    </div>
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      <p className="muted small">
        Ready to make something? <Link to="/">Go to your projects</Link>, or see everything you have finished in the <Link to="/gallery">gallery</Link>.
      </p>
    </div>
  )
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}
