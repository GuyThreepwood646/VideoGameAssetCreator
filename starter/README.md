# VGAC starter (VGAC.exe)

The program you double-click after cloning. It does only what must exist before the app's own server can run, then
starts the app and opens it in your web browser. Everything else (ComfyUI, Blender, Unity, models) is done inside the
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
   the app. Closing the starter window stops a backend this starter started, and never one it did not start.

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

## Not done yet

The window that shows the app itself (WebView2) and the folder-chooser bridge to the page: the app opens in the
default browser. See the installer plan, phases 2 and 8.
