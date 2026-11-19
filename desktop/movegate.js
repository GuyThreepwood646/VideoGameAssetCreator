// Start gate for the big-files move, as the Electron launcher applies it. Same rule as the starter (MoveGate in
// starter/src/Move.cs) and backend/vgac/move_gate.py: while local/move.json exists (a move is unfinished, or its
// record cannot be judged) or local/restart-needed.json exists (a move switched and VGAC has not been restarted),
// the launcher starts nothing: not the backend, not ComfyUI B. Pure function, tested by movegate.test.js.
'use strict';
const fs = require('fs');
const path = require('path');

const MOVE_RECORD = path.join('local', 'move.json');
const RESTART_MARKER = path.join('local', 'restart-needed.json');

// The same plain sentences as the starter and the backend.
const UNFINISHED = 'A move of the big files is not finished. Until it is, nothing else runs: open Maintenance in the VGAC starter, ' +
  'Move the big files, and continue it or abandon it.';
const RESTART = 'VGAC has been moved to a new folder for the big files and has to be restarted first. Close it and start it again ' +
  'from the VGAC starter.';

// null when nothing blocks the start; else the plain reason. Read from the files on each call. An unreadable place is a
// refusal (never a guess).
// statSync, not existsSync: existsSync answers false for every error, so a folder that cannot be read would look clear.
// Only "no such file" counts as clear; any other error refuses.
function present(file, f) {
  try { f.statSync(file); return true; } catch (e) { if (e && e.code === 'ENOENT') return false; throw e; }
}

function blockingReason(projectRoot, fsImpl) {
  const f = fsImpl || fs;
  try {
    if (present(path.join(projectRoot, RESTART_MARKER), f)) return RESTART;
    if (present(path.join(projectRoot, MOVE_RECORD), f)) return UNFINISHED;
    return null;
  } catch (e) {
    return `VGAC cannot tell whether a move of the big files is unfinished (${e && e.message ? e.message : e}), so nothing runs. ` +
      'Fix local\\machine.json and local\\move.json, then start again.';
  }
}

module.exports = { blockingReason, MOVE_RECORD, RESTART_MARKER };
