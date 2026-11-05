// Mock of the items feature (backend vgac/items.py): carried-item words on the asset form, linked item assets and
// "Add an item" with attachments and nudges. The demo has no real attachment points in its sample GLB, so the
// preview shows the character without the item; everything else behaves like the backend.
import type { Asset, Attachment, Equipment, ItemDetect, ItemFound, ItemsPanel, ItemStatus, NudgeBody, StageId } from '../api'
import { newVersion, nowIso, setSelected, store, uid } from './data'

type TypeRow = { id: string; label: string; words: string[]; socket: string; creature: string; kind: string; size: number }
const TYPES: TypeRow[] = [
  { id: 'sword', label: 'sword', words: ['short sword', 'longsword', 'sword', 'swords', 'sabre', 'katana', 'rapier'], socket: 'Socket_Hand_R', creature: 'Socket_Mouth', kind: 'blade', size: 0.9 },
  { id: 'dagger', label: 'dagger', words: ['dagger', 'knife'], socket: 'Socket_Hand_R', creature: 'Socket_Mouth', kind: 'blade', size: 0.35 },
  { id: 'axe', label: 'axe', words: ['axe', 'hatchet'], socket: 'Socket_Hand_R', creature: 'Socket_Back', kind: 'haft', size: 0.7 },
  { id: 'mace', label: 'mace', words: ['mace', 'hammer', 'club'], socket: 'Socket_Hand_R', creature: 'Socket_Back', kind: 'haft', size: 0.7 },
  { id: 'bow', label: 'bow', words: ['longbow', 'bow'], socket: 'Socket_Hand_L', creature: 'Socket_Back', kind: 'bow', size: 1.2 },
  { id: 'quiver', label: 'quiver', words: ['quiver of arrows', 'quiver', 'arrows'], socket: 'Socket_Back', creature: 'Socket_Back', kind: 'worn', size: 0.65 },
  { id: 'shield', label: 'shield', words: ['shield', 'buckler'], socket: 'Socket_Hand_L', creature: 'Socket_Back', kind: 'shield', size: 0.6 },
  { id: 'staff', label: 'staff', words: ['staff'], socket: 'Socket_Hand_R', creature: 'Socket_Back', kind: 'pole', size: 1.7 },
  { id: 'spear', label: 'spear', words: ['spear', 'lance'], socket: 'Socket_Hand_R', creature: 'Socket_Back', kind: 'pole', size: 2.0 },
  { id: 'backpack', label: 'backpack', words: ['backpack', 'rucksack'], socket: 'Socket_Back', creature: 'Socket_Back', kind: 'worn', size: 0.5 },
  { id: 'hat', label: 'hat', words: ['wizard hat', 'hat'], socket: 'Socket_Head', creature: 'Socket_Head', kind: 'head', size: 0.35 },
  { id: 'helmet', label: 'helmet', words: ['helmet', 'helm'], socket: 'Socket_Head', creature: 'Socket_Head', kind: 'head', size: 0.32 },
  { id: 'lantern', label: 'lantern', words: ['lantern'], socket: 'Socket_Hand_L', creature: 'Socket_Mouth', kind: 'hang', size: 0.35 },
  { id: 'torch', label: 'torch', words: ['torch'], socket: 'Socket_Hand_L', creature: 'Socket_Mouth', kind: 'haft', size: 0.5 },
]
const CHAR_CATS = ['humanoid', 'quadruped', 'multileg', 'winged', 'serpent']
export const PLACES: Record<string, string> = {
  Socket_Hand_R: 'In the right hand', Socket_Hand_L: 'In the left hand', Socket_Back: 'On the back', Socket_Hip_L: 'At the left hip',
  Socket_Hip_R: 'At the right hip', Socket_Head: 'On the head', Socket_Chest: 'On the chest', Socket_Mouth: 'In the mouth',
}
const HUMANOID_SOCKETS = ['Socket_Hand_R', 'Socket_Hand_L', 'Socket_Back', 'Socket_Hip_L', 'Socket_Hip_R', 'Socket_Head', 'Socket_Chest']
const CREATURE_SOCKETS = ['Socket_Head', 'Socket_Back', 'Socket_Mouth']
const LIMIT = 'The hand does not close around the handle: the characters have no finger bones, so the hand stays a mitten.'

