# Projects page (UX-P1)

Status: built 2026-11-06. Owner: UX. Traces to docs/UX-REVIEW.md items 1 to 3 (empty state, looks as presets, plain platform names), which this keeps.

## Inherited decisions

- Desktop tool, dark neutral UI, `body { min-width: 1280px }`, 13 px base type, 8 px spacing scale (`--s1` to `--s5`), card radius 6 px, accent `--accent` for the one primary action, green `--ok` for "in use".
- Plain words: "look" for style lock, platform names from `platformPlain`, an "i" (Info) on every term.
- Ready-made looks through `PresetRow`; the hand-edited style lock stays behind "Advanced".
- Reduce Motion is handled per component in styles.css (`prefers-reduced-motion` blocks); this page adds its own.

## Problems this fixes

1. The New project form sat under the list, so with many projects it was off screen.
2. The right half of the page was an empty dashed box until a project was picked.
3. A first visit showed three paragraphs of definitions and a form; nothing inviting.
4. Projects had no pictures, so they were hard to tell apart.

## Layout

- "Jump back in": the most recently active project's four latest assets, each with its picture, its next step in plain words (or "Ready for your game", "Working: ...", "Needs a look: ...") and a progress bar of steps done.
- Header: title, a count line ("3 projects, 12 assets"), search and sort (search shown from 6 projects up), primary "New project" button. Always at the top.
- Grid of project cards, `minmax(260px, 1fr)`, full page width. The first tile is a dashed "New project" tile (hidden while searching).
- Card: cover (up to four of the newest assets' pictures; with none, the look's palette as stripes and the project's initials), name, look and platform, asset count and last activity, palette swatches. The whole card opens the project. A settings button on the cover opens "Look and settings" in a side drawer (`?edit=<id>` keeps it linkable).
- New project opens a dialog with name, platform and look. Create takes the person straight into the new project, where the next step is adding the first asset.
- First visit (no projects): a welcome panel with the create form in it, the three steps of how the app works, and the "Three words to know" behind a disclosure.

## States

| State | What shows |
|---|---|
| First visit, no projects | Welcome panel, form focused, look presets in full |
| Returning | Grid, sorted by recent activity (sort kept in localStorage) |
| Loading | Three placeholder cards (static under Reduce Motion) |
| Error loading | Error text and Try again |
| Search with no match | "No projects match ..." and Clear search |
| Project with no assets | Palette cover with initials, "No assets yet" |
| Creating | Create button disabled, reads "Creating..." |
| Create failed | Error inside the dialog; dialog stays open, name kept |
| Drawer open | Style lock editor; Esc, backdrop or Close shuts it |
| Delete from drawer | Confirm, then drawer closes and the grid reloads |
| Older backend (no summary fields) | Cards fall back to palette covers and no count |
| Reduce Motion | No hover lift, no shimmer, no slide-in; fades only |
| 200% zoom | Page scrolls sideways (app-wide 1280 px floor); grid reflows to one or two columns |

Phone, tablet, orientation and screen-reader-on-device rows: not applicable (desktop web app); keyboard and labels checked instead.

## Controls

| Control | Size | Notes |
|---|---|---|
| New project (header) | 32 px tall | primary, the only accent-filled button on the page |
| New project tile | card size | dashed, opens the same dialog |
| Project card | whole card | link, focus ring, label "Open <name>" |
| Settings on card | 32 x 32 | "Look and settings for <name>", visible on hover and keyboard focus, always visible on touch |
| Search | 32 px | label "Search projects", Esc clears |
| Sort | 32 px | Recent activity, Name, Newest |
| Dialog and drawer Close | 32 x 32 | Esc also closes; focus returns to the opener |

## Accessibility

- Text pairs, measured: `--text` on `--panel` 11.98:1, `--muted` on `--panel` 5.68:1 and on `--panel-2` 6.05:1, `--accent` links on `--panel` 5.80:1, button ink on `--accent` 7.28:1 (all over 4.5:1). Initials on the palette cover sit on a dark scrim so they pass on any palette.
- Dialog and drawer: `role="dialog"`, `aria-modal`, labelled by their title, focus moves in on open and back on close.
- Information is never colour only: the look is named in words next to the swatches.

## Acceptance

1. With 20 projects, "New project" is visible without scrolling and the dialog opens over the list.
2. With no projects, the first screen has a filled welcome panel and a focused name field.
3. Every card shows a cover; cards with assets show their pictures.
4. The style lock editor opens in a drawer from a card and from `?edit=<id>`.
5. Create opens the new project.
6. Backend: GET /api/projects carries asset_count, covers and last_activity (test_project_list_summary).

## Verification (demo mode, 2026-11-06)

Checked in the in-app browser against `npm run dev:mock`: 1280x800, 1440x900 and 1920x1080; 2, 23 and 0 projects; search, no match, Esc; New project dialog (focus in the name field, look picked, Create opens the new project); drawer from a card and by `?edit=`; Esc closes. Settings button 32 x 32. `npm run build` clean; backend 907 tests pass.

Not verified: the real backend (the summary fields need a backend restart), Reduce Motion with the OS setting on (rules are in place, not seen), 200% zoom, a screen reader pass.

Also fixed on the way: in demo mode the banner took the top bar's grid row, so short pages were pushed halfway down the window (`.app:has(> .demo-banner)`).
