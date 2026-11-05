// Mock "Add a move": the hand-made move library (a representative subset of the two packs), a character's moves
// from its rig version's added_moves and removed_moves, and your own animation files. Shapes match backend moves.py.
import type { CharacterMove, CharacterMoves, MoveClip, MoveLibrary, MyMoveFile } from '../api'

export const MOVE_GROUPS: { id: string; label: string }[] = [
  { id: 'standing', label: 'Standing' },
  { id: 'walking', label: 'Walking and running' },
  { id: 'jumping', label: 'Jumping and rolling' },
  { id: 'fighting', label: 'Fighting' },
  { id: 'sword', label: 'Sword' },
  { id: 'shield', label: 'Shield' },
  { id: 'pistol', label: 'Pistol' },
  { id: 'magic', label: 'Magic' },
  { id: 'hit', label: 'Getting hit and falling' },
  { id: 'using', label: 'Using things' },
  { id: 'work', label: 'Work and farming' },
  { id: 'sitting', label: 'Sitting and resting' },
  { id: 'talking', label: 'Talking and gestures' },
  { id: 'swimming', label: 'Swimming' },
  { id: 'climbing', label: 'Climbing and sliding' },
  { id: 'zombie', label: 'Zombie' },
  { id: 'mine', label: 'Yours' },
]

const FRAMES = 8
const CELL = 96

// [pack, action, label, group, loop, seconds, name, moves_character?]
type Row = [string, string, string, string, boolean, number, string, boolean?]
const ROWS: Row[] = [
  ['ual1', 'Idle_Loop', 'Stand still', 'standing', true, 2.5, 'idle'],
  ['ual1', 'Crouch_Idle_Loop', 'Crouch', 'standing', true, 2.93, 'crouch_idle'],
  ['ual1', 'Idle_Torch_Loop', 'Stand holding a torch', 'standing', true, 1.27, 'idle_torch'],
  ['ual2', 'Idle_FoldArms_Loop', 'Stand with arms folded', 'standing', true, 2.5, 'idle_fold_arms'],
  ['ual1', 'Walk_Loop', 'Walk', 'walking', true, 1.33, 'walk'],
  ['ual1', 'Jog_Fwd_Loop', 'Jog', 'walking', true, 0.93, 'run'],
  ['ual1', 'Sprint_Loop', 'Sprint', 'walking', true, 0.67, 'sprint'],
  ['ual1', 'Crouch_Fwd_Loop', 'Crouch walk', 'walking', true, 2, 'crouch_walk'],
  ['ual2', 'Walk_Carry_Loop', 'Walk carrying something', 'walking', true, 2, 'walk_carry'],
  ['ual1', 'Jump_Start', 'Jump (take off)', 'jumping', false, 1.33, 'jump_start'],
  ['ual1', 'Jump_Loop', 'Jump (in the air)', 'jumping', true, 2.5, 'jump_loop'],
  ['ual1', 'Jump_Land', 'Jump (land)', 'jumping', false, 1.27, 'jump_land'],
  ['ual1', 'Roll', 'Roll', 'jumping', false, 1.47, 'roll'],
  ['ual1', 'Punch_Jab', 'Punch (jab)', 'fighting', false, 0.87, 'punch'],
  ['ual2', 'Melee_Hook', 'Hook punch', 'fighting', false, 0.47, 'melee_hook'],
  ['ual1', 'Sword_Attack', 'Sword attack', 'sword', false, 1.53, 'attack'],
  ['ual2', 'Sword_Block', 'Block with a sword', 'sword', true, 1.23, 'sword_block'],
  ['ual2', 'Sword_Regular_Combo', 'Sword combo (three slashes)', 'sword', false, 3, 'sword_regular_combo'],
  ['ual2', 'Idle_Shield_Loop', 'Stand behind a shield', 'shield', true, 2.5, 'idle_shield'],
  ['ual2', 'Shield_OneShot', 'Shield bash', 'shield', false, 0.83, 'shield_one_shot'],
  ['ual1', 'Pistol_Shoot', 'Shoot a pistol', 'pistol', false, 0.63, 'pistol_shoot'],
  ['ual1', 'Spell_Simple_Shoot', 'Cast a spell', 'magic', false, 0.5, 'spell_simple_shoot'],
  ['ual1', 'Spell_Simple_Idle_Loop', 'Hold a spell ready', 'magic', true, 2.1, 'spell_simple_idle'],
  ['ual1', 'Hit_Chest', 'Get hit in the chest', 'hit', false, 0.33, 'hit'],
  ['ual1', 'Death01', 'Die', 'hit', false, 2.4, 'death'],
  ['ual2', 'Hit_Knockback', 'Get knocked back', 'hit', false, 0.83, 'hit_knockback'],
  ['ual1', 'Interact', 'Use something', 'using', false, 2, 'interact'],
  ['ual2', 'Chest_Open', 'Open a chest', 'using', false, 1.37, 'chest_open'],
  ['ual2', 'TreeChopping_Loop', 'Chop wood', 'work', true, 0.97, 'tree_chopping'],
  ['ual1', 'Sitting_Idle_Loop', 'Sit', 'sitting', true, 1.67, 'sitting_idle'],
  ['ual1', 'Sitting_Enter', 'Sit down', 'sitting', false, 1.3, 'sitting_enter'],
  ['ual1', 'Dance_Loop', 'Dance', 'talking', true, 1, 'dance'],
  ['ual2', 'Yes', 'Nod (yes)', 'talking', false, 2.5, 'yes'],
  ['ual1', 'Swim_Fwd_Loop', 'Swim', 'swimming', true, 1.33, 'swim_fwd'],
  ['ual1', 'Swim_Idle_Loop', 'Tread water', 'swimming', true, 3.33, 'swim_idle'],
  ['ual2', 'ClimbUp_1m_RM', 'Climb up a ledge (1 m)', 'climbing', false, 0.67, 'climb_up_1m', true],
  ['ual2', 'Slide_Loop', 'Slide (sliding)', 'climbing', true, 2, 'slide'],
  ['ual2', 'Zombie_Walk_Fwd_Loop', 'Zombie walk', 'zombie', true, 1.33, 'zombie_walk_fwd'],
]

