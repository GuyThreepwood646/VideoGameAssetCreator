# Security audit

Date: 2026-11-04. Read-only review of the working tree at that date (git HEAD plus uncommitted edits to `backend/vgac/main.py`, `stages/mesh.py` and others that were in progress; line numbers in `main.py` are from that snapshot and may drift by a few lines).

## Scope

Video Game Asset Creator is a local, single-user desktop app: a FastAPI backend on `127.0.0.1:8765` (`backend/vgac`), a Vite + React frontend served by that backend (`frontend/src`), an Electron launcher (`desktop/`), and two local ComfyUI instances on `127.0.0.1:8188/8189`. The audit covered path handling, subprocess launching, network exposure, input validation, Electron hardening, secrets in git, resource limits and dependencies. No servers were started. Three behaviours were confirmed with a throwaway FastAPI app in a scratch folder, using the project's own venv (FastAPI 0.162.0, Starlette 1.10.0): JSON bodies sent cross-site are refused, body-less POSTs and WebSocket upgrades from a foreign Origin are accepted, and any Host header is accepted.

The threat model that matters for a loopback-only app is **another web page open in the user's normal browser** talking to `127.0.0.1:8765`. That route is not blocked by binding to loopback. Most other issues need a local attacker who could already run code as the user, so they are rated Low or Info.

## Findings

| ID | Severity | File:line | Title |
|----|----------|-----------|-------|
| F1 | Medium | backend/vgac/main.py:722-724 | WebSocket `/api/ws/events` accepts any Origin; any web page can read the live event stream |
| F2 | Medium | backend/vgac/main.py:54-62 | No Host header check; DNS rebinding gives a web page full API access, including the SQLite file |
| F3 | Medium | backend/vgac/stages/export.py:294-304 | Export `_slug` keeps dots, so a project or asset named `..` writes deliverables outside `exports/` |
| F4 | Low | backend/vgac/main.py:521-525, 569, 577, 705, 713 | Body-less POST endpoints can be triggered cross-site (CSRF) |
| F5 | Low | backend/vgac/main.py:83, 90, 304, 321; stages/export.py:321-341 | `unity_project_path` is not validated; backend writes an Editor script there and runs Unity on it |
| F6 | Low | backend/vgac/main.py:114, 122, 126; stages/export.py:168-176; stages/concept.py:42-43 | Stage `params` are not checked against `STAGE_SCHEMAS`; sizes and counts are unbounded |
| F7 | Low | backend/vgac/main.py:121; stages/__init__.py:32-39 | `mask_png` has no size limit and is decoded without validation |
| F8 | Low | backend/vgac/stages/rig.py:123-135 | Rig stage copies any file path that ComfyUI reports into the library |
| F9 | Low | desktop/main.js:97-117, 249 | Electron main window: no navigation guard, no CSP, no permission handler, IPC sender not checked |
| F10 | Low | frontend/vite.config.ts:30-32 | Mock-mode library server uses a string prefix check for containment |
| F11 | Info | desktop/config.json:7; runtime/start-comfy-b.ps1:4,7; backend/config.yaml:15,17; backend/vgac/graphs/concept.py:4; bakeoff/mesh.py:21 | Personal absolute paths (including the Windows user name) are committed |
| F12 | Info | backend/restart.ps1:6; desktop/config.json:23 | Backend bind address is implicit in two launch paths |
| F13 | Info | backend/requirements.txt; frontend/package.json; desktop/package.json | Dependency hygiene: no audit tooling, runtime and dev deps mixed |

Counts: Critical 0, High 0, Medium 3, Low 7, Info 3.

---

### F1. WebSocket accepts any Origin (Medium)

**What the code does.** `backend/vgac/main.py:722-724`:

```python
@app.websocket("/api/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
```

`CORSMiddleware` (lines 56-62) only handles `http` scopes, and browsers do not apply CORS to WebSocket upgrades. A test against the same FastAPI/Starlette versions confirmed that a socket with `Origin: https://evil.example` is accepted and receives `hello`.

