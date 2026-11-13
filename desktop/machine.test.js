// node desktop/machine.test.js : this machine's launcher settings (machine.js).
'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { findProjectRoot, merge, readMachine, applyMachine } = require('./machine');

// merge: tables merge, null removes, arrays and plain values replace.
assert.deepStrictEqual(merge({ a: { x: 1, y: 2 }, b: [1, 2], c: 3 }, { a: { y: 5, z: 6 }, b: [9], c: null }),
  { a: { x: 1, y: 5, z: 6 }, b: [9] });
assert.deepStrictEqual(merge({ a: 1 }, {}), { a: 1 });

// A fake project: <tmp>/proj/backend/vgac/config.py, <tmp>/proj/desktop/config.json, <tmp>/proj/desktop/dist/.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vgac-machine-'));
const proj = path.join(tmp, 'proj');
fs.mkdirSync(path.join(proj, 'backend', 'vgac'), { recursive: true });
fs.writeFileSync(path.join(proj, 'backend', 'vgac', 'config.py'), '');
const desk = path.join(proj, 'desktop');
const dist = path.join(desk, 'dist');
fs.mkdirSync(dist, { recursive: true });

// projectRoot ".." relative to desktop/config.json; the same ".." in a copy next to the portable exe (desktop/dist)
// points at desktop/, which is not the project, so the search upward finds it.
assert.strictEqual(findProjectRoot('..', desk, []), proj);
assert.strictEqual(findProjectRoot('..', dist, [dist]), proj);
assert.strictEqual(findProjectRoot(proj, path.join(tmp, 'elsewhere'), []), proj);
assert.strictEqual(findProjectRoot(undefined, dist, []), proj);

// No machine file: the config as it is, with projectRoot absolute.
assert.deepStrictEqual(readMachine(proj).data, {});
const base = { projectRoot: '..', comfyA: { name: 'ComfyUI A', url: 'http://127.0.0.1:8188' }, comfyB: { name: 'B' } };
let cfg = applyMachine(base, desk, []);
assert.strictEqual(cfg.projectRoot, proj);
assert.strictEqual(cfg.comfyA.desktopExe, undefined);
assert.strictEqual(cfg.machineFile, path.join(proj, 'local', 'machine.json'));

// The machine's "desktop" section is laid over; its "backend" section is not the launcher's business.
fs.mkdirSync(path.join(proj, 'local'));
fs.writeFileSync(path.join(proj, 'local', 'machine.json'), '\uFEFF' + JSON.stringify({
  schema: 1, backend: { blender_exe: 'x' },
  desktop: { comfyA: { name: 'ComfyUI A (3060)', desktopExe: 'C:/Comfy/Comfy.exe' }, comfyB: { startScript: 'runtime/s.ps1' } },
}));
cfg = applyMachine(base, desk, []);
assert.strictEqual(cfg.comfyA.name, 'ComfyUI A (3060)');
assert.strictEqual(cfg.comfyA.url, 'http://127.0.0.1:8188');
assert.strictEqual(cfg.comfyA.desktopExe, 'C:/Comfy/Comfy.exe');
assert.strictEqual(cfg.comfyB.startScript, 'runtime/s.ps1');
assert.strictEqual(cfg.comfyB.name, 'B');
assert.strictEqual(cfg.blender_exe, undefined);
assert.strictEqual(base.comfyA.desktopExe, undefined, 'the input is not changed');

// A broken machine file is a plain error, not a crash with a stack in the user's face.
fs.writeFileSync(path.join(proj, 'local', 'machine.json'), '{ not json');
assert.throws(() => applyMachine(base, desk, []), /machine\.json is not valid JSON/);

fs.rmSync(tmp, { recursive: true, force: true });
console.log('machine.test.js: all passed');