// The default set a new human-shaped character gets (humanoid.yaml library_clips), plus the two built-in turns.
const DEFAULTS: { name: string; clip: string; label: string; loop: boolean }[] = [
  ['idle', 'ual1:Idle_Loop', 'Stand still', true],
  ['walk', 'ual1:Walk_Loop', 'Walk', true],
  ['run', 'ual1:Jog_Fwd_Loop', 'Run', true],
  ['sprint', 'ual1:Sprint_Loop', 'Sprint', true],
  ['attack', 'ual1:Sword_Attack', 'Sword attack', false],
  ['punch', 'ual1:Punch_Jab', 'Punch', false],
  ['hit', 'ual1:Hit_Chest', 'Get hit', false],
  ['death', 'ual1:Death01', 'Die', false],
  ['jump_start', 'ual1:Jump_Start', 'Jump (take off)', false],
  ['jump_loop', 'ual1:Jump_Loop', 'Jump (in the air)', true],
  ['jump_land', 'ual1:Jump_Land', 'Jump (land)', false],
  ['crouch_idle', 'ual1:Crouch_Idle_Loop', 'Crouch', true],
  ['crouch_walk', 'ual1:Crouch_Fwd_Loop', 'Crouch walk', true],
  ['roll', 'ual1:Roll', 'Roll', false],
  ['interact', 'ual1:Interact', 'Use or pick up', false],
].map(([name, clip, label, loop]) => ({ name: String(name), clip: String(clip), label: String(label), loop: Boolean(loop) }))
const TURNS = [
  { name: 'turn_left', label: 'Turn left' },
  { name: 'turn_right', label: 'Turn right' },
]

const stripCache = new Map<string, string>()

// A strip of FRAMES stick-figure cells; each group swings its limbs its own way, so the previews differ.
function moveStrip(key: string, group: string, hue: number): string {
  const hit = stripCache.get(key)
  if (hit) return hit
  const c = document.createElement('canvas')
  c.width = CELL * FRAMES
  c.height = CELL
  const ctx = c.getContext('2d')
  if (!ctx) return ''
  for (let i = 0; i < FRAMES; i++) {
    const t = (i / FRAMES) * Math.PI * 2
    const x0 = i * CELL
    ctx.fillStyle = 'hsl(220 10% 20%)'
    ctx.fillRect(x0, 0, CELL, CELL)
    ctx.fillStyle = 'hsl(220 8% 28%)'
    ctx.fillRect(x0, CELL - 12, CELL, 12)
    const low = group === 'sitting' || group === 'swimming' ? 22 : group === 'jumping' ? -10 * Math.max(0, Math.sin(t)) : 0
    const hip = { x: x0 + CELL / 2, y: CELL - 44 + low }
    const neck = { x: hip.x, y: hip.y - 26 }
    const swing = group === 'standing' || group === 'sitting' ? 0.1 : group === 'walking' || group === 'zombie' ? 0.6 : 0.4
    const armUp = group === 'magic' || group === 'sword' || group === 'fighting' || group === 'shield' || group === 'pistol' ? 1.4 + Math.sin(t) * 0.6 : 0.2
    ctx.strokeStyle = `hsl(${hue} 45% 62%)`
    ctx.lineWidth = 5
    ctx.lineCap = 'round'
    const limb = (from: { x: number; y: number }, ang: number, len: number) => {
      ctx.beginPath()
      ctx.moveTo(from.x, from.y)
      ctx.lineTo(from.x + Math.sin(ang) * len, from.y + Math.cos(ang) * len)
      ctx.stroke()
    }
    limb(hip, group === 'sitting' ? 1.4 : Math.sin(t) * swing, 30)
    limb(hip, group === 'sitting' ? 1.3 : -Math.sin(t) * swing, 30)
    ctx.beginPath()
    ctx.moveTo(hip.x, hip.y)
    ctx.lineTo(neck.x, neck.y)
    ctx.stroke()
    limb(neck, Math.PI - armUp - Math.sin(t) * swing * 0.5, 24)
    limb(neck, -(Math.PI - armUp) + Math.sin(t) * swing * 0.5, 24)
    ctx.fillStyle = `hsl(${hue} 45% 70%)`
    ctx.beginPath()
    ctx.arc(neck.x, neck.y - 9, 8, 0, Math.PI * 2)
    ctx.fill()
  }
  const url = c.toDataURL('image/png')
  stripCache.set(key, url)
  return url
}

