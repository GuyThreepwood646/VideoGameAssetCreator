// Tests for the launcher's start gate (movegate.js). Run: node desktop/movegate.test.js
'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { blockingReason, MOVE_RECORD, RESTART_MARKER } = require('./movegate');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vgac-movegate-'));
const put = (rel, text) => {
  fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
  fs.writeFileSync(path.join(root, rel), text);
};
const clear = () => {
  for (const rel of [MOVE_RECORD, RESTART_MARKER]) fs.rmSync(path.join(root, rel), { force: true });
};

// nothing to block: the launcher starts
clear();
assert.strictEqual(blockingReason(root), null, 'no record, no marker: start');

// an unfinished move (copying or switching) or a record that cannot be judged: refuse
put(MOVE_RECORD, '{"state":"copying"}');
assert.ok(/not finished/.test(blockingReason(root)), 'unfinished move refuses');
put(MOVE_RECORD, 'not json');
assert.ok(blockingReason(root) !== null, 'unreadable record refuses (never guessed)');

// a switched move that has not been restarted yet: refuse with the restart sentence
clear();
put(RESTART_MARKER, '{}');
assert.ok(/restarted first/.test(blockingReason(root)), 'restart pending refuses');

// the marker wins when both exist
put(MOVE_RECORD, '{}');
assert.ok(/restarted first/.test(blockingReason(root)), 'restart sentence first');

// an unreadable place (the file check throws) refuses and says why
const broken = { statSync() { const e = new Error('access denied'); e.code = 'EACCES'; throw e; } };
const why = blockingReason(root, broken);
assert.ok(why !== null && /cannot tell/.test(why) && /access denied/.test(why), 'a check that throws refuses: ' + why);
// a missing file is clear; a non-ENOENT error refuses even when the other file is absent
const absent = { statSync() { const e = new Error('no'); e.code = 'ENOENT'; throw e; } };
assert.strictEqual(blockingReason(root, absent), null, 'no such file is clear');
const denied = { statSync(p) { if (p.endsWith('move.json')) { const e = new Error('denied'); e.code = 'EPERM'; throw e; } const e = new Error('no'); e.code = 'ENOENT'; throw e; } };
assert.ok(blockingReason(root, denied) !== null, 'a permission error on the move record refuses');

clear();
fs.rmSync(root, { recursive: true, force: true });
console.log('movegate.test.js: all checks passed');
