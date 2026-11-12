'use strict';
// Pure checks for the app window's bridge (UI-31, UI-32), kept apart from Electron so they can be tested with node.

const MAX_ICON_BYTES = 16 * 1024;
const PNG_PREFIX = 'data:image/png;base64,';

// The page may only talk to the bridge from the backend's own origin.
function senderAllowed(frameUrl, backendUrl) {
  try { return new URL(frameUrl).origin === new URL(backendUrl).origin; } catch { return false; }
}

// 0..99, whole numbers; anything else is 0 (clears the badge).
function cleanCount(n) {
  const v = Math.floor(Number(n));
  return Number.isFinite(v) && v > 0 ? Math.min(v, 99) : 0;
}

// A small PNG drawn by the page, or null. Never a path, a URL or anything larger than a badge.
function cleanIcon(dataUrl) {
  if (typeof dataUrl !== 'string' || !dataUrl.startsWith(PNG_PREFIX)) return null;
  const b64 = dataUrl.slice(PNG_PREFIX.length);
  if (!/^[A-Za-z0-9+/=]+$/.test(b64) || b64.length > Math.ceil(MAX_ICON_BYTES * 4 / 3)) return null;
  return Buffer.from(b64, 'base64');
}

function cleanText(s, max) {
  return String(s || '').replace(/[\u0000-\u001f]/g, ' ').slice(0, max);
}

module.exports = { senderAllowed, cleanCount, cleanIcon, cleanText, MAX_ICON_BYTES };