const GROUP_HUE: Record<string, number> = { standing: 210, walking: 140, jumping: 50, fighting: 10, sword: 0, shield: 30, pistol: 260, magic: 280, hit: 350, using: 180, work: 100, sitting: 200, talking: 320, swimming: 190, climbing: 70, zombie: 90, mine: 160 }

// Your own animation files, kept for the session.
export const myMoveFiles: MyMoveFile[] = []

function libraryClips(): MoveClip[] {
  const base: MoveClip[] = ROWS.map(([pack, action, label, group, loop, seconds, name, moving]) => ({
    id: `${pack}:${action}`,
    pack,
    action,
    label,
    group,
    loop,
    seconds,
    name,
    moves_character: !!moving,
    preview: moveStrip(`${pack}:${action}`, group, GROUP_HUE[group] ?? 200),
    frames: FRAMES,
    still: 3,
  }))
  const mine: MoveClip[] = myMoveFiles.flatMap((f) =>
    f.clips.map((c) => ({ id: c.id, pack: 'mine', action: c.action, label: c.label, group: 'mine', loop: c.loop, seconds: c.seconds, name: slug(c.label), preview: c.preview, frames: c.frames, still: c.still })),
  )
  return [...base, ...mine]
}

export function moveLibrary(): MoveLibrary {
  const clips = libraryClips()
  return {
    packs: [
      { id: 'ual1', label: 'Hand-made moves, pack 1', licence: 'Free to use in any game, also commercial (CC0, Quaternius).', count: 43 },
      { id: 'ual2', label: 'Hand-made moves, pack 2', licence: 'Free to use in any game, also commercial (CC0, Quaternius).', count: 41 },
      { id: 'mine', label: 'Yours', licence: 'Your own files: you are responsible for being allowed to use them in your game.', count: clips.filter((c) => c.pack === 'mine').length },
    ],
    groups: MOVE_GROUPS,
    clips,
    previews: { ready: clips.length, total: clips.length, making: false },
  }
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'move'
}

export type AddedMove = { clip: string; name: string; loop: boolean }
export const addedOf = (params: Record<string, unknown>): AddedMove[] =>
  Array.isArray(params.added_moves) ? (params.added_moves as AddedMove[]).filter((a) => a && typeof a.clip === 'string') : []
export const removedOf = (params: Record<string, unknown>): string[] => (Array.isArray(params.removed_moves) ? (params.removed_moves as unknown[]).map(String) : [])

// The clip names a rig version ends up with (what metrics.clips lists).
export function clipNamesFor(params: Record<string, unknown>): string[] {
  const removed = new Set(removedOf(params))
  return [...DEFAULTS.map((d) => d.name).filter((n) => !removed.has(n)), ...TURNS.map((t) => t.name).filter((n) => !removed.has(n)), ...addedOf(params).map((a) => a.name)]
}