export function mockDetect(prompt: string, category: string): ItemDetect {
  if (!CHAR_CATS.includes(category)) return { checked: false, items: [], worn: [] }
  const items: ItemFound[] = []
  const low = prompt.toLowerCase()
  for (const t of TYPES) {
    const w = t.words.find((x) => new RegExp(`(^|[^\\w-])${x}(?![\\w])`).test(low))
    if (!w || /\b(without|no) (an? )?\w*\s?$/.test(low.slice(0, low.indexOf(w)))) continue
    if (items.some((i) => i.type === t.id)) continue
    const m = low.match(new RegExp(`((?:an?|the|his|her|their)\\s+)?(?:\\w+\\s+){0,2}${w}`))
    items.push({ type: t.id, label: t.label, word: w, phrase: (m?.[0] ?? w).trim(), place: PLACES[t.socket], socket: t.socket })
  }
  const names = items.map((i) => i.label)
  const held = items.find((i) => ['blade', 'haft', 'pole', 'bow', 'shield'].includes(typeOf(i.type).kind))
  const listed = names.length === 1 ? names[0] : names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1]
  return {
    checked: true,
    items,
    worn: ['cloak', 'armour', 'boots', 'belt'].filter((w) => low.includes(w)),
    sentence: items.length
      ? `These work best as their own asset. We will make the character empty-handed, then make the ${listed} ${items.length === 1 ? 'as a separate item and put it on' : 'as separate items and put them on'}.${held ? ` That way they can really hold the ${held.label} when they fight.` : ' That way each one can be moved, swapped or taken off.'}`
      : undefined,
  }
}

function typeOf(id: string | undefined): TypeRow {
  return TYPES.find((t) => t.id === id) ?? TYPES[0]
}

function newAttachment(item: Asset, category: string, socket?: string): Attachment {
  const t = typeOf(item.held_item?.type)
  const s = socket ?? (category === 'humanoid' ? t.socket : t.creature)
  return {
    id: uid('att'),
    item_asset_id: item.id,
    item_name: item.name,
    type: t.id,
    label: t.label,
    socket: s,
    stow_socket: category === 'humanoid' && t.kind !== 'worn' && t.kind !== 'head' ? 'Socket_Hip_L' : null,
    position: [0, 0, 0],
    rotation: [0, 0, 0, 1],
    rotation_deg: [0, 0, 0],
    scale: 1,
  }
}

// A finished item in the demo: every step done, the sample model as its file.
function makeItemAsset(ch: Asset, typeId: string, phrase: string, finished: boolean): Asset {
  const t = typeOf(typeId)
  const item: Asset = {
    id: uid('a'),
    project_id: ch.project_id,
    name: `${ch.name} - ${t.label}`,
    category: 'prop_rigid',
    style_preset: ch.style_preset,
    platform_target: ch.platform_target,
    prompt: `${phrase.replace(/^(an?|the|his|her|their)\s+/i, '').replace(/^./, (c) => c.toUpperCase())}, a game item, about ${t.size} m.`,
    parts: [],
    created_at: nowIso(),
    concept_model: ch.concept_model ?? null,
    held_item: { type: t.id, label: t.label, kind: t.kind, phrase, character_id: ch.id, character_name: ch.name },
    selected: {},
    stages: {},
  }
  if (finished) {
    let parent: string | null = null
    for (const stage of ['concept', 'turnaround', 'mesh', 'prep', 'rig', 'export'] as StageId[]) {
      const v = newVersion(item, stage, parent, {})
      v.status = 'done'
      if (stage === 'concept') v.files = { image: 'samples/knight/front.png' }
      if (stage === 'prep' || stage === 'export') v.files = { glb: 'samples/sample.glb' }
      if (stage === 'rig') v.metrics = { rigid: true }
      if (stage === 'export') v.metrics = { grip: { found: true, confidence: 0.8, sure: true, method: 'the middle of the handle', label: t.label } }
      setSelected(item, v)
      parent = v.id
    }
  }
  store.assets.push(item)
  return item
}

export function mockItemsOnCreate(a: Asset, choice: unknown) {
  const found = mockDetect(a.prompt, a.category)
  if (!found.items.length) return
  const keep = choice === 'keep'
  const eq: Equipment = { choice: keep ? 'keep' : 'separate', found: found.items.map((i) => ({ type: i.type, label: i.label, phrase: i.phrase })), set_aside: [], attachments: [] }
  if (!keep)
    for (const i of found.items) {
      const item = makeItemAsset(a, i.type, i.phrase, false)
      eq.set_aside.push({ type: i.type, label: i.label, phrase: i.phrase, item_asset_id: item.id })
      eq.attachments.push(newAttachment(item, a.category))
    }
  a.equipment = eq
}

function itemStatus(item: Asset): ItemStatus {
  const ex = item.stages.export?.find((v) => v.id === item.selected.export)
  const done = ex?.status === 'done'
  return {
    id: item.id,
    name: item.name,
    status: done ? 'done' : Object.keys(item.stages).length ? 'started' : 'not_started',
    stage: null,
    reason: null,
    held_item: item.held_item ?? null,
    glb: done ? (ex!.files.glb ?? null) : null,
    fbx: null,
    export_version_id: done ? ex!.id : null,
    grip: (ex?.metrics?.grip as ItemStatus['grip']) ?? null,
    thumbnail: null,
    autopilot: null,
  }
}

const applied = new Map<string, string>()
const sig = (eq: Equipment) => JSON.stringify(eq.attachments.map((a) => [a.id, a.socket, a.position, a.rotation, a.scale]))

