# VGAC starter (VGAC.exe)

The program you double-click after cloning. It does only what must exist before the app's own server can run, then
starts the app and opens it in its own window. Everything else (ComfyUI, Blender, Unity, models) is done inside the
app, on the Health page, in the setup card.

## What it does, in order

1. Reads `local/machine.json` and `local/state.json`. If nothing changed since the last start, it goes straight to step 5.
2. Shows what it is about to do, the room it needs and where, and the hosts it will contact, with Start, Not now and
   "Choose another folder" for the big files.
3. Gets `uv` (pinned version, SHA-256 checked) and makes the app's private Python 3.13 and `backend/.venv` with it.
   Nothing is added to Windows: no registry entries, no PATH change, no administrator rights.
4. Installs `backend/requirements.txt`; builds the screens (`npm ci`, `npm run build`) with the Node found on the PATH
   (20 or newer) or a pinned Node zip put in the data folder.
5. Starts the backend on 127.0.0.1:8765 (or uses the one already answering there), waits for `/api/health`, and opens
   the app. The starter then stays running as the app's host (see "Staying running").

Records: `runtime/logs/setup.log` (one line per action; "Open log" in the window), `local/installed.json` (what was
added), `local/state.json` (what the install was made from). The big files go to `local/data` in the project unless
a folder was chosen (`machine.json`, section `starter`, key `dataFolder`).

## Build

```
starter\build.cmd         builds ..\VGAC.exe with the C# compiler that ships with Windows (nothing to install)
starter\build.cmd test    builds and runs the tests (temporary folders, no network)
```

The source is C# 5 so that compiler can build it. A rebuild is not byte-identical to the committed file (the in-box
compiler has no deterministic mode), so `CHECKSUMS.txt` is for spotting a damaged copy, not for proving the build.
To trust the exe without trusting the checksum, rebuild it and use your own.

`VGAC.exe --plan out.json` writes what a start would do and changes nothing.

## The app's window and the bridge

The app opens in its own window (Microsoft WebView2; files and pins in `bin/`) when Microsoft's WebView2 runtime is
installed, and in the default browser when it is not (the window says so). Pages of other sites open in your browser,
never in this window. The page gets `window.vgacHost` only from the backend's own address, with `hello()`,
`pickFolder(title)` and `pickFile(title, "Name|pattern")`, each returning a path or null. The page sends the path to the
backend, which checks it. `VGAC.exe --window <url>` opens only the window on a page (for checks).

## Staying running

After the app opens the starter stays in the tray (the V icon). It does what the Electron launcher in `desktop/` does:

- Status window: the four services (ComfyUI A, ComfyUI B, the backend, the screens) with a plain line each, checked every
  two seconds, and the buttons that fit (Restart, Start, Build again, Open ComfyUI Desktop). Tray menu: open the app, the
  status window, restart the backend, open the logs or exports folder, open ComfyUI Desktop, open the settings file, Quit.
  Left click on the tray icon brings the app forward; the tray tooltip says how many services are up.
- Taskbar badge and notifications for the app's pages (`window.vgacDesktop`: `setWork`, `notify`, `startEngine`, the same
  three calls the Electron window gave). A notification is shown only while the app window is not in front.
- One copy per project folder: starting VGAC.exe a second time says nothing and brings the first window forward.
- Closing the app window means quit. Before the app has opened (or when it opened in the browser) closing the status
  window means quit; afterwards it hides to the tray.

What it starts and stops. It starts one thing: the backend (and it asks the backend, never a program of its own, to bring
up ComfyUI B). The backend's output goes straight to `runtime/logs/backend.log`.

- Quit asks first when it would stop something: "This stops the app's engine ... ComfyUI Desktop and anything else you
  started yourself is left running", and it says how many jobs are running or waiting and will be lost (Cancel is then the
  default button). With nothing of its own running it quits without a question. Quit during setup asks too and stops the
  setup program that is running; starting again carries on where it stopped.
- Quit stops the backend this starter started and the graphics engines that backend started (they are its children).
  It never stops a ComfyUI that someone else started, ComfyUI Desktop, or a backend it did not start.
- If the starter is killed or crashes, the backend and its engines keep running and keep writing their logs. The next start
  takes the backend over (so Quit stops it) only when the starter that wrote `local/starter-owned.json` has ended and the
  record still matches that exact process (id, start time, the app's own Python and the command line). Anything else is
  treated as someone else's and left alone. Every process is stopped through a handle that was checked against its start
  time, so a process id that Windows gave to another program is never stopped; a start time that is unknown means no stop.
- Restarting the backend asks first when work would be lost (jobs running or waiting) or when the backend was not started
  by this starter; Cancel is the default. It stops that one process only (not its engines) and starts a new one. Quit and
  restart cannot overlap: once Quit has begun no new backend is started.
- Starting VGAC.exe a second time waits up to three seconds for the first copy to confirm; if it does not (stuck, or run
  by another user) it says so.

Test switches: `VGAC.exe --exit-after <ms> --state-out <file>` quits by itself without asking (but not while an owned backend has jobs running) and writes what the status
window showed; `--keep-status` keeps the status window open next to the app.