**Why it matters.** Any web page the user has open can run `new WebSocket("ws://127.0.0.1:8765/api/ws/events")` and read every event. Those events carry `job_to_dict` and `version_to_dict` (db.py:273-287): asset, version and job IDs, full `params` (prompts, `instruction`), `files`, `metrics` (including `delivered_to` absolute paths) and error text. Prompts and paths leak, and the IDs are what F4 needs to act on specific assets. No authentication exists anywhere, so this is the easiest cross-site way in.

**Fix.** Check the Origin header before accepting. The Electron window and the Vite dev server both send a loopback origin:

```python
import re
LOCAL_ORIGIN = re.compile(r"https?://(localhost|127\.0\.0\.1)(:\d+)?")

@app.websocket("/api/ws/events")
async def ws_events(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    if origin is not None and not LOCAL_ORIGIN.fullmatch(origin):
        await ws.close(code=1008)
        return
    await ws.accept()
    ...
```

Reuse `LOCAL_ORIGIN` for the CORS `allow_origin_regex` so there is one definition. Add a test using `client.websocket_connect("/api/ws/events", headers={"origin": "https://evil.example"})` that expects a disconnect.

**Effort.** S.

### F2. No Host header validation; DNS rebinding (Medium)

**What the code does.** `backend/vgac/main.py:54-62` adds only CORS:

```python
app = FastAPI(title="Video Game Asset Creator", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
```

There is no `TrustedHostMiddleware`, and a request with `Host: rebind.evil.example:8765` returned 200 in the isolated test.

**Why it matters.** With DNS rebinding, an attacker's domain first resolves to their server and then to `127.0.0.1`. The page then counts as same-origin with the backend, so CORS never applies. The page can read and write the whole API. That includes `GET /api/library/vgac.sqlite` (the library root holds the database, so the `/api/library` handler at lines 743-755 will serve it), setting `unity_project_path` (F5), queueing jobs and deleting projects. Some browsers now block public pages from reaching loopback addresses (Private Network Access or Local Network Access prompts), but coverage depends on the browser and version, so do not rely on it.

**Fix.** Add one middleware line next to CORS. Starlette's version also covers WebSocket scopes:

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
```

(`testserver` keeps `TestClient` tests working.) The Vite proxy sets `changeOrigin: false`, so dev requests arrive with `Host: localhost:5173`, which still matches `localhost`. If you want defence in depth, consider moving the database out of the served folder. Alternatively, make `library_file` refuse `vgac.sqlite*` and `*.log`:

```python
if target.name.startswith("vgac.sqlite") or target.suffix.lower() == ".log":
    raise HTTPException(404, "file not found")
