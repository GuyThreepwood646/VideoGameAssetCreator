'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vgac', {
  onState: (cb) => { ipcRenderer.on('state', (_e, snap) => cb(snap)); },
  requestState: () => ipcRenderer.invoke('state'),
  action: (id, arg) => ipcRenderer.invoke('action', id, arg),
});
