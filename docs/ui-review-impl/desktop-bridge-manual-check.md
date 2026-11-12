# Desktop bridge: manual check (UI-31 badge and notifications, UI-32 Start the engine)

Automated: `node desktop/appbridge.test.js` covers the sender check, count, icon and text cleaning. The Electron wiring
(`desktop/main.js`, `desktop/preload-app.js`) was syntax-checked only; no launcher was started in this session because
starting it would try to take over port 8765 while the backend was in use for the UI-47 run.

Security shape: the app window gets `desktop/preload-app.js` with contextIsolation on, the sandbox on and nodeIntegration
off. It exposes `window.vgacDesktop.{setWork, notify, startEngine}` and nothing else (no ipcRenderer, no channel names).
Each IPC handler in `main.js` accepts only the app window's own `webContents` and a frame on the backend's origin, and
cleans every value (`desktop/appbridge.js`). The existing permission handler still denies web permissions, so the page
cannot raise web notifications; the launcher raises them instead.

Steps (about 5 minutes, with the launcher on a spare port or with the real backend stopped first):
1. Start the launcher (`npm start` in `desktop`). The app window opens.
2. In the window's dev tools console (View menu): `typeof window.vgacDesktop.setWork` is `function`;
   `Object.keys(window.vgacDesktop)` lists exactly `setWork,notify,startEngine`; `window.require` and `process` are undefined.
3. Start an Autopilot run. While a job runs or waits, the taskbar icon shows a red disc with the number of jobs; it clears
   when the queue is empty.
4. Minimise the window until the run finishes or pauses: a system notification "Knight is ready" or "Knight needs you: ..."
   appears; clicking it brings the window back. With the window in front, no system notification (the toast shows).
5. Stop the backend from outside (Task Manager, end the uvicorn python.exe). Within about 10 seconds the red banner appears
   with "Start the engine". Click it: the button says "Starting...", the launcher restarts the backend (a confirmation dialog
   appears first if the old process was not started by the launcher), and the banner goes away.
6. Open the same address in an ordinary browser: the banner has no button and keeps the old tray-menu sentence.