export function mockPanel(a: Asset): ItemsPanel {
  const eq: Equipment = a.equipment ?? { choice: null, set_aside: [], attachments: [] }
  const ex = a.stages.export?.find((v) => v.id === a.selected.export && v.status === 'done')
  const canAdd = CHAR_CATS.includes(a.category) && !!ex
  const sockets = a.category === 'humanoid' ? HUMANOID_SOCKETS : CREATURE_SOCKETS
  const items: Record<string, ItemStatus> = {}
  for (const at of eq.attachments) {
    const it = store.assets.find((x) => x.id === at.item_asset_id)
    if (it) items[it.id] = itemStatus(it)
  }
  const attached = eq.attachments.some((at) => items[at.item_asset_id]?.status === 'done')
  return {
    asset_id: a.id,
    can_add: canAdd,
    reason: !CHAR_CATS.includes(a.category) ? 'Items go on characters and creatures with a skeleton.' : canAdd ? null : 'Finish the character first: items go on the attachment points made in the last step.',
    category: a.category,
    places: sockets.map((s) => ({ socket: s, label: PLACES[s] })),
    equipment: eq,
    items,
    character_glb: ex ? (ex.files.glb ?? null) : null,
    saved: attached && applied.get(a.id) === sig(eq),
    needs_update: attached && applied.get(a.id) !== sig(eq),
    build: eq.build ?? null,
    others: store.assets.filter((x) => x.project_id === a.project_id && x.held_item && !items[x.id]).map(itemStatus),
    types: TYPES.map((t) => ({ id: t.id, label: t.label, place: PLACES[a.category === 'humanoid' ? t.socket : t.creature] })),
    limit: LIMIT,
    step_cm: 1,
    step_deg: 5,
  }
}

export function mockAddItem(a: Asset, body: Record<string, unknown>): { attachment: Attachment; item: Asset; panel: ItemsPanel } {
  const eq: Equipment = a.equipment ?? { choice: null, set_aside: [], attachments: [] }
  let item: Asset | undefined
  if (typeof body.item_asset_id === 'string') {
    item = store.assets.find((x) => x.id === body.item_asset_id)
    if (!item?.held_item) throw new Error('that asset is not an item')
  } else {
    const desc = String(body.description ?? '').trim()
    const typeId = typeof body.type === 'string' && body.type ? body.type : mockDetect(desc, 'humanoid').items[0]?.type
    if (!typeId) throw new Error('say what the item is (for example: a short sword with a leather grip)')
    item = makeItemAsset(a, typeId, desc || typeOf(typeId).label, true)
  }
  const att = newAttachment(item, a.category, typeof body.socket === 'string' ? body.socket : undefined)
  eq.attachments = [...eq.attachments, att]
  a.equipment = eq
  return { attachment: att, item, panel: mockPanel(a) }
}

function find(a: Asset, id: string): Attachment {
  const at = a.equipment?.attachments.find((x) => x.id === id)
  if (!at) throw new Error('attachment not found')
  return at
}

export function mockUpdate(a: Asset, id: string, body: Record<string, unknown>) {
  const at = find(a, id)
  if (typeof body.socket === 'string' && body.socket !== at.socket) {
    at.socket = body.socket
    at.position = [0, 0, 0]
    at.rotation = [0, 0, 0, 1]
    at.rotation_deg = [0, 0, 0]
    at.scale = 1
  }
  if (typeof body.stow_socket === 'string') at.stow_socket = body.stow_socket || null
  if (Array.isArray(body.position)) at.position = body.position.map(Number) as Attachment['position']
  if (Array.isArray(body.rotation_deg)) at.rotation_deg = body.rotation_deg.map(Number) as Attachment['rotation_deg']
  if (typeof body.scale === 'number') at.scale = body.scale
  return { attachment: { ...at }, panel: mockPanel(a) }
}

// The demo moves in the attachment point's own axes (the real backend uses the character's axes).
export function mockNudge(a: Asset, id: string, body: NudgeBody) {
  const at = find(a, id)
  const step = body.big ? 0.05 : 0.01
  const deg = body.big ? 15 : 5
  if (body.reset) {
    at.position = [0, 0, 0]
    at.rotation = [0, 0, 0, 1]
    at.rotation_deg = [0, 0, 0]
    at.scale = 1
  }
  if (body.move) at.position = at.position.map((v, i) => Math.round((v + body.move![i] * step) * 1e5) / 1e5) as Attachment['position']
  if (body.turn) at.rotation_deg = at.rotation_deg.map((v, i) => v + body.turn![i] * deg) as Attachment['rotation_deg']
  if (body.slide) at.position = [at.position[0], at.position[1], Math.round((at.position[2] - body.slide * step) * 1e5) / 1e5]
  if (body.scale_steps) at.scale = Math.round(Math.min(5, Math.max(0.2, at.scale * 1.05 ** body.scale_steps)) * 1e4) / 1e4
  return { attachment: { ...at } }
}

export function mockRemove(a: Asset, id: string) {
  const at = find(a, id)
  a.equipment!.attachments = a.equipment!.attachments.filter((x) => x.id !== id)
  return { removed: at, panel: mockPanel(a) }
}

export function mockApplied(a: Asset) {
  if (a.equipment) applied.set(a.id, sig(a.equipment))
}
