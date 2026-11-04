// VGAC desktop launcher (Electron main process).
// Shows a status window, brings up ComfyUI B, the backend and the UI build, then opens the app window.
'use strict';
const { app, BrowserWindow, Tray, Menu, nativeImage, shell, dialog, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { Supervisor } = require('./services');

// ---- config ---------------------------------------------------------------------------------------------------

function configPath() {
  if (process.env.VGAC_DESKTOP_CONFIG) return path.resolve(process.env.VGAC_DESKTOP_CONFIG);
  if (!app.isPackaged) return path.join(__dirname, 'config.json');
  const exeDir = process.env.PORTABLE_EXECUTABLE_DIR || path.dirname(process.execPath);
  const p = path.join(exeDir, 'config.json');
  if (!fs.existsSync(p)) {
    // read + write rather than copyFileSync: the source lives inside the asar archive.
    try { fs.writeFileSync(p, fs.readFileSync(path.join(__dirname, 'config.json'))); } catch (_) { return path.join(__dirname, 'config.json'); }
  }
  return p;
}

function loadConfig() {
  const file = configPath();
  const cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
  cfg.file = file;
  const cfgDir = path.dirname(file);
  cfg.projectRoot = path.isAbsolute(cfg.projectRoot) ? cfg.projectRoot : path.resolve(cfgDir, cfg.projectRoot);
  cfg.resolve = (p) => (path.isAbsolute(p) ? p : path.resolve(cfg.projectRoot, p));
  return cfg;
}

let cfg;
let logStream = null;
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  if (logStream) logStream.write(line + '\n');
}

// ---- state ----------------------------------------------------------------------------------------------------

let supervisor;
let tray = null;
let statusWindow = null;
let mainWindow = null;
let quitting = false;
let mainOpened = false;

const ICON_PATH = path.join(__dirname, 'build', 'icon.png');
function appIcon() {
  return fs.existsSync(ICON_PATH) ? nativeImage.createFromPath(ICON_PATH) : nativeImage.createEmpty();
}

// ---- windows --------------------------------------------------------------------------------------------------

