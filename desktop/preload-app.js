'use strict';
// Preload for the app window (the page the backend serves). It exposes three narrow calls and nothing else: no
// ipcRenderer, no channel names, no Node. The main process checks the sender again (see appBridge in main.js).
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vgacDesktop', {
  // How many things are being worked on; 0 clears the taskbar badge. `icon` is a small PNG data URL drawn by the page.
  setWork: (count, icon) => ipcRenderer.invoke('app:setWork', Number(count) || 0, typeof icon === 'string' ? icon : ''),
  // A system notification (finished, needs you). Shown only while the window is not in front.
  notify: (title, body) => ipcRenderer.invoke('app:notify', String(title || ''), String(body || '')),
  // Start the engine (the backend) again when it is not running.
  startEngine: () => ipcRenderer.invoke('app:startEngine'),
});