```

**Effort.** S.

### F3. Export delivery path escapes `exports/` (Medium)

**What the code does.** `backend/vgac/stages/export.py:294-304`:

```python
def _slug(text: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "asset"
...
    export_root = Path(sc.settings.export_path) if sc.settings.export_path else Path(sc.settings.library_path).parent / "exports"
    dest = export_root / _slug(sc.project.name) / _slug(sc.asset.name)
    dest.mkdir(parents=True, exist_ok=True)
```

The character class keeps `.`, and `.strip("_")` does not remove dots, so `_slug("..")` returns `..` (confirmed). Lines 310-317 then call `shutil.copy2(src, dest / Path(src).name)`, which overwrites existing files without asking.

**Why it matters.** Names are accepted with no character rules (`ProjectIn.name` min 1 / max 200, `AssetPatch.name` has no constraint at all, main.py:104). With the default config:
- project `..` sends files to the repo root;
- project `..` plus asset `..` sends them to `F:\Projects`;
- asset `..` sends them to `exports/` itself.

The copied files have fixed names (`manifest.json`, `normal.png`, `base_color.png`, `<slug>_<id>.fbx`), so same-named files in those folders are overwritten. A second problem has the same cause: `My Game` and `My_Game` map to the same folder and overwrite each other's deliverables. The user would normally type the name, so the likelihood is low. But this is a real write outside the intended root, and F2 makes it reachable from a web page. The Blender and Unity export names use `stages/base.py:29-31` `slug()`, which only keeps `[A-Za-z0-9]`, so those are safe.

**Fix.**

```python
def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_-")
    return s or "asset"

...
    root = export_root.resolve()
    dest = (root / _slug(sc.project.name) / _slug(sc.asset.name)).resolve()
    dest.relative_to(root)  # raises ValueError if it escapes
```

To avoid collisions, consider `f"{_slug(sc.asset.name)}_{sc.asset.id[:8]}"` for the asset folder, matching `asset_export_name`. Add a unit test that `_slug("..")`, `_slug(".")` and `_slug("a/../b")` contain no `..` and no separator.

**Effort.** S.

### F4. Cross-site request forgery on body-less POSTs (Low)

**What the code does.** These endpoints take no JSON body, or an optional one:
- `POST /api/assets/{aid}/autopilot` (main.py:521-525, `body: AutopilotIn | None = None` then `body = body or AutopilotIn()`)
- `/autopilot/stop` (539)
- `/api/versions/{vid}/select` (569)
- `/api/versions/{vid}/reject` (577)
- `/api/jobs/{job_id}/cancel` (705)
- `/api/queue/{instance}/pause` (713-714, query param `paused`)

A cross-site `fetch(url, {method: "POST", mode: "no-cors"})` is a CORS "simple request": it is sent without a preflight and the handler runs, even though the page cannot read the response. The isolated test confirmed that the optional-body autopilot pattern returns 200 for a foreign Origin.

Endpoints that take a JSON body are protected. FastAPI 0.162 defaults to `strict_content_type=True`, and both a missing Content-Type and `text/plain` returned 422 in the test. DELETE always needs a preflight, which the CORS regex refuses.

**Why it matters.** Any page can pause or unpause queues `a`, `b` and `host`, since those names are fixed. With IDs from F1 it can also start or stop autopilot, cancel running GPU jobs and reject versions. This is a nuisance and can lose work, but it cannot read data or run code.

**Fix.** Refuse state-changing requests whose Origin is foreign. Browsers always send Origin on cross-origin POST, PUT and DELETE. Same-origin requests and non-browser clients (tests, curl, the Electron probe) either send a matching Origin or none:

```python
from fastapi.responses import JSONResponse

@app.middleware("http")
async def refuse_cross_site_writes(request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and not LOCAL_ORIGIN.fullmatch(origin):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    return await call_next(request)
```

Together with F1 and F2 this closes the browser route. A per-launch bearer token (generated by Electron, passed to the backend as an environment variable, injected into the page) would be stronger, but it is not needed for a single-user tool. That would be M effort.

**Effort.** S.

### F5. `unity_project_path` is unvalidated and Unity is run on it (Low)

**What the code does.** `ProjectIn` and `ProjectPatch` accept any string (main.py:83, 90), stored as-is at lines 304 and 321. At export, `stages/export.py:321-341` does this:

```python
proj = Path(proj_path)
if not (proj / "Assets").is_dir():
    ...
editor_dir = proj / "Assets" / "Editor"
editor_dir.mkdir(parents=True, exist_ok=True)
...
    shutil.copy2(script_src, editor_dir / "VgacImport.cs")
...
await sc.unity("VgacImport.Run", args, flags, "unity_project_import.log", project_path=proj)
```

`tools.py:60-68` builds an argv list (no shell), so there is no command injection.

**Why it matters.** This is the intended feature. The risks are (a) any folder that happens to contain `Assets/` gets an Editor script and a full Unity import, and (b) Unity batchmode runs every Editor script in that project, so pointing at an untrusted Unity project runs its code. Today, setting the path needs a JSON body, so only F2 makes it reachable from a web page. The script is copied only when absent (`not (editor_dir / "VgacImport.cs").is_file()`), so an outdated or foreign `VgacImport.cs` already in the target is executed as-is.

**Fix.** Validate when the path is saved and again when it is used:

```python
def _check_unity_project(raw: str) -> str:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise HTTPException(400, "unity_project_path must be absolute")
    p = p.resolve()
    if not (p / "Assets").is_dir() or not (p / "ProjectSettings" / "ProjectVersion.txt").is_file():
        raise HTTPException(400, "not a Unity project (Assets/ and ProjectSettings/ProjectVersion.txt required)")
    return str(p)
```

Call it in `create_project` and `update_project` before storing. In `_deliver`, compare the existing `VgacImport.cs` hash with the sandbox copy and overwrite it, or warn, when they differ.

**Effort.** S.

### F6. Stage params are not validated (Low)

**What the code does.** `RunIn.params`, `RefineIn.params` and `AdjustIn.params` are `dict[str, Any]` (main.py:114, 122, 126). They are merged into version params and passed to stages untouched. Stages cast and use them directly, for example:
- `stages/concept.py:42-43` `width = int(params.get("width") or 1024)` and `height = ...`;
- `stages/export.py:168-170` `directions`, `frames` and `size` from `sprite_directions`, `sprite_frames` and `sprite_size`;
- `stages/mesh.py:41-43` `target_faces`, `texture_size` and `upsample`.

`reference.STAGE_SCHEMAS` already defines the limits (width 512-2048, sprite_size 32-1024, sprite_directions 1-16, texture_size options 512-4096). The server never enforces them; only the UI does. `sprite_frames` is not in the schema at all. There is also no cap on how many jobs can be queued (`JobQueue.submit`, jobs.py:338), and `count` is the only bounded field (`le=4`, main.py:115).

**Why it matters.** This is self-inflicted unless F2 or F4 apply. `width=16384` makes ComfyUI run out of GPU memory. `sprite_directions=360, sprite_frames=1000, sprite_size=8192` keeps Blender busy for the full 3600 s timeout and fills the disk. Subprocesses use argv lists, so values cannot become shell commands. A value such as `collider="--formats"` only confuses the Blender scripts' own `argv` parsing (`blender/pack.py:33-34`), which is harmless.

**Fix.** Add one validator in `reference.py` and call it wherever user params enter: `_run_stage_internal`, `refine_version`, `adjust_version`, `create_variant`, and the autopilot `fix["params"]`:

```python
def validate_params(stage: str, params: dict[str, Any]) -> dict[str, Any]:
    schema = {p["name"]: p for p in STAGE_SCHEMAS[stage]}
    out = {}
    for k, v in params.items():
        s = schema.get(k)
        if s is None:
            raise ValueError(f"unknown param {k!r} for stage {stage}")
        if v is None:
            out[k] = None
            continue
        t = s["type"]
        if t in ("integer", "number"):
            v = int(v) if t == "integer" else float(v)
            if "min" in s and v < s["min"] or "max" in s and v > s["max"]:
                raise ValueError(f"{k} must be between {s.get('min')} and {s.get('max')}")
        elif t == "boolean":
            v = bool(v)
        elif t == "select" and v not in s["options"]:
            raise ValueError(f"{k} must be one of {s['options']}")
        elif t == "multiselect" and (not isinstance(v, list) or any(x not in s["options"] for x in v)):
            raise ValueError(f"{k} must be a subset of {s['options']}")
        elif t == "string":
            v = str(v)[:2000]
        out[k] = v
    return out
```

Add `sprite_frames` (integer, 1-32) to the export schema. Turn `ValueError` into `HTTPException(400)` in main.py. Optionally refuse new jobs when more than about 50 are already queued for an instance.

**Effort.** M (the validator is small, but every call site and the frontend's param names need checking).

### F7. `mask_png` is unbounded and decoded loosely (Low)

**What the code does.** `RefineIn.mask_png: str | None = None` (main.py:121) has no `max_length`. The whole string is stored in `Job.payload_json`, then `stages/__init__.py:32-39` runs:

```python
data = mask_b64.split(",", 1)[1] if mask_b64.startswith("data:") else mask_b64
p = sc.dir / "mask.png"
p.write_bytes(base64.b64decode(data))
```

`b64decode` without `validate=True` silently drops non-alphabet characters. Nothing checks that the result is a PNG. `imaging.apply_mask` then opens it with Pillow. The file name is fixed, so there is no path traversal.

**Why it matters.** A very large body is held in memory several times (request, JSON, DB row, decoded bytes). Uvicorn has no default body size limit. Pillow's default decompression-bomb guard (it errors above about 179 megapixels) limits damage from the image itself. Only local or F2-style callers can reach this, so it is Low.

**Fix.**

```python
mask_png: str | None = Field(default=None, max_length=12_000_000)  # about 9 MB of PNG
...
raw = base64.b64decode(data, validate=True)
if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
    raise ValueError("mask_png is not a PNG")
```

Optionally, open it with `Image.open(...)`, check that `.size` is at most 4096x4096, and write it from there.

**Effort.** S.

### F8. Rig stage trusts any path ComfyUI reports (Low)

**What the code does.** `backend/vgac/stages/rig.py:123-128`:

```python
def _locate_output(sc: StageContext, paths: list[str]) -> Path:
    client = sc.client()
    for p in paths:
        cand = Path(str(p).strip())
        if cand.is_file():
            return cand
```

`paths` is the text output of the SkinToken "show" node from ComfyUI's `/history`. Line 105 then copies the file into the version folder as `rig_raw.glb`, and `/api/library` will serve it.

**Why it matters.** If a ComfyUI custom node misbehaves or is malicious, or someone else's ComfyUI is reachable at the configured URL, any readable file on the machine can be copied into the library and served. ComfyUI is local and trusted in this setup, so the risk is Low. The fix also makes the stage more robust.

**Fix.** Only accept files under the instance's configured `output_dir`:

```python
root = client.output_dir.resolve() if client.output_dir else None
for p in paths:
    if root is None:
        break
    cand = (root / str(p).strip()).resolve()   # an absolute p replaces root, then fails the check below
    if cand.is_relative_to(root) and cand.is_file() and cand.suffix.lower() == ".glb":
        return cand
```

**Effort.** S.

### F9. Electron main window hardening gaps (Low)

**What the code does.** `desktop/main.js:106` sets `webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }` and no preload, which is good. Lines 114-117:

```js
mainWindow.webContents.setWindowOpenHandler(({ url }) => {
  if (/^https?:/i.test(url)) shell.openExternal(url);
  return { action: 'deny' };
});
```

What is missing: a `will-navigate` handler, so the window itself can navigate to any URL; a Content Security Policy (`frontend/index.html` has none, while `desktop/status.html` does); and `session.setPermissionRequestHandler`. The `ipcMain.handle('action', ...)` at line 249 does not check `event.senderFrame`. `preload.js:4-8` exposes `action(id, arg)` to the status window only.

**Why it matters.** The renderer is sandboxed, so a script injected into the app page would not get Node access. These gaps only matter together with a future XSS or an accidental same-window link to a remote page. React escapes text, and no `dangerouslySetInnerHTML` or `innerHTML` was found in `frontend/src`.

**Fix.**

```js
const appOrigin = new URL(cfg.backend.url).origin;
mainWindow.webContents.on('will-navigate', (e, url) => {
  if (new URL(url).origin !== appOrigin) {
    e.preventDefault();
    if (/^https?:/i.test(url)) shell.openExternal(url);
  }
});
mainWindow.webContents.session.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));