// New added_moves and removed_moves after adding clip ids and removing move names. Throws a plain sentence.
export function changeMoves(params: Record<string, unknown>, add: string[], remove: string[]): { added_moves: AddedMove[]; removed_moves: string[] } {
  const byId = new Map(libraryClips().map((c) => [c.id, c]))
  let added = addedOf(params)
  const removed = new Set(removedOf(params))
  const have = new Set(clipNamesFor(params))
  for (const id of add) {
    const c = byId.get(id)
    if (!c) throw new Error(`That move is not in the library (${id}).`)
    const def = DEFAULTS.find((d) => d.clip === id)
    if (def) {
      removed.delete(def.name)
      continue
    }
    if (added.some((a) => a.clip === id)) continue
    let name = c.name
    for (let i = 2; have.has(name); i++) name = `${c.name}_${i}`
    have.add(name)
    added.push({ clip: id, name, loop: c.loop })
  }
  for (const n of remove) {
    if (added.some((a) => a.name === n)) added = added.filter((a) => a.name !== n)
    else if (DEFAULTS.some((d) => d.name === n) || TURNS.some((t) => t.name === n)) removed.add(n)
  }
  return { added_moves: added, removed_moves: [...removed] }
}

export function characterMoves(category: string, rigVersion: { id: string; params: Record<string, unknown> } | null, busy: boolean, others: CharacterMoves['others']): CharacterMoves {
  const human = category === 'humanoid'
  if (!human) {
    return {
      can_add: false,
      reason: 'The hand-made moves only fit human-shaped characters, so they cannot be added to this creature.',
      instead: ['stand still', 'walk', 'run', 'turn', 'attack', 'get hit', 'die'],
      rig_version: rigVersion?.id ?? null,
      busy,
      moves: [
        ['idle', 'Stand still', true],
        ['walk', 'Walk', true],
        ['run', 'Run', true],
        ['turn_left', 'Turn left', false],
        ['turn_right', 'Turn right', false],
        ['attack', 'Attack', false],
        ['hit', 'Get hit', false],
        ['death', 'Die', false],
      ].map(([name, label, loop]) => ({ name: String(name), label: String(label), clip: null, source: 'built-in' as const, loop: Boolean(loop), added: false, default: true, made: true })),
      added: [],
      removed: [],
      others: [],
    }
  }
  const params = rigVersion?.params ?? {}
  const removed = new Set(removedOf(params))
  const byId = new Map(libraryClips().map((c) => [c.id, c]))
  const moves: CharacterMove[] = [
    ...DEFAULTS.filter((d) => !removed.has(d.name)).map((d) => ({ name: d.name, label: d.label, clip: d.clip, source: 'library' as const, loop: d.loop, added: false, default: true, made: true })),
    ...TURNS.filter((t) => !removed.has(t.name)).map((t) => ({ name: t.name, label: t.label, clip: null, source: 'built-in' as const, loop: false, added: false, default: true, made: true })),
    ...addedOf(params).map((a) => {
      const c = byId.get(a.clip)
      return { name: a.name, label: c?.label ?? a.name, clip: a.clip, source: (a.clip.startsWith('mine_') ? 'mine' : 'library') as CharacterMove['source'], loop: a.loop, added: true, default: false, made: !!c }
    }),
  ]
  return { can_add: true, reason: '', instead: [], rig_version: rigVersion?.id ?? null, busy, moves, added: addedOf(params), removed: [...removed], others }
}

// A mock upload: a file named with "creature" or "robot" is refused; others give two clips and one bone not found.
export function mockReadMoveFile(name: string): MyMoveFile {
  const lower = name.toLowerCase()
  const ext = lower.split('.').pop() ?? ''
  if (!['fbx', 'glb', 'bvh'].includes(ext)) throw new Error('Choose an FBX, GLB or BVH file. Other kinds of file cannot hold moves the app can read.')
  if (/creature|robot|dragon/.test(lower)) throw new Error("This file's skeleton is not human-shaped, so we cannot use it.")
  if (/empty|still/.test(lower)) throw new Error('This file has no moves in it.')
  let id = 'mine_' + slug(name.replace(/\.[^.]+$/, ''))
  for (let i = 2; myMoveFiles.some((f) => f.id === id); i++) id = id.replace(/_\d+$/, '') + '_' + i
  const actions = ext === 'bvh' ? ['Take 001'] : ['mixamo.com', 'Wave_Hello']
  const file: MyMoveFile = {
    id,
    label: name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' '),
    file_name: name.replace(/[^A-Za-z0-9._ -]+/g, '_'),
    kind: ext as MyMoveFile['kind'],
    source: '',
    recognised: 21,
    total: 22,
    sentence: 'We recognised 21 of the 22 main bones.',
    missing: ['left toes'],
    clips: actions.map((a, i) => ({
      id: `${id}:${a}`,
      action: a,
      label: i === 0 && a === 'mixamo.com' ? 'My move' : a.replace(/_/g, ' '),
      loop: false,
      seconds: 1.6 + i,
      preview: moveStrip(`${id}:${a}`, 'mine', GROUP_HUE.mine),
      frames: FRAMES,
      still: 3,
    })),
    added_at: new Date().toISOString(),
  }
  myMoveFiles.push(file)
  return file
}
