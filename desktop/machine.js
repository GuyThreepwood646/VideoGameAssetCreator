// This machine's launcher settings: the project folder, and local/machine.json's "desktop" section laid over
// desktop/config.json (same shape; tables merge key by key, a null removes a key). The backend reads the same file's
// "backend" section (backend/vgac/config.py). Pure functions, tested by machine.test.js.
'use strict';
const fs = require('fs');
const path = require('path');

function isProjectRoot(dir) {
  return fs.existsSync(path.join(dir, 'backend', 'vgac', 'config.py'));
}

// The project folder: projectRoot as configured (absolute, or relative to the config file), else the first folder
// upward from the config file, then from each extra start folder (the portable exe's folder), that holds the backend.
function findProjectRoot(configured, configDir, extraStarts) {
  if (configured) {
    const p = path.isAbsolute(configured) ? configured : path.resolve(configDir, configured);
    if (isProjectRoot(p)) return p;
  }
  for (const start of [configDir].concat(extraStarts || [])) {
    if (!start) continue;
    let dir = path.resolve(start);
    for (;;) {
      if (isProjectRoot(dir)) return dir;
      const up = path.dirname(dir);
      if (up === dir) break;
      dir = up;
    }
  }
  return configured ? (path.isAbsolute(configured) ? configured : path.resolve(configDir, configured)) : path.resolve(configDir);
}

function merge(base, over) {
  if (!base || typeof base !== 'object' || Array.isArray(base) || !over || typeof over !== 'object' || Array.isArray(over)) {
    return over;
  }
  const out = Object.assign({}, base);
  for (const [k, v] of Object.entries(over)) {
    if (v === null) delete out[k];
    else if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) out[k] = merge(out[k], v);
    else out[k] = v;
  }
  return out;
}

// local/machine.json under the project folder: { file, data } (data {} when there is none). Throws with a plain
// message when the file is there but is not valid JSON.
function readMachine(projectRoot) {
  const file = path.join(projectRoot, 'local', 'machine.json');
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (_) {
    return { file, data: {} };
  }
  try {
    const data = JSON.parse(text.replace(/^﻿/, '') || '{}');
    return { file, data: data && typeof data === 'object' && !Array.isArray(data) ? data : {} };
  } catch (e) {
    throw new Error(`${file} is not valid JSON (${e.message}). Fix it, or restore machine.json.bak beside it.`);
  }
}

// desktop/config.json's contents with this machine's "desktop" section laid over them. Returns the merged config
// with projectRoot made absolute and machineFile set.
function applyMachine(cfg, configDir, extraStarts) {
  const root = findProjectRoot(cfg.projectRoot, configDir, extraStarts);
  const { file, data } = readMachine(root);
  const desk = data.desktop && typeof data.desktop === 'object' ? data.desktop : {};
  const out = merge(cfg, desk);
  out.projectRoot = root;
  out.machineFile = file;
  return out;
}

module.exports = { findProjectRoot, isProjectRoot, merge, readMachine, applyMachine };
