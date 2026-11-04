// Generates build/icon.png (256x256) and build/icon.ico (PNG-in-ICO) with no dependencies:
// a dark rounded square in the web UI's panel colour with a block "V" in the accent colour.
'use strict';
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const SIZE = 256;
const BG = [0x24, 0x27, 0x2d];      // --panel
const BORDER = [0x4d, 0x52, 0x5c];  // --border-strong
const INK = [0x6e, 0xa4, 0xe8];     // --accent

function crc32(buf) {
  let c, crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) {
    c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

// Distance from point p to segment ab.
function segDist(px, py, ax, ay, bx, by) {
  const vx = bx - ax, vy = by - ay, wx = px - ax, wy = py - ay;
  const t = Math.max(0, Math.min(1, (wx * vx + wy * vy) / (vx * vx + vy * vy)));
  const dx = ax + t * vx - px, dy = ay + t * vy - py;
  return Math.sqrt(dx * dx + dy * dy);
}

// Signed distance to a rounded rectangle centred at the origin.
function roundRectDist(px, py, hw, hh, r) {
  const qx = Math.abs(px) - hw + r, qy = Math.abs(py) - hh + r;
  const ox = Math.max(qx, 0), oy = Math.max(qy, 0);
  return Math.sqrt(ox * ox + oy * oy) + Math.min(Math.max(qx, qy), 0) - r;
}

function coverage(d) { // anti-aliased coverage from a signed distance (inside < 0)
  return Math.max(0, Math.min(1, 0.5 - d));
}

function render() {
  const rows = [];
  const c = SIZE / 2;
  const stroke = 24;
  // The V: two strokes from the top corners to a bottom point.
  const vTopY = 70, vBotY = 196, vHalf = 60;
  for (let y = 0; y < SIZE; y++) {
    const row = Buffer.alloc(1 + SIZE * 4);
    row[0] = 0; // filter: none
    for (let x = 0; x < SIZE; x++) {
      const px = x + 0.5 - c, py = y + 0.5 - c;
      const dRect = roundRectDist(px, py, 112, 112, 40);
      const aRect = coverage(dRect);
      let r = 0, g = 0, b = 0, a = 0;
      if (aRect > 0) {
        // border ring 6 px wide
        const aBorder = coverage(dRect + 6) ; // inner part
        const isBorder = 1 - aBorder;
        r = BG[0] * aBorder + BORDER[0] * isBorder;
        g = BG[1] * aBorder + BORDER[1] * isBorder;
        b = BG[2] * aBorder + BORDER[2] * isBorder;
        a = aRect;
        const dl = segDist(x + 0.5, y + 0.5, c - vHalf, vTopY, c, vBotY) - stroke / 2;
        const dr = segDist(x + 0.5, y + 0.5, c + vHalf, vTopY, c, vBotY) - stroke / 2;
        const aV = coverage(Math.min(dl, dr));
        r = r * (1 - aV) + INK[0] * aV;
        g = g * (1 - aV) + INK[1] * aV;
        b = b * (1 - aV) + INK[2] * aV;
      }
      const o = 1 + x * 4;
      row[o] = Math.round(r); row[o + 1] = Math.round(g); row[o + 2] = Math.round(b); row[o + 3] = Math.round(a * 255);
    }
    rows.push(row);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(SIZE, 0); ihdr.writeUInt32BE(SIZE, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const png = Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(Buffer.concat(rows), { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
  return png;
}

function ico(png) {
  // ICONDIR (6) + ICONDIRENTRY (16) + PNG data. Width/height 0 means 256.
  const header = Buffer.alloc(6 + 16);
  header.writeUInt16LE(0, 0); header.writeUInt16LE(1, 2); header.writeUInt16LE(1, 4);
  header[6] = 0; header[7] = 0; header[8] = 0; header[9] = 0;
  header.writeUInt16LE(1, 10); header.writeUInt16LE(32, 12);
  header.writeUInt32LE(png.length, 14); header.writeUInt32LE(22, 18);
  return Buffer.concat([header, png]);
}

const outDir = path.join(__dirname, 'build');
fs.mkdirSync(outDir, { recursive: true });
const png = render();
fs.writeFileSync(path.join(outDir, 'icon.png'), png);
fs.writeFileSync(path.join(outDir, 'icon.ico'), ico(png));
console.log('wrote build/icon.png and build/icon.ico (' + SIZE + 'x' + SIZE + ')');