ipcMain.handle('action', async (e, id) => {
  if (!statusWindow || e.sender !== statusWindow.webContents) return false;
  ...
});
```

Add a CSP for the built UI, either as a response header from the backend's static mount or with `session.webRequest.onHeadersReceived` in Electron. Use something like `default-src 'self'; img-src 'self' blob: data:; connect-src 'self' ws://127.0.0.1:8765; style-src 'self' 'unsafe-inline'`. Do not put it in `index.html`, because the Vite dev server's React refresh preamble is an inline script.

**Effort.** S.

### F10. Mock-mode library server prefix check (Low)

**What the code does.** `frontend/vite.config.ts:30-32`:

```ts
const rel = decodeURIComponent(url.slice('/api/library/'.length).split('?')[0])
const file = path.resolve(root, rel)
if (!file.startsWith(root) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
```

`startsWith(root)` also matches sibling folders such as `library-old/` or `library.bak/`, reachable through `../library-old/x`.

**Why it matters.** This runs only in `npm run dev:mock`, and the Vite dev server listens on localhost. The impact is reading files in a sibling folder whose name starts with `library`. Low.

**Fix.**

```ts
const relCheck = path.relative(root, file)
if (relCheck.startsWith('..') || path.isAbsolute(relCheck) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
```

**Effort.** S.