function createStatusWindow() {
  if (statusWindow) return statusWindow;
  statusWindow = new BrowserWindow({
    width: 620,
    height: 460,
    minWidth: 480,
    minHeight: 360,
    title: 'VGAC status',
    backgroundColor: '#1b1d21',
    icon: appIcon(),
    autoHideMenuBar: true,
    show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  statusWindow.setMenu(null);
  statusWindow.loadFile(path.join(__dirname, 'status.html'));
  statusWindow.once('ready-to-show', () => statusWindow.show());
  statusWindow.webContents.on('did-finish-load', () => sendState());
  statusWindow.on('close', (e) => {
    if (quitting) return;
    // Closing the status window before the app is open means quitting; afterwards it just hides.
    if (!mainWindow) { requestQuit(); e.preventDefault(); return; }
    e.preventDefault();
    statusWindow.hide();
  });
  statusWindow.on('closed', () => { statusWindow = null; });
  return statusWindow;
}

function showStatusWindow() {
  const w = createStatusWindow();
  if (w.isMinimized()) w.restore();
  w.show();
  w.focus();
}

function createMainWindow() {
  if (mainWindow) { mainWindow.show(); mainWindow.focus(); return mainWindow; }
  const w = cfg.window || {};
  mainWindow = new BrowserWindow({
    width: w.width || 1440,
    height: w.height || 900,
    minWidth: w.minWidth || 1200,
    minHeight: w.minHeight || 800,
    title: 'Video Game Asset Creator',
    backgroundColor: '#1b1d21',
    icon: appIcon(),
    show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  mainWindow.loadURL(cfg.backend.url + '/');
  mainWindow.once('ready-to-show', () => { mainWindow.show(); mainWindow.focus(); });
  mainWindow.on('page-title-updated', (e) => e.preventDefault());
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
    if (isMainFrame) log(`main window failed to load ${url}: ${code} ${desc}`);
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.on('close', (e) => {
    if (quitting) return;
    e.preventDefault();
    requestQuit();
  });
  mainWindow.on('closed', () => { mainWindow = null; });
  mainOpened = true;
  log('main window opened at ' + cfg.backend.url + '/');
  return mainWindow;
}

function openMainWindow() {
  createMainWindow();
  if (statusWindow && statusWindow.isVisible() && !process.env.VGAC_DESKTOP_KEEP_STATUS) statusWindow.hide();
}

// ---- menus and tray -------------------------------------------------------------------------------------------

function servicesMenuTemplate() {
  return [
    { label: 'Restart backend', click: () => restartService('backend') },
    { label: 'Restart ComfyUI B', click: () => restartService('comfyB') },
    { type: 'separator' },
    { label: 'Open logs folder', click: () => openFolder(cfg.resolve(cfg.logsFolder)) },
    { label: 'Open exports folder', click: () => openFolder(cfg.resolve(cfg.exportsFolder)) },
    { type: 'separator' },
    { label: 'Open ComfyUI Desktop', click: () => openComfyDesktop(), enabled: !!(cfg.comfyA.desktopExe && fs.existsSync(cfg.comfyA.desktopExe)) },
    { label: 'Edit launcher config', click: () => shell.openPath(cfg.file) },
  ];
}

function buildAppMenu() {
  const template = [
    {
      label: 'VGAC',
      submenu: [
        { label: 'Status window', accelerator: 'CmdOrCtrl+Shift+S', click: () => showStatusWindow() },
        { label: 'Open app window', click: () => createMainWindow(), enabled: true },
        { type: 'separator' },
        { label: 'Quit', accelerator: 'CmdOrCtrl+Q', click: () => requestQuit() },
      ],
    },
    { label: 'Services', submenu: servicesMenuTemplate() },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' }, { role: 'forceReload' }, { role: 'toggleDevTools' }, { type: 'separator' },
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function trayTooltip(snap) {
  if (!snap) return 'Video Game Asset Creator';
  const up = snap.services.filter((s) => s.state === 'up').length;
  return `Video Game Asset Creator: ${up}/${snap.services.length} services up`;
}

function createTray() {
  const img = appIcon().resize({ width: 16, height: 16 });
  tray = new Tray(img);
  tray.setToolTip('Video Game Asset Creator');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Video Game Asset Creator', click: () => createMainWindow() },
    { label: 'Status window', click: () => showStatusWindow() },
    { label: 'Services', submenu: servicesMenuTemplate() },
    { type: 'separator' },
    { label: 'Quit', click: () => requestQuit() },
  ]));
  tray.on('click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } else showStatusWindow(); });
  tray.on('double-click', () => createMainWindow());
}

// ---- actions --------------------------------------------------------------------------------------------------

function openFolder(p) {
  try { fs.mkdirSync(p, { recursive: true }); } catch (_) { /* ignore */ }
  shell.openPath(p).then((err) => { if (err) log('openPath ' + p + ': ' + err); });
}

function openComfyDesktop() {
  const exe = cfg.comfyA.desktopExe;
  if (!exe || !fs.existsSync(exe)) {
    dialog.showMessageBox({ type: 'info', message: 'ComfyUI Desktop was not found', detail: `Expected it at:\n${exe}\n\nEdit desktop/config.json (comfyA.desktopExe) if it lives elsewhere.` });
    return;
  }
  try {
    spawn(exe, [], { detached: true, stdio: 'ignore', cwd: path.dirname(exe) }).unref();
    log('launched ComfyUI Desktop');
  } catch (e) {
    log('could not launch ComfyUI Desktop: ' + e.message);
  }
}

async function restartService(key) {
  const name = key === 'backend' ? 'backend' : 'ComfyUI B';
  let r = await supervisor.restart(key);
  if (r.result === 'needs-confirm') {
    const pids = r.foreign.map((p) => p.pid).join(', ');
    const choice = await dialog.showMessageBox({
      type: 'question',
      buttons: ['Stop it and start a new one', 'Cancel'],
      defaultId: 0,
      cancelId: 1,
      message: `The ${name} was not started by this launcher`,
      detail: `A matching process is running (PID ${pids}). Restarting stops it and starts a new one owned by the launcher. Any job it is running is lost.`,
    });
    if (choice.response !== 0) return;
    r = await supervisor.restart(key, { force: true });
  }
  log(`restart ${key}: ${r.result}`);
}

function sendState() {
  if (!supervisor) return;
  const snap = supervisor.snapshot();
  if (statusWindow && !statusWindow.isDestroyed()) statusWindow.webContents.send('state', snap);
  if (tray) tray.setToolTip(trayTooltip(snap));
}

ipcMain.handle('state', () => supervisor.snapshot());
ipcMain.handle('action', async (_e, id) => {
  log('action: ' + id);
  switch (id) {
    case 'openComfyDesktop': openComfyDesktop(); break;
    case 'startComfyB': supervisor.autoStarted.comfyB = true; supervisor.startComfyB(); break;
    case 'startBackend': supervisor.autoStarted.backend = true; supervisor.startBackend(); break;
    case 'retryBuild': supervisor.retryBuild(); break;
    case 'restartBackend': await restartService('backend'); break;
    case 'restartComfyB': await restartService('comfyB'); break;
    case 'openApp': createMainWindow(); break;
    case 'openLogs': openFolder(cfg.resolve(cfg.logsFolder)); break;
    case 'openExports': openFolder(cfg.resolve(cfg.exportsFolder)); break;
    case 'refresh': supervisor.tick(); break;
    case 'quit': requestQuit(); break;
    default: log('unknown action ' + id);
  }
  sendState();
  return true;
});

// ---- quit -----------------------------------------------------------------------------------------------------

async function requestQuit() {
  if (quitting) return;
  const owned = ['comfyB', 'backend'].filter((k) => supervisor && supervisor.isOwned(k));
  if (owned.length && !process.env.VGAC_DESKTOP_EXIT_AFTER_MS) {
    const names = owned.map((k) => (k === 'backend' ? 'the backend' : 'ComfyUI B')).join(' and ');
    const choice = await dialog.showMessageBox({
      type: 'question',
      buttons: ['Quit and stop them', 'Cancel'],
      defaultId: 0,
      cancelId: 1,
      message: 'Quit Video Game Asset Creator?',
      detail: `This stops ${names}, which this launcher started. ComfyUI A (ComfyUI Desktop) is left running.`,
    });
    if (choice.response !== 0) return;
  }
  quitting = true;
  app.quit();
}

app.on('before-quit', () => {
  quitting = true;
  if (supervisor) { supervisor.stop(); supervisor.stopOwned(); }
  if (tray) { tray.destroy(); tray = null; }
  log('quit');
  if (logStream) logStream.end();
});

app.on('window-all-closed', () => { /* keep running in the tray */ });

// ---- startup --------------------------------------------------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } else showStatusWindow(); });

  app.whenReady().then(() => {
    try {
      cfg = loadConfig();
    } catch (e) {
      dialog.showErrorBox('VGAC launcher', 'Could not read config.json: ' + e.message);
      app.quit();
      return;
    }
    try {
      const lp = cfg.resolve(cfg.launcherLog);
      fs.mkdirSync(path.dirname(lp), { recursive: true });
      logStream = fs.createWriteStream(lp, { flags: 'a' });
    } catch (_) { /* console only */ }
    log(`launcher start; config ${cfg.file}; projectRoot ${cfg.projectRoot}; packaged ${app.isPackaged}`);

    supervisor = new Supervisor(cfg, log);
    supervisor.on('state', sendState);
    supervisor.on('all-up', () => { log('all services up'); if (!mainOpened) openMainWindow(); });
    supervisor.on('service-up', (key) => {
      // The app window shows an error page when the backend was down; reload it once the backend is back.
      if (key === 'ui' && mainWindow && mainOpened) mainWindow.webContents.reload();
    });

    buildAppMenu();
    createTray();
    createStatusWindow();
    supervisor.start();

    // Test hook: quit by itself after N ms, saving window captures first.
    const exitAfter = Number(process.env.VGAC_DESKTOP_EXIT_AFTER_MS || 0);
    if (exitAfter > 0) {
      setTimeout(async () => {
        const dir = process.env.VGAC_DESKTOP_SNAPSHOT_DIR;
        if (dir) {
          // capturePage never resolves on a hidden window, so capture only visible ones and give up after 5 s.
          const capture = async (win, file) => {
            if (!win || win.isDestroyed() || !win.isVisible()) return;
            const img = await Promise.race([win.webContents.capturePage(), new Promise((res) => setTimeout(() => res(null), 5000))]);
            if (img) fs.writeFileSync(path.join(dir, file), img.toPNG());
          };
          try {
            fs.mkdirSync(dir, { recursive: true });
            await capture(statusWindow, 'status.png');
            await capture(mainWindow, 'main.png');
            fs.writeFileSync(path.join(dir, 'state.json'), JSON.stringify({ mainOpened, hasMainWindow: !!mainWindow, statusVisible: !!(statusWindow && statusWindow.isVisible()), snapshot: supervisor.snapshot() }, null, 2));
          } catch (e) { log('snapshot failed: ' + e.message); }
        }
        requestQuit();
      }, exitAfter);
    }
  });
}
