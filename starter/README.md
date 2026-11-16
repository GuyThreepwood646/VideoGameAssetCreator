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
- Keys: Ctrl+Q quits (asked first), Ctrl+Shift+S shows the status window, F11 makes the app window full screen and back;
  they work also while the page has focus. Each key that arrives, and each notification the page asks for (and whether it
  was handed to Windows or why not), is a line in `runtime/logs/setup.log`, so a missing popup can be told from one that
  Windows did not show.
- One copy per project folder: starting VGAC.exe a second time says nothing and brings the first window forward.
- Closing the app window means quit. Before the app has opened (or when it opened in the browser) closing the status
  window means quit; afterwards it hides to the tray.

What it starts and stops. It starts one thing: the backend (and it asks the backend, never a program of its own, to bring
up ComfyUI B). The backend's output goes straight to `runtime/logs/backend.log`, and it inherits only its log handles, nothing else this
program has open.

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

## Maintenance (tray menu, "Maintenance..." on the status window)

- Check and repair: a read-only check of what VGAC installed (the app's Python and packages, the screens, uv and Node, the
  records), a plain line for each, and a Fix button that only ever changes what VGAC made. A rebuild puts the old part aside
  first and puts it back if the new one does not work. Linked tools are reported, never changed.
- Update: "Update VGAC now" runs `git pull --ff-only` (nothing is merged or overwritten). VGAC.exe is running, so it renames
  itself to VGAC.old.exe just before the pull (Windows allows that) and git writes the new one; restart VGAC afterwards. The old
  file is deleted at the next start only if it is the file VGAC recorded and it was the published one (a build you made
  yourself is kept). If the rename fails, nothing is changed. While VGAC.exe is aside a note, VGAC-RESTORE.txt, sits beside the project's
  files saying how to put it back (rename VGAC.old.exe to VGAC.exe) in the rare case the computer stops right then; it is removed when the
  update is through. The screens are built beside the live ones and swapped in with two
  renames, so the running app never sees a half-built folder. Updating asks first when jobs are running, like Quit. The new Python
  packages and screens that an update needs are not installed in the same click: you are shown the steps (they run pip and npm from the
  files just pulled) and say yes. A second update does not replace a VGAC.old.exe that is your own build.
  **If you update by hand in a terminal, close VGAC first, then pull.** A pull that stopped halfway is noticed at the next start
  ("A pull did not finish. Close VGAC, then pull again.").
- The folder for the big files is VGAC's only when it carries one marker file (`.vgac-data.json`) whose random id is also in
  `local/machine.json` (`starter.dataFolderId`). VGAC claims a folder that is new or empty, before any setup step puts anything in it.
  Inside a claimed folder the fixed names (tools\uv, tools\node, python, uv-cache, downloads, webview2, comfyui, and their .old and
  .new leftovers) are VGAC's, and removed or moved whole; anything else in the folder is left for you and named in the summary. A
  missing or damaged marker, or another id, means "not ours": nothing is deleted or moved. There are no paths in the marker or the id, so
  moving the project folder, renaming the data folder (edit the setting) or changing a drive letter keeps it ours. An install made by
  an earlier version has no marker: Check and repair (or the screen setup shows when it needs the folder) offers "Use this folder for
  VGAC only" with the exact list, but only when every entry is a fixed name with the shape only VGAC's setup makes (uv's Python folder
  that VGAC's own Python environment uses, uv's cache with its tag, uv.exe and node.exe, only the setup's own downloads, only the
  window's EBWebView folder, ComfyUI with the backend's marker). Otherwise it offers another folder. A whole drive, your profile,
  Documents, Downloads, Public, Program Files, the project or a part of it are never accepted (checked by real path, at every start).
- Move the big files (with VGAC's own ComfyUI present, the backend's installer must confirm it first: `check`; after the switch the starter
  asks it to `retarget` the settings and `rebuild-venv`; if it cannot, the move is refused before anything is copied, or waits with the reason): copies the fixed-name folders of a claimed data folder (comfyui without its Python) to another folder or
  drive, checks every file by size and SHA-256, and only then switches (one file write). The new place is claimed (same id) when the move
  begins. A folder that holds a link leading outside it is not moved; links between places inside one folder (uv's Python keeps some)
  are copied as links. Until the switch the old place is whole and in use. Interrupted at any point, the next start offers to continue
  or cancel (`local/move.json`; a journal whose two places do not both fit this install is set aside, never acted on). Cancel before
  the switch removes only what the move copied into the new place it claimed. Cancel after the switch only points the settings back:
  the new copy is left alone, because the app may have written to it. The old copy is deleted only after you confirm and after every file
  is compared with the new copy again; "Keep both copies" closes the move without deleting anything.
- Remove: a list of what VGAC added, with sizes, and nothing is deleted until you have seen every folder and its size. Items in the
  data folder are listed only when it is VGAC's (its marker matches); otherwise the list says why and nothing in it is removed. Ticked by
  default: the app's Python, tools and screens, and VGAC's own ComfyUI, which is removed by the backend's own installer (`python -m vgac.comfy_install remove`, which proves
  it is VGAC's by its marker AND the ledger and keeps `ComfyUI\models`): everything else in that folder goes, its output, input and user
  folders and custom nodes too, and the list says exactly what stays, as the installer reports it. The starter keeps no list of what is
  owned or kept there. VGAC's own ComfyUI is handled first because the command needs the app's Python. Not ticked: models and packs VGAC downloaded, node packs it put into a ComfyUI you linked, its settings, and your own
  work (the word REMOVE must be typed). Your own work is a library folder that carries VGAC's own marker (`.vgac-library.json`, whose id is also in
  `starter.libraryId`; made at start for a library that is new or empty, or by the "mark as VGAC's library" click in Check and repair for an
  existing one that holds a real vgac.sqlite and only things the app makes) AND holds only things the app makes (the database, projects,
  hair, moves and settings entries): without the marker, or with anything else in the folder, nothing in it is removed (names alone prove nothing). Exports are removed one export folder at a time, only those holding VGAC's manifest.json; the folder an export
  sits in (for example a Unity project's Assets) is never removed. Links are unlinked, never followed. Small amounts go to the Recycle Bin;
  more than 2 GB, a drive that is not a fixed local one, or a folder with a link inside is deleted for good, and the list says so per item.
  If the Recycle Bin cannot take an item it is NOT deleted: you are asked (the answer is No unless you click Yes) whether to delete exactly
  those items for good. The project folder, VGAC.exe and anything VGAC did not make are never touched.

Test switches: `VGAC.exe --exit-after <ms> --state-out <file>` quits by itself without asking (but not while an owned backend has jobs running) and writes what the status
window showed; `--keep-status` keeps the status window open next to the app.