### F11. Personal absolute paths committed (Info)

**What the code does.** These tracked files contain machine-specific paths, several with the Windows user name:
- `desktop/config.json:7` (`C:\\Users\\evanr\\AppData\\Local\\Programs\\Comfy Desktop\\...`)
- `runtime/start-comfy-b.ps1:4` and `:7` (`C:\Users\evanr\ComfyUI-Installs\...`, `...\instance-model-paths\inst-1790285903050.yaml`)
- `backend/config.yaml:15,17` (F: drive project paths)
- `backend/vgac/graphs/concept.py:4` (docstring)
- `bakeoff/mesh.py:21`

A scan of all 212 tracked files found no tokens, API keys, passwords or email addresses. The Unity `ProjectSettings.asset` "Secret" and "Password" keys are empty defaults. Logs, the SQLite database, `library/`, `exports/` and `runtime/*` are all gitignored. The repo has no remote configured.

**Why it matters.** Nothing is secret, but if the repo is ever published it reveals the user name and folder layout, and it does not run on another machine without edits.

**Fix.** Commit `backend/config.example.yaml` and `desktop/config.example.json` with placeholders. Gitignore the real files, and have the loaders fall back to the example with a clear error. In `start-comfy-b.ps1`, read the ComfyUI install path from an environment variable or from `desktop/config.json`. Remove the user path from the `concept.py` docstring.

