'use strict';
// node desktop/appbridge.test.js   (no packages needed)
const assert = require('assert');
const { senderAllowed, cleanCount, cleanIcon, cleanText } = require('./appbridge');

assert(senderAllowed('http://127.0.0.1:8765/assets/1', 'http://127.0.0.1:8765'));
assert(!senderAllowed('http://127.0.0.1:9999/', 'http://127.0.0.1:8765'));
assert(!senderAllowed('https://evil.example/', 'http://127.0.0.1:8765'));
assert(!senderAllowed('not a url', 'http://127.0.0.1:8765'));
assert(!senderAllowed('', 'http://127.0.0.1:8765'));

assert.strictEqual(cleanCount(3), 3);
assert.strictEqual(cleanCount('4'), 4);
assert.strictEqual(cleanCount(-2), 0);
assert.strictEqual(cleanCount(NaN), 0);
assert.strictEqual(cleanCount(5000), 99);

const png = 'data:image/png;base64,' + Buffer.from('x'.repeat(100)).toString('base64');
assert(Buffer.isBuffer(cleanIcon(png)));
assert.strictEqual(cleanIcon('data:image/svg+xml;base64,AAAA'), null);
assert.strictEqual(cleanIcon('file:///C:/Windows/x.png'), null);
assert.strictEqual(cleanIcon('data:image/png;base64,' + 'A'.repeat(40000)), null);
assert.strictEqual(cleanIcon('data:image/png;base64,<script>'), null);
assert.strictEqual(cleanIcon(42), null);

assert.strictEqual(cleanText('a\nb\u0000c', 10), 'a b c');
assert.strictEqual(cleanText('x'.repeat(500), 120).length, 120);
console.log('appbridge: all checks passed');
