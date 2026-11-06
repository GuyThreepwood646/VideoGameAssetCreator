# Building the VGAC .exe (the launcher)

Written 2026-11-06. Everything here was checked against this PC on that day.

## The short version

You already have it. The file is

    F:\Projects\VideoGameAssetCreator\desktop\dist\VGAC-portable.exe

and the "VGAC" shortcut on your desktop points at it. Double-click the shortcut and the app opens. You only need the steps below if you want to build the file again.

## What the .exe is, and what it is not

- It is a launcher (a small program that starts the app's parts and opens its window). It is about 100 MB.
- When you start it, it shows a status window, starts the second ComfyUI and the backend (the app's engine room) if they are not already running, and then opens the app in its own window. It also puts an icon in the tray (the row of small icons by the clock) with a Services menu.
- It does not contain the app. The app itself lives in the project folder `F:\Projects\VideoGameAssetCreator`, and the launcher reads it from there. So the .exe on its own, copied to another PC, will not work. That PC would also need the project folder and everything the app depends on (ComfyUI, the models, Blender, Unity). A separate guide for that is being written: `docs/FIRST-TIME-SETUP.md`.

## When you need to build it again

Almost never.

- Changes to the app's screens or its engine room do not need a new .exe. The launcher picks them up the next time it starts.
- You need a new .exe only when a file inside the `desktop` folder changes (the launcher's own code or its icon). The last such change was on 4 November, and the .exe you have was built after it, so it is current.

## Before you start (one time only)

1. Node.js must be installed. It is on this PC (version 22). To check, open PowerShell and run:

    ```powershell
    node --version
    ```

2. The launcher's building tools must be downloaded into the project. They are already there on this PC (the folder `desktop\node_modules`, about 440 MB). If that folder is ever missing, this puts it back (needs the internet, takes a few minutes):

    ```powershell
    cd F:\Projects\VideoGameAssetCreator\desktop
    ```

    ```powershell
    npm install
    ```

## Building the .exe

1. Close the app if it is open: right-click the VGAC icon in the tray and choose Quit. Windows cannot replace the .exe while it is running.

2. Open PowerShell and go to the launcher's folder:

    ```powershell
    cd F:\Projects\VideoGameAssetCreator\desktop
    ```

3. Remove the old settings file that sits beside the .exe, so a fresh one is written the next time the launcher starts. (The build does not replace this file by itself.)

    ```powershell
    Remove-Item .\dist\config.json -ErrorAction SilentlyContinue
    ```

4. Build:

    ```powershell
    npm run dist
    ```

    This draws the icon and then packs the launcher. It takes a few minutes. The first time ever, it also downloads some packing tools, so it needs the internet. When it finishes without red error text, the new file is at `desktop\dist\VGAC-portable.exe`.

5. Point the desktop shortcut at the new file (only needed if the shortcut is missing or broken; it normally keeps working):

    ```powershell
    powershell -ExecutionPolicy Bypass -File make-shortcut.ps1
    ```

## Checking that it worked

1. Double-click the VGAC shortcut on the desktop. The window takes several seconds to appear, because this kind of .exe unpacks itself each time it starts.
2. The status window should show four lines (ComfyUI A, ComfyUI B, Backend, UI) turning ready, and then the app opens.
3. If a line stays red, open the tray icon's Services menu and choose the logs folder; the file `launcher.log` says what it was waiting for.

## Changing where things are

The launcher reads its settings from `config.json` in the same folder as the .exe (`desktop\dist\config.json`). It holds the project folder, the addresses of the two ComfyUIs and the backend, and the window size. Edit it in Notepad and start the launcher again. If you delete it, the launcher writes a fresh copy from the one built into the .exe.

You can move the .exe (together with its `config.json`) anywhere on this PC, because the settings give the project folder as a full path. If you move it, run the shortcut step again from the `desktop` folder, or make a new shortcut by hand.

## If something goes wrong

- "The process cannot access the file" or the build fails at the very end: the launcher is still running. Quit it from the tray and build again.
- "Windows protected your PC" when you start a freshly built .exe: the file is not signed (signing costs money). Click "More info", then "Run anyway".
- `npm` is not recognised: Node.js is not installed or PowerShell was opened before it was installed. Install Node.js from nodejs.org and open a new PowerShell window.
- The build complains that Electron is missing: run this once in the `desktop` folder, then build again:

    ```powershell
    node node_modules/electron/install.js
    ```

## Running without the .exe

You never need the .exe to use the app. From the `desktop` folder, this starts the same launcher straight from its source:

```powershell
npm run start
```