**Effort.** S.

### F12. Backend bind address is implicit in two launch paths (Info)

**What the code does.** `backend/run.ps1:4` and `run.sh` pass `--host 127.0.0.1`. However, `backend/restart.ps1:6` (`-ArgumentList "-m","uvicorn","vgac.main:app","--port","8765"`) and `desktop/config.json:23` (`"args": ["-m", "uvicorn", "vgac.main:app", "--port", "8765"]`) do not. Uvicorn defaults to 127.0.0.1, so today the backend is loopback-only. But the uvicorn CLI reads `UVICORN_*` environment variables (`auto_envvar_prefix="UVICORN"` in uvicorn/main.py:61), and `services.js:244` passes the full `process.env` to the backend. A stray `UVICORN_HOST=0.0.0.0` would therefore expose the unauthenticated API to the LAN.

**Fix.** Add `"--host", "127.0.0.1"` to both argument lists.

**Effort.** S.

### F13. Dependency hygiene (Info)

**What the code does.** `backend/requirements.txt` pins exact versions (good), but mixes runtime packages with `pytest`, `trimesh` and `numpy`, which are only used by tests and `scripts/make_sample_glb.py`. The frontend and desktop use caret ranges with committed lockfiles. Electron resolves to 45.2.2 (installed). I have no confirmed CVE for any pinned or installed version, so nothing is claimed here. The repo has no `pip-audit` or `npm audit` step.

**Fix.**
- Move test and script dependencies to `requirements-dev.txt`.
- Run `pip-audit -r backend/requirements.txt`, then `npm audit --omit=dev` in `frontend/` and `desktop/`, and record the result in STATUS.md.
- Keep Electron on a currently supported major. Electron supports the latest three stable majors, so check where 45 stands.

**Effort.** S.

---

## Things that are fine

1. **`GET /api/library/{path}`** (main.py:743-755) resolves the target, checks `relative_to(root)`, and rejects backslashes and NUL bytes. An absolute or other-drive path (`C:/Windows/win.ini`) replaces the root when joined and then fails the containment check. `resolve()` follows symlinks and junctions, so a link inside `library/` that points outside is refused rather than followed. A regression test exists (`tests/test_api.py` `test_library_path_traversal_blocked`).
2. **`library.abs_path` and `rel_path`** (library.py:37-48) check containment for every stored file path, so DB-stored relative paths cannot escape the library. `import_file` is only called with library-internal sources and server-chosen names.
3. **Turnaround view names** from params are checked against a fixed dict in `graphs/qwen_edit.py:60-62` (`view_prompt` raises on an unknown view) before any `sc.dir / f"{view}_raw.png"` write. `refine_version` also checks `body.view` against `TURNAROUND_VIEWS` (main.py:551).
4. **Every file name the stages write** is server-chosen (`mask.png`, `image.png`, `rig.glb`, `<slug>_<id8>.fbx`), and the Blender and Unity asset name comes from the alphanumeric-only `slug()` in `stages/base.py:29-31`.
5. **No subprocess uses a shell with user data.**
   - `tools.py:23` uses `subprocess.Popen(cmd_list)`; Blender and Unity commands are argv lists (tools.py:48, 63-68).
   - Electron `spawn` calls pass argv arrays (`services.js:223, 232, 240`; `procs.js:10`; `taskkill` at `procs.js:61`).
   - The PowerShell `-Command` string in `procs.js` is a constant.
   - The `cmd.exe /c` call at `services.js:276` runs `buildCommand` from the launcher's own config, not from the web UI.
6. **Subprocess timeouts** are set: Blender and Unity 3600 s by default, 1800 s for helpers, ComfyUI jobs `job_timeout_seconds` (3600). Cancelling kills registered processes.
7. **Network.**
   - `run.ps1` and `run.sh` bind to `127.0.0.1`, and ComfyUI B is started with `--listen 127.0.0.1` (start-comfy-b.ps1:6).
   - The ComfyUI client only talks to the configured instance URLs.
   - No code in `backend/vgac`, `frontend/src` or `desktop/` contacts the public internet. There is no telemetry, no model download and no CDN, and `index.html` loads only local assets.
8. **CORS** uses `allow_origin_regex` with Starlette's `fullmatch`. A preflight from `http://localhost.evil.example` was refused (400) and `http://localhost:5173` was allowed. JSON-body endpoints cannot be forged cross-site, because FastAPI 0.162's strict content-type check returned 422 for both a missing Content-Type and `text/plain`.
9. **Deserialization.** YAML is loaded with `yaml.safe_load` everywhere (config.py:55, reference.py:93, 193). No `pickle`, `eval`, `exec`, `yaml.load` or `shell=True` exists in the backend. JSON from ComfyUI and Blender is parsed with `json.loads` and read with `.get()`.
10. **SQL** goes through the SQLAlchemy ORM with bound parameters. Delete endpoints look the row up first (`_project`, `_asset`), so `remove_tree` only ever gets server-generated IDs.
11. **Event bus fan-out** is bounded: each subscriber has a 500-item queue that drops the oldest event when full (events.py:17, 39-43), so a slow client cannot block workers. `GET /api/jobs` caps `limit` at 500, and `RunIn.count` is capped at 4.
12. **Electron.**
    - Both windows use `contextIsolation: true`, `nodeIntegration: false` and `sandbox: true`.
    - `webSecurity` is not disabled, and `allowRunningInsecureContent` is not set.
    - The main window has no preload.
    - The status page has a strict CSP (`default-src 'self'; script-src 'self'`) and builds its DOM with `textContent` only.
    - `setWindowOpenHandler` denies every new window and passes only `http(s)` URLs to the OS browser.
13. **The Vite dev proxy** targets `127.0.0.1:8765` only. Recent Vite versions validate the Host header on the dev server.
14. **Secrets.** No credentials of any kind are in the tracked tree or the history (6 commits). The `.gitignore` covers `.env`, the venv, `node_modules`, `library/`, `exports/`, `runtime/*`, the Unity generated folders and `desktop/dist/`.

## Prioritised fix list

1. **F2 + F1 + F4 together** (S, about 20 lines in main.py): add `TrustedHostMiddleware` for `127.0.0.1` and `localhost`, check the Origin header on the WebSocket, and refuse non-GET requests with a foreign Origin. This closes the only realistic remote route (a web page in the user's browser) and makes F3, F5, F6 and F7 local-only. Add three tests: a foreign Host, a foreign-origin WebSocket and a foreign-origin POST.
2. **F3** (S): drop `.` from the export `_slug` and check that `dest` resolves under the export root. Add a `_slug("..")` test.
3. **F6 + F7** (M): enforce `STAGE_SCHEMAS` on the server for every params entry point, add `sprite_frames` to the schema, cap `mask_png` and decode it strictly, and optionally cap the queued jobs per instance.
4. **F5** (S): validate `unity_project_path` as an absolute Unity project folder when it is saved, and refresh a stale `VgacImport.cs`.
5. **F12** (S): add `--host 127.0.0.1` to `restart.ps1` and `desktop/config.json`.
6. **F8** (S): confine `_locate_output` to the instance's `output_dir`.
7. **F9** (S): add a `will-navigate` guard, a permission handler, an IPC sender check and a CSP header for the built UI.
8. **F10, F11, F13** (S each): fix the mock prefix check, move personal paths into gitignored config with committed examples, split dev requirements, and run `pip-audit` and `npm audit`.
