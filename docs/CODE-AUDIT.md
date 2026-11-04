# Code audit: cleanliness and efficiency

## Scope

This is a read-only review of the Video Game Asset Creator repo as it stood on 2026-11-04, around 11:00 to 12:00 local time. It covers:

- the backend in `backend/vgac`: `main.py`, `db.py`, `jobs.py`, `events.py`, `comfy.py`, `library.py`, `reference.py`, `guidance.py`, `quality.py`, `imaging.py`, `tools.py`, `autopilot.py`, `config.py`, `stages/*`, `graphs/concept.py` and `blender/*`
- the frontend in `frontend/src`: `api.ts`, `events.ts`, `help.ts`, `refdata.ts`, `glbSnapshot.ts`, `Viewer.tsx`, `HealthPanel.tsx`, `App.tsx`, `pages/*`, `pipeline/StagePanel.tsx` and `components/*`
- the desktop launcher (`desktop/services.js`, `desktop/config.json`)
- the Unity importer (`unity-sandbox/Assets/Editor/VgacImport.cs`, report paths only)
- `tools/e2e.py`, and `bakeoff/` (checked only for duplication)

Ruff ran read-only with rules F, B, BLE, S110 and T20. Two small scratch scripts confirmed behaviour; neither touched the repo.

Other work was editing `main.py`, `export.py`, `api.ts`, `help.ts`, `StagePanel.tsx` and `ProjectPage.tsx` during the review. The feedback and gallery endpoints, the feedback UI, a host and origin guard in `main.py` and a shared `slug` in `export.py` were added while it ran. A duplicate-slug finding was dropped because that change resolved it. Line numbers were re-checked just before writing. In those six files they may already have moved by a few lines, so the quoted code is the reliable anchor. There are now 40 backend tests (test_api 15, test_graphs 10, test_quality_imaging 7, test_reference 6, test_db 2).

## Health summary: what is good

- Every backend module has a docstring that says what it does and where its rules come from (decision ids, ARCHITECTURE.md sections, bakeoff origins).
- Jobs live in the database, not in memory. Rows left "running" by a crash go back to the queue at start (`jobs.py` `_requeue_orphans`). Each instance gets exactly one worker. The RAM guard runs before dispatch.
- Blender and Unity run in `asyncio.to_thread`, and their processes are registered so a cancel can kill them (`stages/base.py` `blender`, `unity`; `tools.py`).
- Each websocket subscriber has a bounded queue that drops the oldest events, so a slow tab never blocks a worker (`events.py`).
- `/api/library/{path}` guards against path traversal (backslashes, NUL, resolve plus `relative_to`), and a test covers it.
- Reference data (categories, styles, platforms, models, presets) lives in YAML and is cached in one place (`reference.py`).
- Tests use a temporary library and unroutable ComfyUI ports, so they can never touch live data or the GPUs (`tests/conftest.py`).
- The frontend has no `any`, no leftover `console.log` (only `console.error` and `console.warn` on real failures), and no emoji.
- The frontend shares one offscreen WebGL renderer and caches GLB snapshots. `Viewer.tsx` disposes its renderer and controls.
- Accessibility basics hold in the files read. The icon-only "i" button has an `aria-label`, candidate cards have `aria-label` and `aria-pressed`, and selects without a visible label have `aria-label`.
- The stage modules mostly record failures in metrics (`audit_error`, `rig_info_error`, `unity_error`) instead of hiding them.

## Findings

| ID | Category | Impact | File:line | Title |
|----|----------|--------|-----------|-------|
| B1 | Bug | High | backend/vgac/jobs.py:343-349 | `Version.job_id` is always null because it is read before the job row is flushed |
| B2 | Bug / race | High | backend/vgac/jobs.py:287-301, main.py:630-639 | Rejecting a queued or running version is undone when the job finishes |
| B3 | Bug / threading | Medium | backend/vgac/main.py:658 | Feedback endpoint calls `bus.publish` from a worker thread |
| B4 | Bug | Medium | backend/vgac/stages/export.py:342-345 | Unity project import can report the sandbox import's stale result as its own |
| B5 | Correctness smell | Medium | backend/vgac/stages/turnaround.py:153-163, 173-177 | Turnaround refine and adjust commit a temporary wrong parent to the database |
| B6 | Bug | Medium | backend/vgac/autopilot.py:22, main.py:43-54 | Autopilot "running" state survives a restart with no task behind it |
| B7 | Bug | Medium | backend/vgac/jobs.py:149-157, 260-261 | Graceful shutdown records the running job as cancelled, and the worker can start the next job |
| B8 | Bug (UI) | Medium | frontend/src/pipeline/StagePanel.tsx:700-709 | Fine-tune form throws away the user's edits on every asset reload |
| B9 | Correctness | Low | backend/vgac/guidance.py:89, 112-114 | Check guide key "height" never matches "height_m"; texture help sentence is wrong for most texture checks |
| B10 | Bug | Low | backend/vgac/stages/export.py:84-86 | Asking for no LODs builds three, then fails the LOD gate |
| B11 | Correctness smell | Low | backend/vgac/main.py:528-538 | Parent walk-back skips any missing stage and names the wrong stage in its error |
| B12 | Bug (UI) | Low | frontend/src/components/ui.tsx:288-313 | `useAsync` shares one `alive` ref across runs, so a stale response can win |
| P1 | Efficiency | High | backend/vgac/stages/base.py:153-154, jobs.py:234-251, frontend/src/pages/AssetPage.tsx:148 | Once-a-second progress messages cause a DB commit, a log line, a websocket event and a full asset refetch |
| P2 | Efficiency | Medium | backend/vgac/jobs.py:128-131, 234-251; autopilot.py:98-105; stages/material.py:57-63 | Synchronous SQLite and CPU-heavy image work run on the event loop |
| P3 | Efficiency | Medium | backend/vgac/main.py:402, 693-731; db.py:309; frontend/src/pages/ProjectPage.tsx:24-30 | N+1 queries on the asset list and gallery, plus undebounced full reloads |
| P4 | Efficiency | Medium | frontend/src/pages/AssetPage.tsx:72-81; backend/vgac/autopilot.py:98-105 | Polling for autopilot state while the websocket already pushes it |
| P5 | Efficiency | Low | backend/vgac/comfy.py:61-62, 137; main.py:274; desktop/services.js:83-88 | A new HTTP client per ComfyUI request; `/api/health` is heavy and polled every 2 s |
| P6 | Efficiency | Low | backend/vgac/stages/base.py:183-185, turnaround.py:83-104, imaging.py:65-73 | Each cutout PNG is decoded four or five times |
| P7 | Efficiency / correctness | Low | backend/vgac/jobs.py:396-403 | `queue.status()` runs two queries per worker, one of them only to count, with no ordering |
| D1 | Duplication | Medium | backend/vgac/main.py:37, reference.py:25, graphs/concept.py:18,123,145, guidance.py:118; frontend/src/api.ts:18, help.ts:110 | The concept model list is defined in eight places |
| D2 | Duplication | Medium | backend/vgac/db.py:21, reference.py:16-24, stages/__init__.py:20, jobs.py:35, guidance.py:9; frontend/src/api.ts:3-13, help.ts:23-63 | Stage order, labels and time estimates are defined seven times, with different numbers |
| D3 | Duplication | Medium | frontend/src/help.ts:449-465, 547-569; backend/vgac/guidance.py:83-115, 128-162; stages/turnaround.py:36-51 | Check severity, fixes and suggestions are written twice, with different rules |
| D5 | Duplication | Low | backend/vgac/db.py:27,254; autopilot.py:27-28; stages/export.py:218; stages/material.py:102 | Four timestamp helpers in two output formats |
| D6 | Duplication | Low | backend/vgac/stages/*.py (12 places), mesh.py:156, rig.py:188 | Copied "merge parent params" boilerplate and pass-through wrappers |
| D7 | Duplication | Low | backend/vgac/stages/export.py:112-122, 334-340 | Unity argument building written twice |
| D8 | Duplication | Low | backend/vgac/blender/*.py (8 scripts); bakeoff/procanim.py, bakeoff/rig_info.py | `_import_glb` and argv parsing copied into eight Blender scripts; two bakeoff scripts forked |
| D9 | Duplication | Low | backend/vgac/stages/base.py:91-133; export.py:188-201; material.py:92-106 | Lineage walk written three times; manifest model and seed loop written twice |
| H1 | Layering | Medium | backend/vgac/autopilot.py:109-181 | Autopilot drives the pipeline through `main.py`'s private helpers |
| H2 | Hygiene | Low | backend/vgac/stages/export.py:16, 209, 271-275, 304; events.py:21,35-37; reference.py:191; turnaround.py:156 | Dead code, unused and redundant imports |
| H3 | Config | Low | backend/vgac/main.py:679, stages/judge.py:40-41, stages/base.py:177, jobs.py:35-36, mesh.py:122 | Instance names, timeouts and thresholds hardcoded outside config.yaml |
| H4 | Consistency | Low | backend/vgac/main.py:109-126, 390, 508; jobs.py:374 | Input validation and response shapes differ between similar endpoints |
| H5 | Hygiene | Low | backend/vgac/main.py:463-464, comfy.py:242-243, jobs.py:182-183, blender/prep.py:144 | Exceptions swallowed with no log |
| H6 | Types | Low | frontend/src/api.ts:71, 154, 203, 313, 333 | Frontend types lag the backend responses |
| H7 | Tests | Low | backend/tests/test_api.py | Public endpoints and paths with no test |
| U1 | Frontend structure | Medium | frontend/src/pipeline/StagePanel.tsx (968 lines) | Oversized components and prop drilling |
| U2 | Frontend state | Low | frontend/src/pages/AssetPage.tsx:26-41 | Autopilot state is kept in two places |
| U3 | Frontend copy | Low | frontend/src/pipeline/StagePanel.tsx:658, 803 | Strings that bypass the help registry |

Counts: 3 High, 14 Medium, 20 Low (37 findings).

---

## B1. `Version.job_id` is always null

File: `backend/vgac/jobs.py:343-349`

```python
            job = Job(instance=instance, kind=kind, asset_id=asset_id, version_id=version_id, status="queued",
                      payload_json=json.dumps(payload or {}))
            db.add(job)
            if version_id:
                v = db.get(Version, version_id)
                if v is not None:
                    v.job_id = job.id
```

**Why it matters.** `Job.id` gets its value from the column default (`default=new_id`, `db.py`), and that default runs only at flush time. At line 349, `job.id` is still `None`. A scratch script against `vgac.db` printed `job.id before flush: None` and `version.job_id after commit: None`. Everything that reads `Version.job_id` is therefore dead:

- `reject_version` (`main.py:633`, `if v.status in ("queued", "running") and v.job_id and queue is not None:`) never cancels the job.
- Autopilot reports `job_id=None` for in-flight versions (`autopilot.py:164`).
- The "job" link in StagePanel's Details never shows.
- `CandidateCard` silently falls back to matching on `version_id`.

**Fix.** Call `db.flush()` after `db.add(job)`, or construct the job as `Job(id=new_id(), ...)`. Add a test that `GET /api/versions/{id}` returns a non-null `job_id` after a run.

**Effort.** S.

**Safe.** This is a behaviour change, but it is the intended one: reject will start cancelling jobs. Do it together with B2.

## B2. A rejected version is overwritten when its job finishes

Files: `backend/vgac/main.py:633-635`, `backend/vgac/jobs.py:287-299`

```python
    if v.status in ("queued", "running") and v.job_id and queue is not None:
        queue.cancel(v.job_id)
    v.status = "rejected"
```

```python
                    if job.version_id:
                        v = db.get(Version, job.version_id)
                        if v is not None:
                            if is_judge:
                                ...
                            elif error is None:
                                v.status = "done"
                            else:
                                v.status = "error"
                                v.error = error
```

**Why it matters.** The request handler and the worker each have their own session. Because of B1, the job is never cancelled. It runs to the end, and the worker's `finally` block sets the version back to "done". Once B1 is fixed, cancelling a running job goes through the same `finally`, which then sets "error" with "cancelled". Either way the person's "Reject" does not stick. `_execute` also never checks whether the version was rejected before it starts.

**Fix.**

1. In the `finally` block of `_execute`, leave `v.status` alone when it is already "rejected", and still record the files.
2. In `_execute`, skip a job whose version is "rejected" before marking it running.
3. Add a test: reject a queued version, let the fake executor run, and assert the status is still "rejected".

**Effort.** S.

**Safe.** This is a behaviour change, and it fixes the bug.

## B3. Feedback endpoint publishes from a worker thread

File: `backend/vgac/main.py:647-658`

```python
@app.post("/api/versions/{vid}/feedback")
def feedback_version(vid: str, body: FeedbackIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    ...
    out = version_to_dict(v)
    bus.publish({"type": "version", "version": out})
```

**Why it matters.** A plain `def` endpoint runs in FastAPI's thread pool. `EventBus.publish` calls `asyncio.Queue.put_nowait` (`events.py:45`), and asyncio queues are not thread-safe. The waiting `ws_events` coroutine may not wake until some other event arrives, and two threads can race on the queue. `events.py`'s own docstring says "publish() is safe from the event loop; publish_threadsafe() from worker threads". This is the only `bus.publish(` call outside `events.py`.

**Fix.** Use `bus.publish_threadsafe(...)`.

**Effort.** S.

**Safe.** Yes.

## B4. Unity project import can report a stale result

File: `backend/vgac/stages/export.py:342-346`

```python
        try:
            await sc.unity("VgacImport.Run", args, flags, "unity_project_import.log", project_path=proj)
            rp = sc.dir / "unity_report.json"
            report = json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else {}
            sc.set_metrics(unity_project_import={"ok": bool(report.get("ok")), ...
```

**Why it matters.** `VgacImport.Run` writes `<input>/unity_report.json` (`VgacImport.cs:232`), and both the sandbox import (`_unity`, line 114) and the project import pass `-vgacInput sc.dir`. Suppose the project's Unity run exits 0 without reaching that write: the project fails to compile, a licence prompt aborts the run, or the method is missing because `VgacImport.cs` was not copied. The code then reads the sandbox's earlier report and records `ok: True` for the user's own project. Even when the run succeeds, it overwrites the file the sandbox step registered as `files.unity_report`.

**Fix.** Pass a different report name (for example a `-vgacReport` argument, or delete `unity_report.json` before the second run) and read that file. The sandbox report should keep its own name.

**Effort.** S (backend) to M (also adding the C# argument).

**Safe.** Yes. Only the reported outcome changes.

## B5. Turnaround refine and adjust commit a temporary wrong parent

File: `backend/vgac/stages/turnaround.py:153-163`

```python
    grand = sc.db.get(type(parent), parent.parent_version_id) if parent.parent_version_id else None
    ...
    sc.version.parent_version_id = parent.id
    # _run_views reads the concept through sc.parent; temporarily point at the concept version.
    original_parent = sc.version.parent_version_id
    sc.version.parent_version_id = grand.id
    try:
        await _run_views(sc, [view], merged, keep=keep, extra=instruction)
    finally:
        sc.version.parent_version_id = original_parent
        sc.save()
```

**Why it matters.** `_run_views` calls `sc.register`, which calls `sc.save()` and so commits after every view. For the whole job, which takes minutes, the database row says this turnaround version's parent is the concept. Any `GET /api/assets/{id}` in that window returns a wrong lineage. If the process is killed (the desktop launcher uses `taskkill /F`), the wrong parent stays for good. Line 156 assigns a value that is already there. `adjust` (lines 173-177) does the same swap.

**Fix.** Give `_source_image` and `_run_views` an explicit `source: Version` argument, and stop mutating `parent_version_id`.

**Effort.** S.

**Safe.** Yes. Behaviour stays the same apart from the transient wrong value.

## B6. Autopilot state survives a restart with no task behind it

Files: `backend/vgac/autopilot.py:22`, `backend/vgac/main.py:43-54`

```python
_tasks: dict[str, asyncio.Task] = {}
```

**Why it matters.** The running autopilot task exists only in memory, while its state (`status: "running"`) is saved on `Asset.autopilot_json`. After a restart nothing resumes it or clears it, because `lifespan` only starts the job queue. The asset page then shows "Stop autopilot" and polls `GET .../autopilot` every 3 s forever (`AssetPage.tsx:72-81`), and nothing moves.

**Fix.** At startup, either resume `_run` for every asset whose state is "running", or set those to "paused" with a reason such as "The app restarted; press Make it to continue". Resuming is safe because `_run` re-evaluates from the database.

**Effort.** S.

**Safe.** This is a behaviour change, and it fixes the bug.

## B7. Graceful shutdown cancels the running job instead of re-queueing it

File: `backend/vgac/jobs.py:149-157` and `260-261`

```python
                self._job_task = asyncio.create_task(self._execute(job_id), name=f"vgac-job-{job_id[:8]}")
                try:
                    await self._job_task
                except asyncio.CancelledError:
                    me = asyncio.current_task()
                    if me is not None and me.cancelling():
                        raise
```

```python
        except (asyncio.CancelledError, JobCancelled):
            cancelled = True
            error = "cancelled"
```

**Why it matters.** The module docstring (line 7) promises that "a restart resumes queued jobs". On a graceful stop (Ctrl+C in `run.ps1`, which goes through the lifespan's `queue.stop()`), cancelling the worker task also cancels the awaited job task. `_execute` swallows the `CancelledError` and writes `status="cancelled"`, so the job is lost rather than re-queued. Because the inner task swallowed the cancel, the worker's `except` never runs. The loop goes on, picks the next queued job, and `stop()` waits for it to finish. A scratch script with the same structure on Python 3.13.7 printed "job 1 swallowed cancel, recorded as cancelled" and then "worker continued after job 1".

**Fix.**

1. In `_execute`, treat a `CancelledError` as a user cancel only when `ctx.cancelled` is set, since `cancel_current` sets it at line 178.
2. Otherwise set the job back to "queued" with the message "re-queued after restart", and re-raise.
3. In `_run`, check `asyncio.current_task().cancelling()` after the `await` as well as in the `except`.

**Effort.** M.

**Safe.** This is a behaviour change on shutdown only.

## B8. Fine-tune form throws away edits on every asset reload

File: `frontend/src/pipeline/StagePanel.tsx:700-709`

```tsx
  const initial = useMemo(() => {
    const out: Record<string, unknown> = {}
    for (const p of schema) {
      ...
    }
    return out
  }, [schema, target])
  const [values, setValues] = useState<Record<string, unknown>>(initial)
  useEffect(() => setValues(initial), [initial])
```

**Why it matters.** `target` is the active `Version` object. Every `reload()` in AssetPage replaces the asset with freshly parsed JSON, so `target` is a new object, `initial` is recomputed and `setValues(initial)` wipes whatever the person changed. While any job of the asset is running, AssetPage reloads about once a second (see P1). Editing a setting in Fine-tune while concept candidates are still being made is therefore close to impossible.

**Fix.** Key the reset on content, not identity, for example `useMemo(..., [schema, target.id, JSON.stringify(target.params)])`. The new `useFeedback` hook in the same file (line 470) already uses a `storedKey` for this. Alternatively, reset only when `target.id` changes.

**Effort.** S.

**Safe.** Yes.

## B9. Check guide misses "height_m", and the texture help sentence is wrong

Files: `backend/vgac/guidance.py:89, 112-114`; `backend/vgac/quality.py:47`

```python
    "height": {"severity": "warn", "help": "The model is not the size the category expects.", "fix": lambda c: {"stage": "prep", "params": {}}},
```

```python
        out.append(check("height_m", ok, round(h, 4), height_m))
```

```python
    for k in ("texture_", "lod"):
        if (c.get("name") or "").startswith(k) and "help" not in out:
            out["help"] = "A texture is not a power-of-two size." if k == "texture_" else ...
```

**Why it matters.** `quality` emits "height_m", so the "height" entry never matches, and the height check never gets its help sentence or its "fix" button. The prefix rule gives the power-of-two sentence to `texture_*_square` and `texture_*_budget` too. A texture over budget is then explained as a power-of-two problem.

**Fix.** Rename the key to "height_m". Map the help sentence by suffix (`_pow2`, `_square`, `_budget`).

**Effort.** S.

**Safe.** Yes. Only help text and the presence of a fix button change.

## B10. Asking for no LODs builds three, then fails the gate

Files: `backend/vgac/stages/export.py:76-86`; `backend/vgac/blender/pack.py:37`

```python
    lods = parse_lods(params.get("lods"))
    if lods is None:
        lods = list(sc.category.get("lod_ratios") or [0.5, 0.25])
    ...
    args: list[Any] = [src, sc.dir, name, "--lods", ",".join(str(x) for x in lods) if lods else "", ...]
    if not lods:
        args = [src, sc.dir, name, "--collider", collider, "--formats", ",".join(formats)]
```

```python
lods = [float(x) for x in opt("--lods", "0.5,0.25,0.1").split(",") if x.strip()]
```

**Why it matters.** `lods: "1.0"` parses to `[]` because the leading 1.0 is dropped. The override at line 85 then leaves out `--lods`, so `pack.py` falls back to its own default of three LODs. The judge compares against `params.lods == []` and fails `lod_count`, a blocking check. The defaults also disagree: `[0.5, 0.25]` in export and `0.5,0.25,0.1` in pack.py.

**Fix.** Always pass `--lods` (an empty string means none), and delete lines 85-86. Keep one default in `export.py`.

**Effort.** S.

**Safe.** This changes behaviour only for the "no LODs" request, which is broken today.

## B11. Parent walk-back skips any stage and names the wrong one

File: `backend/vgac/main.py:528-538`

```python
            idx = STAGES.index(stage) - 1
            prev = STAGES[idx]
            parent = None
            while idx >= 0 and parent is None:
                prev = STAGES[idx]
                parent = _selected(db, a.id, prev)
                idx -= 1
            if parent is None:
                raise HTTPException(409, f"no selected {prev} version to build on")
```

**Why it matters.** The comment gives the material category as the reason for the walk-back, but it applies to every category. A humanoid export with no rig kept quietly builds from prep. When nothing is found, `prev` has walked all the way to "concept", so the 409 message names the wrong stage. Lines 531-532 are redundant. The frontend's `previousStage` (`api.ts:403`) is strictly linear, so the UI and the API disagree.

**Fix.** Walk back only over stages the category skips (put a `skips: [turnaround]` list in the category YAML), and report the stage immediately before.

**Effort.** S.

**Safe.** Mostly. It could reject a skip that some script relies on today.

## B12. `useAsync` can apply a stale response

File: `frontend/src/components/ui.tsx:288-313`

```tsx
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    ...
    return () => {
      alive.current = false
    }
  }, [...deps, tick])
```

**Why it matters.** A single ref is shared across runs. When the deps or `tick` change, the cleanup sets it false and the next run sets it true straight away. A slower earlier promise then sees `alive.current === true` and overwrites newer data. ProjectPage's `reload()` can fire several times in a row (see P3), which makes this reachable.

**Fix.** Use a local `let alive = true` inside the effect, the way the other hooks in the codebase do (`refdata.ts:28`, `glbSnapshot.ts`).

**Effort.** S.

**Safe.** Yes.

## P1. Once-a-second progress messages cause a full asset refetch

Files: `backend/vgac/stages/base.py:153-158`; `backend/vgac/jobs.py:234-251`; `frontend/src/pages/AssetPage.tsx:148`

```python
        def on_poll(elapsed: float) -> None:
            self.ctx.progress(None, f"{message or 'running'} on instance {client.name}: {int(elapsed)} s")
```

```python
                if message and message != last["message"]:
                    j.message = message
                    ...
                if changed:
                    db2.commit()
                    log.info("job %s: %.0f%% %s", job_id[:8], j.progress * 100, j.message)
                    _publish_job(j)
```

```tsx
        if (ev.job.status === 'done' || ev.job.status === 'error' || ev.job.status === 'cancelled' || ev.job.status === 'running') reloadSoon()
```

**Why it matters.** The elapsed-seconds message changes on every ComfyUI poll: every 1 s for concept and turnaround, every 5 s for mesh and rig. Each change opens a session, commits to SQLite on the event loop, writes an INFO log line and sends a websocket event. On the asset page every "running" job event triggers `GET /api/assets/{id}`, which serialises every version with its params, files and metrics JSON. For a long job that adds up to thousands of commits, log lines and full-asset fetches, with nothing new in them except a counter.

**Fix.**

1. Backend: throttle message-only updates, for example to at most one every 5 s or only when the text without the counter changes. Keep a fraction change of at least 0.5 percent as it is.
2. Frontend: update the job in `jobs` state from the event, as it already does, and reload the asset only on a status transition. Track the previous status per job id.

**Effort.** M.

**Safe.** Yes. The progress bar and status still update.

## P2. Synchronous database and image work on the event loop

Files: `backend/vgac/jobs.py:128-131` (`_next_queued`) and `234-251` (`progress`); `backend/vgac/autopilot.py:98-105`; `backend/vgac/stages/material.py:57-63`; `backend/vgac/stages/mesh.py:97-98`; `backend/vgac/main.py:272, 292, 663`

```python
    def _next_queued(self) -> str | None:
        with session_factory()() as db:
            job = db.scalar(select(Job).where(Job.instance == self.name, Job.status == "queued")...
```

```python
    base = make_seamless(src, sc.dir / "base_color.png", power=power)
    sc.progress(0.5, "derived maps")
    maps = derive_maps(base, sc.dir)
```

**Why it matters.** Everything the workers do outside Blender and Unity runs on the server's only event loop:

- each worker's DB poll every 2 s
- every progress commit
- autopilot's DB poll every 3 s
- every `sc.save()`
- PIL and numpy work: seamless blend and map derivation on textures up to 2048 px, 24 silhouettes plus `best_alignment`, contact sheets, `alpha_bbox` and histograms

While that work runs, API requests and websocket sends wait. The `async def` endpoints `health`, `judge_version`, `start_autopilot` and `stop_autopilot` use the synchronous `Session` on the loop too. `judge_metrics` re-opens PNGs to read their size. SQLite writes are short, so this is noticeable rather than broken.

**Fix.** Run CPU-bound image functions with `await asyncio.to_thread(...)`; the stage helpers can wrap `make_seamless`, `derive_maps`, `contact_sheet` and the silhouette loop. Make `judge_version` (metrics mode) and `health` call their synchronous parts through `to_thread`, or turn them into plain `def` (autopilot needs the loop only for `create_task`).

**Effort.** M.

**Safe.** Yes.

## P3. N+1 queries on the asset list and gallery, plus undebounced reloads

Files: `backend/vgac/main.py:402`; `backend/vgac/db.py:309`; `backend/vgac/main.py:701-718`; `frontend/src/pages/ProjectPage.tsx:24-30`

```python
    return [asset_to_dict(a, with_stages=True) for a in rows]
```

```python
        for v in sorted(a.versions, key=lambda x: (x.created_at, x.id)):
```

```python
        a = db.get(Asset, ex.asset_id)
        ...
        kept = _selected(db, a.id, "export")
        ...
        pr = db.get(Project, a.project_id)
        concept = _selected(db, a.id, "concept")
        ...
            mesh = _selected(db, a.id, "prep") or _selected(db, a.id, "mesh")
```

```tsx
      if (ev.type === 'version' && assets?.some((a) => a.id === ev.version.asset_id)) reload()
      if (ev.type === 'job' && (ev.job.status === 'done' || ev.job.status === 'error') && assets?.some((a) => a.id === ev.job.asset_id)) reload()
```

**Why it matters.**

- `list_assets` lazy-loads `a.versions` once per asset.
- `gallery` runs up to six queries per exported asset.
- ProjectPage refetches every asset with every version on each version event and each finished job. A single job end sends a job event and a version event, so it reloads twice, with no debounce.
- The full tree goes over the wire for a grid that needs only a thumbnail and a stage state per asset.

**Fix.**

1. Add `.options(selectinload(Asset.versions))` in `list_assets`.
2. In `gallery`, preload the selected versions of all assets in one query, keyed by `(asset_id, stage)`, and the projects in one query.
3. In ProjectPage, debounce the reload, as AssetPage's `reloadSoon` does.
4. Later, add a slim summary shape: selected file paths and a status per stage.

**Effort.** S for the selectinload and the debounce; M for the summary shape.

**Safe.** Yes.

## P4. Autopilot state is polled while the websocket already pushes it

Files: `frontend/src/pages/AssetPage.tsx:72-81`; `backend/vgac/autopilot.py:98-105`

```tsx
  useEffect(() => {
    if (!apRunning) return
    const t = window.setInterval(() => {
      api.getAutopilot(aid).then(...)
    }, 3000)
```

```python
async def _wait(version_ids: list[str]) -> None:
    while True:
        await asyncio.sleep(POLL_SECONDS)
        with session_factory()() as db:
            vs = [db.get(Version, vid) for vid in version_ids]
```

**Why it matters.** The comment says the frontend poll "covers the polling fallback", but it runs even when `useEventMode()` is 'ws'. Every `_set` already publishes an `autopilot` event. On the backend, autopilot polls the version table every 3 s, even though the jobs layer publishes every version transition on the bus. Each poll runs one `db.get` per version.

**Fix.**

1. Frontend: poll only when `events.mode !== 'ws'`.
2. Backend: have `_wait` wait on an `asyncio.Event` that a small bus subscriber sets when a version in `version_ids` reaches a final state. Keep a slow fallback poll of about 30 s.

**Effort.** S for the frontend, M for the backend.

**Safe.** Yes.

## P5. A new HTTP client per ComfyUI request; health is heavy and polled often

Files: `backend/vgac/comfy.py:61-62, 137`; `backend/vgac/main.py:274`; `desktop/services.js:83-88`; `desktop/config.json` (`"pollIntervalMs": 2000`)

```python
    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout or self.timeout)
```

```python
    clients = {name: ComfyClient(inst.url, name=name) for name, inst in settings.instances.items()}
```

**Why it matters.**

- Every `/history` poll (once a second per running job) opens and closes a new connection pool.
- `/api/health` builds new clients, calls both ComfyUI instances and runs two DB queries per worker (P7). The desktop launcher calls it every 2 s and at the same moment probes both ComfyUIs directly, so that is four `/system_stats` calls every 2 s.
- `HealthBadge` adds one call every 10 s and the Health page one every 5 s.

**Fix.**

1. Give `ComfyClient` one long-lived `httpx.AsyncClient`, created lazily and closed in `JobQueue.stop`, with per-request timeouts.
2. Make `health` reuse `queue.clients`.
3. Give the launcher a cheap `/api/ping`, or cache the health result for 2 s.

**Effort.** S to M.

**Safe.** Yes.

## P6. Each cutout PNG is decoded four or five times

Files: `backend/vgac/stages/base.py:183-185`; `backend/vgac/stages/turnaround.py:83-104`; `backend/vgac/imaging.py:65-73`

```python
        white = white_composite(dest)
        self.use_model("birefnet")
        return {"cutout": dest, "white": white, "stats": alpha_bbox(dest)}
```

```python
            stats = alpha_bbox(files["cutout"])
```

```python
    a = np.array(im.getchannel("A")) > 127
    ...
            "image_size": [w, h], "empty": False, "alpha_corner": int(np.array(im.getchannel("A"))[2, 2]),
            "alpha_centre": int(np.array(im.getchannel("A"))[h // 2, w // 2])}
```

**Why it matters.** For each turnaround view and each re-roll, the same PNG is decoded by:

1. `white_composite`
2. `alpha_bbox` in `cutout`
3. `alpha_bbox` again in `_run_views`, because `_generate_view` drops `res["stats"]`
4. `colour_histogram`
5. `silhouette` through `mirror_iou`, which also decodes the opposite view

`alpha_bbox` also extracts the alpha channel three times. Material does the same with `base_color` (`make_seamless`, `derive_maps`, `seam_error`, `image_size`, and then `texture_checks`).

**Fix.** Return `stats` from `_generate_view` and reuse it. Have `alpha_bbox` extract alpha once. Optionally add one helper that loads the RGBA array once and computes bbox, histogram and silhouette from it.

**Effort.** S.

**Safe.** Yes.

## P7. `queue.status()` queries twice per worker and does not order

File: `backend/vgac/jobs.py:396-403`

```python
                queued = db.scalar(select(Job).where(Job.instance == name, Job.status == "queued"))
                n = len(db.scalars(select(Job.id).where(Job.instance == name, Job.status == "queued")).all())
```

**Why it matters.** Every job id is loaded only so it can be counted. `next_job_id` has no `order_by`, while the worker picks `order_by(Job.created_at, Job.id)` (line 130). So the "next job" shown in Health is not guaranteed to be the one that runs next.

**Fix.** Use `select(func.count()).where(...)` for the count and the same ordering as `_next_queued` for the next job. Better still, one grouped query for all instances.

**Effort.** S.

**Safe.** Yes.

## D1. The concept model list is defined in eight places

| File:line | Code |
|---|---|
| `backend/vgac/main.py:37` | `STYLE_MODELS = ("zturbo", "zbase", "klein", "anima", "pixel", "chroma")` |
| `backend/vgac/reference.py:25` | `CONCEPT_MODELS = ("zturbo", "zbase", "klein", "anima", "pixel", "chroma")` |
| `backend/vgac/graphs/concept.py:18` | `DEFAULT_STEPS = {"zturbo": 8, ...}` |
| `backend/vgac/graphs/concept.py:123` | `BUILDERS = {...}` |
| `backend/vgac/graphs/concept.py:145` | `save_node` (a literal dict) |
| `backend/vgac/guidance.py:118` | `MODEL_LABELS` |
| `backend/vgac/data/models.yaml` | the six model entries |
| `frontend/src/api.ts:18` | `STYLE_MODELS` |
| `frontend/src/help.ts:110` | `MODEL_HELP` |

**Why it matters.** Adding a model means editing all of these. If one is missed, the model is either rejected by validation or crashes in `save_node` with a `KeyError`.

**Fix.**

1. Make `graphs/concept.py` the single source: one dict per model holding builder, save node and default steps.
2. Derive `CONCEPT_MODELS` from its keys and import that in `main.py`.
3. Put the labels in `models.yaml`.
4. The frontend already receives the options through `/api/stages` `adjust_schema` (`options=list(CONCEPT_MODELS)`). Use those, plus the labels, in place of `STYLE_MODELS` and `MODEL_HELP`.

**Effort.** M.

**Safe.** Yes, if done one module at a time with the existing graph tests.

## D2. Stage order, labels and estimates are defined seven times

- `backend/vgac/db.py:21`: `STAGES = (...)`
- `backend/vgac/reference.py:16-24`: `STAGE_ORDER` and `STAGE_LABELS` ("Preview turnaround")
- `backend/vgac/stages/__init__.py:20`: `STAGE_MODULES = {s: ... for s in ("concept", "turnaround", ...)}`
- `backend/vgac/jobs.py:35-36`: `STAGE_INSTANCE`, `MATERIAL_INSTANCE`
- `backend/vgac/guidance.py:9-46`: `STAGE_GUIDE`, estimates 25, 300, 180, 10, 60, 120 s
- `frontend/src/api.ts:3-13`: `STAGES` and `STAGE_LABELS` ("Turnaround")
- `frontend/src/help.ts:23-63`: `STAGE_TEXT`, estimates 30, 120, 240, 90, 120, 90 s

**Why it matters.** The two sets of time estimates disagree by up to a factor of nine; prep is 10 s on the backend and 90 s on the frontend. Which one shows depends on whether `/api/stages` has loaded. The two copies of the stage help also say different things. A stage rename or insertion touches seven files.

**Fix.**

1. Make `reference.STAGE_ORDER` the one backend tuple, and import it in `db`, `stages` and `jobs`.
2. Have the frontend take order, labels, help and estimates from `/api/stages`, which already returns `plain_name`, `help` and `estimate_seconds`.
3. Keep `help.ts` `STAGE_TEXT` only for the gerund and as the offline or mock fallback, with the backend's numbers.

**Effort.** M.

**Safe.** Yes on the backend; on the frontend, check the copy visually.

## D3. Check severity, fixes and suggestions are written twice

Files: `frontend/src/help.ts:449-465`; `backend/vgac/guidance.py:83-115`; `frontend/src/help.ts:547-569` and `backend/vgac/guidance.py:128-162`; `backend/vgac/stages/turnaround.py:36-51`

```ts
export function checkSeverity(check: Check): 'block' | 'warn' {
  if (check.severity === 'warn' || check.severity === 'block') return check.severity
  ...
  return 'block'
}
...
  if (id === 'triangle_budget' && limit) return { stage: 'mesh', params: { target_faces: limit } }
```

```python
    "triangle_budget": {"severity": "block", ...
                        "fix": lambda c: {"stage": "prep", "params": {}}},
    ...
    else:
        out.setdefault("severity", "warn")
```

```python
    checks.append({"id": "coverage", "ok": stats["coverage"] >= LIMITS["min_coverage"], "value": stats["coverage"], "limit": LIMITS["min_coverage"]})
```

**Why it matters.**

- The default severity is "warn" on the backend and "block" on the frontend.
- The triangle-budget fix goes to prep on the backend and to mesh on the frontend.
- The turnaround view checks are built by hand rather than with `quality.check`, so they skip `describe_check` (no help, no severity) and rely on the frontend's regex.
- `suggestFallback` re-implements `guidance.suggest` with different rules: zbase concept 90 s, high-platform mesh x1.5 against the backend's 600 s, and a hardcoded category regex against the backend's `rig_engine` check. Autopilot pauses on backend severity, so the two can disagree on whether something blocks.

**Fix.** Build turnaround checks with `quality.check`, and add their ids to `CHECK_GUIDE`. Reduce `checkSeverity` and `checkFix` to "use the backend's field, else warn and no fix". Keep `suggestFallback` for mock mode only, and say so in a comment.

**Effort.** M.

**Safe.** This is a behaviour change. The severity default on unknown checks moves to the backend's rule.

## D5. Four timestamp helpers in two formats

Files: `backend/vgac/db.py:27, 254-259`; `backend/vgac/autopilot.py:27-28`; `backend/vgac/stages/export.py:218`; `backend/vgac/stages/material.py:102`

```python
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```

**Why it matters.** The API's timestamps end in "Z" (`db._iso`), while `autopilot.updated_at`, `started_at` and the manifests' `created_at` end in "+00:00". Both parse, but the frontend compares `autopilot.updated_at` as a string for the dismissed-card key, and the two forms sort differently.

**Fix.** Add a public `db.iso_now()` that returns `_iso(utcnow())`, and use it in all four places.

**Effort.** S.

**Safe.** Yes. Manifests get "Z" instead of "+00:00".

## D6. Copied parent-params boilerplate and pass-through wrappers

Files: 12 occurrences of `merged = {**(parent.params if parent else {}), **params}` across `stages/concept.py`, `turnaround.py`, `mesh.py`, `prep.py`, `rig.py`, `export.py` and `material.py`; `backend/vgac/stages/mesh.py:156-158`; `backend/vgac/stages/rig.py:188-191`

```python
async def _rerun_from_mesh_parent(sc: StageContext, merged: dict[str, Any]) -> None:
    # Inputs come from the lineage above the mesh parent; lineage_file walks through it, so nothing to swap.
    await run(sc, merged)
```

**Why it matters.** Every stage spells out the same refine and adjust merge in its own words. The wrappers add a hop without doing anything.

**Fix.** Add `StageContext.merged_params(params, **extra)` and a default `refine` and `adjust` in `stages/__init__.py` for modules that do not define one. Delete the two wrappers.

**Effort.** S.

**Safe.** Yes.

## D7. Unity argument building written twice

File: `backend/vgac/stages/export.py:112-122` and `334-340`

```python
        humanoid = _is_humanoid(sc)
        flags = ["-nographics"] + (["-vgacHumanoid"] if humanoid else [])
        args: dict[str, Any] = {"-vgacInput": sc.dir, "-vgacName": name}
        clips_lib = params.get("clips_library", "ual1")
```

**Why it matters.** The sandbox import and the project import must stay identical, but they already differ: the sandbox path records `use_model(clips_lib)` and a `clips_warning`, and the project path does neither.

**Fix.** Write one `_unity_args(sc, params, name) -> (args, flags, warnings)` and use it in both places.

**Effort.** S.

**Safe.** Yes.

## D8. Helpers copied into eight Blender scripts; two bakeoff scripts forked

Files: `backend/vgac/blender/{pack,prep,procanim,render_anim,render_views,rig_fix,rig_info,sprites}.py`; `bakeoff/procanim.py`, `bakeoff/rig_info.py`

```python
def _import_glb(path):
    """glTF import without the bone display-shape helper object the importer otherwise adds for skins."""
    try:
        bpy.ops.import_scene.gltf(filepath=path, bone_shape_type="NONE")
    except TypeError:
        bpy.ops.import_scene.gltf(filepath=path)
```

**Why it matters.** The same function, plus the `argv = sys.argv[sys.argv.index("--") + 1:]` and `opt()` pattern, is pasted into all eight scripts. A Blender API change has to be fixed eight times. `backend/vgac/blender/procanim.py` shares 179 of the 225 lines in `bakeoff/procanim.py`, and `rig_info.py` shares 57 of 89. They are forks rather than imports, which is fine for a frozen bakeoff record, but the bakeoff copies are now stale.

**Fix.** Add `backend/vgac/blender/_common.py` with `import_glb`, `argv()` and `opt()`. In each script, start with `sys.path.insert(0, os.path.dirname(__file__))` and import from it. Mark the bakeoff copies as historical in `bakeoff/BAKEOFF.md`.

**Effort.** M.

**Safe.** Yes. Run each script once in Blender after the change.

## D9. Lineage walk written three times; manifest loop written twice

Files: `backend/vgac/stages/base.py:91-133`; `backend/vgac/stages/export.py:188-201`; `backend/vgac/stages/material.py:92-106`

```python
        v = self.parent
        seen = set()
        while v is not None and v.id not in seen:
            ...
            seen.add(v.id)
            v = self.db.get(Version, v.parent_version_id) if v.parent_version_id else None
```

**Why it matters.** `stage_version`, `lineage_file` and `lineage` each walk the parent chain with their own loop. `export.build_manifest` and `material.export_run` both call `sc.lineage()` and then run `db.get` again on every entry to collect models and seeds. Material calls `sc.lineage()` twice (lines 92 and 106).

**Fix.** Add one `iter_lineage()` generator on `StageContext` that yields `Version` objects, and one `manifest_provenance(sc) -> (models, seeds, lineage)` shared by both exports.

**Effort.** S.

**Safe.** Yes.

## H1. Autopilot drives the pipeline through `main.py`'s private helpers

File: `backend/vgac/autopilot.py:109-181`

```python
    from . import main as m  # late import: main imports this module
    ...
                    kept = m._selected(db, aid, fstage)
                    ...
                        v = m._new_version(db, a, fstage, kept, {**kept.params, **(fix.get("params") or {})})
                        job = m._submit(db, a, v, "adjust", {"params": fix.get("params") or {}})
                    ...
                        pairs = m._run_stage_internal(db, a, fstage, count=1, params=fix.get("params") or {})
```

**Why it matters.**

- Pipeline rules (choosing a parent, auto-selecting the first version, submitting) live in the HTTP module, and a background task reaches into it through underscore names and a circular import.
- `_run_stage_internal` raises `HTTPException`, which autopilot catches as a bare `Exception` (line 182).
- `main.py` mixes middleware, schemas, helpers, serialisation and 36 routes in 832 lines.

**Fix.** Move `_selected`, `_select`, `_new_version`, `_submit` and `_run_stage_internal` into `vgac/pipeline.py`. Have them raise domain errors such as `NotFound`, `Conflict` and `BadRequest`, and map those to HTTP in one exception handler in `main.py`. Optionally split the routes into `APIRouter` modules (`routes/projects.py`, `assets.py`, `versions.py`, `jobs.py`).

**Effort.** M.

**Safe.** Yes. Behaviour is unchanged if done by moving code; the existing API tests cover it.

## H2. Dead code, unused and redundant imports

- `backend/vgac/stages/export.py:16`: `from .. import library, quality, reference`. Ruff F401: `reference` is unused.
- `backend/vgac/stages/export.py:271-275`: `if sc.asset.category == "material": ... await material.export_run(...)` cannot run. `stages.module_for` already sends material exports to `stages/material.py` (`stages/__init__.py:21,27`).
- `backend/vgac/stages/export.py:304`: `import shutil` inside `_deliver` repeats the top-level import at line 11. `from ..imaging import image_size` sits inside a loop at line 209.
- `backend/vgac/events.py:21, 35-37`: `self.history` is appended to and trimmed but never read.
- `backend/vgac/reference.py:191`: `import yaml` inside `style_presets()` repeats the module import, and the file is re-read on every request, unlike the cached `_data()`.
- `backend/vgac/stages/turnaround.py:156`: `sc.version.parent_version_id = parent.id` assigns a value already there (see B5).
- `backend/vgac/db.py:5` says "no migrations: delete library/vgac.sqlite", while `_ensure_columns` at line 223 is a migration. `db.py:101` uses `json.dumps` while every other JSON setter uses `_dumps`.
- `backend/vgac/blender/*.py` and `backend/vgac/workflows.py:46`: these `print` calls are deliberate. Blender stdout is the log, and workflows is a CLI. No other debug prints exist.

**Fix.** Delete the dead items, move the imports to the top, cache `style_presets` through `_data()` (it is then cleared by `reload()`), and correct the docstring.

**Effort.** S.

**Safe.** Yes.

## H3. Hardcoded instance names, timeouts and thresholds

Files: `backend/vgac/main.py:679`; `backend/vgac/stages/judge.py:40-41`; `backend/vgac/stages/base.py:177`; `backend/vgac/jobs.py:35-36`; `backend/vgac/stages/mesh.py:122`; `backend/vgac/stages/turnaround.py:21`; `backend/vgac/quality.py:114`; `backend/vgac/stages/rig.py:109, 142, 165, 174`

```python
        job = q.submit("b", job_kind(v.stage, "judge", a.category), a.id, v.id, {"version_id": v.id, "op": "judge", "mode": "local"})
```

```python
        checks.append(quality.check("triangle_budget", tris <= sc.budget["triangles"] * 1.05, tris, sc.budget["triangles"]))
```

```python
        out.append(check("max_influences", info["max_influences"] <= 4, info["max_influences"], 4))
```

**Why it matters.**

- `config.yaml` names the instances, but the code assumes they are called "a" and "b" in five places.
- The stage-to-instance map is code.
- The same check id, `triangle_budget`, allows 5 percent over budget in mesh and nothing in prep or export (`quality.geometry_checks`).
- `max_influences <= 4` ignores the rig's own `group_per_vertex` parameter, which goes up to 8.
- The Blender timeouts of 1800 and 3600 s and the turnaround `LIMITS` are inline.

**Fix.** Add `stage_instances` and `judge_instance` to `Settings`, with the current values as defaults. Pass `group_per_vertex` to `rig_checks`. Name the 5 percent tolerance as a constant in `quality`. Optionally move the turnaround limits into the category YAML next to `silhouette_min`.

**Effort.** S to M.

**Safe.** Yes, with defaults equal to today's values. The `max_influences` change alters a check result when `group_per_vertex` is above 4.

## H4. Validation and response shapes differ between similar endpoints

Files: `backend/vgac/main.py:109-126, 390, 508`; `backend/vgac/jobs.py:374`

```python
class AssetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str
    style_preset: str
...
class AssetPatch(BaseModel):
    name: str | None = None
```

**Why it matters.**

- `PUT /api/assets/{id}` accepts an empty name that `POST` rejects; `ProjectPatch` does validate.
- `style_preset` is never checked against `/api/styles` on create or update, and `StageContext` silently falls back to "zturbo".
- Deletes return `{"ok": True, "deleted": id}` while every other mutation returns the object.
- A cancelled queued job marks its version "error" (jobs.py:374) while the job says "cancelled". `VERSION_STATUSES` has no cancelled state, so the UI shows "failed" for something the person cancelled.

**Fix.** Use `Field(default=None, min_length=1, max_length=200)` on `AssetPatch.name`. Validate `style_preset` the way category and platform are validated. Document the delete shape in API.md, or return 204. Set a cancelled version to "rejected", or add a "cancelled" version status.

**Effort.** S.

**Safe.** Mostly. The validation is stricter, and the version status shown for a cancel changes.

## H5. Exceptions swallowed with no log

Files: `backend/vgac/main.py:458-464`; `backend/vgac/comfy.py:238-243`; `backend/vgac/jobs.py:179-183`; `backend/vgac/blender/prep.py:142-145`

```python
    for key, rel in src.files.items():
        try:
            dst = library.import_file(folder, library.abs_path(rel, settings))
            files[key] = library.rel_path(dst, settings)
        except Exception:  # noqa: BLE001
            continue
```

```python
try:
    bpy.ops.object.shade_auto_smooth(angle=math.radians(smooth_angle))
except Exception:
    pass
```

**Why it matters.**

- If copying the base concept's image fails, the variant is created with no image. Its first refine then fails with "refine needs a parent concept version with an image", which hides the real cause.
- If auto-smooth fails in the Blender version in use, the `smooth_angle` parameter silently has no effect.
- The interrupt and kill swallows are reasonable but should leave a debug log.

**Fix.** Log a warning with the key and exception in `create_variant`, and return 500 if `image` was not copied. Print a warning line in `prep.py` so it reaches `prep.log`, and record it in the report. Add `log.debug` in `interrupt` and `cancel_current`.

**Effort.** S.

**Safe.** Yes.

## H6. Frontend types lag the backend responses

File: `frontend/src/api.ts:71-77, 154-163, 203-209, 313, 333`

```ts
export type Project = {
  id: string
  name: string
  created_at: string
  style_lock: StyleLock
  platform_target: PlatformId
}
```

**Why it matters.**

- The backend also returns `unity_project_path` on projects, and nothing in `frontend/src` references it. The decision D12 import into the user's Unity project can only be configured through the API.
- `Asset` lacks `concept_model`.
- `Category` lacks `views`, `lod_ratios`, `topology` and `input_view_default`.
- `Suggestion` lacks `total_minutes`, `platform_label` and `category_label`.
- `Health.queue` lacks `queued` and `next_job_id`, and types `current_job_id` as `number | string`.
- `judge()` returns `judge_job` in local mode.
- `deleteProject` is typed `void` but receives `{ok, deleted}`.

**Fix.** Bring the types in line, and add a `unity_project_path` field to the project settings editor (`ProjectsPage.tsx` `StyleLockEditor`). Longer term, generate the types from FastAPI's OpenAPI schema (`openapi-typescript`) so they cannot drift.

**Effort.** S for the types; M for the generator.

**Safe.** Yes.

## H7. Public endpoints and paths with no test

File: `backend/tests/test_api.py`

The 15 API tests cover projects, assets, variants, run, refine, adjust, select, reject (done version only), judge, feedback, gallery, jobs, library, the websocket and autopilot start and get. These have no test:

- `GET /api/suggest`
- `POST /api/assets/{aid}/autopilot/stop`
- `POST /api/queue/{instance}/pause` (and its 404 for an unknown instance)
- `GET /` (the placeholder when `frontend/dist` is missing)
- reject of a queued or running version (would catch B1 and B2)
- `Version.job_id` non-null after a run (B1)
- `PUT /api/assets/{id}` with an invalid platform or category (the error paths)
- autopilot state after a simulated restart (B6)

**Fix.** Add one test per item. The `fake_execute` fixture already makes this cheap.

**Effort.** S to M.

**Safe.** Yes.

## U1. Oversized components and prop drilling

Files: `frontend/src/pipeline/StagePanel.tsx` (968 lines, 805 when the review started); `frontend/src/pages/AssetPage.tsx` (420); `frontend/src/Viewer.tsx` (381); `frontend/src/components/ui.tsx` (332); `frontend/src/pages/ProjectsPage.tsx` (321)

```tsx
export function StagePanel({ asset, stage, jobs, suggestion, baseName, autopilot, apAvailable, autopilotActions, onMakeVariant, onChanged, onError, onCancelJob }: Props) {
```

**Why it matters.**

- `StagePanel.tsx` holds about 17 components and helpers: CandidateCard, StageViewer, FilesRow, ParamsRow, HappyBlock, the feedback hook and buttons, ChangePanel, FineTune, AdjustField and more. It grew by 163 lines during this review.
- `autopilot`, `apAvailable`, `autopilotActions`, `jobs`, `suggestion` and `baseName` pass from AssetPage through StagePanel into GuidePanel unchanged, which gives GuidePanel 17 props.
- `autopilotActions` and the `apCall` closures are rebuilt on every AssetPage render, so nothing below can be memoised.

**Fix.**

1. Move `FineTune` and `AdjustField` to `pipeline/FineTune.tsx`, `CandidateCard` to `pipeline/CandidateCard.tsx`, and the feedback pieces to `pipeline/Feedback.tsx`.
2. Put the asset-level context (`asset`, `jobs`, `autopilot`, `apAvailable`, `autopilotActions`, `suggestion`, `reloadSoon`) in an `AssetContext` provided by AssetPage.
3. Wrap `autopilotActions` in `useMemo`.
4. Move `statusLine` and `VariantPanel` out of AssetPage.

**Effort.** M.

**Safe.** Yes. The moves are mechanical.

## U2. Autopilot state is kept in two places

File: `frontend/src/pages/AssetPage.tsx:26-41`

```tsx
  const [autopilot, setAutopilot] = useState<AutopilotState | null>(null)
  ...
      if ('autopilot' in a) {
        setApAvailable(true)
        if (a.autopilot) setAutopilot(a.autopilot)
      }
```

**Why it matters.** Autopilot state arrives three ways: on `asset.autopilot` from every reload, from `GET .../autopilot` (the probe and the 3 s poll), and from websocket events. Each writes the same `useState`. Which value wins depends on arrival order. An older asset reload can overwrite a newer websocket state, since there is no `updated_at` comparison.

**Fix.** Keep one source. Use `asset.autopilot` plus websocket events, and accept an update only if its `updated_at` is newer. Keep the separate probe only for detecting 404 on old backends, or drop it, since the backend always has the endpoint now.

**Effort.** S.

**Safe.** Yes.

## U3. Strings that bypass the help registry

File: `frontend/src/pipeline/StagePanel.tsx:658-679, 803`

```tsx
          <Info entry={{ plain: 'Fine-tune', meaning: 'Re-run this step with different settings.', why: 'Use it when the result is close but a setting is off; the description stays the same.', technical: 'adjust' }} />
```

**Why it matters.** `help.ts` exists so the wording lives in one place, as the `guidance.py` docstring says. An inline `Info entry`, the six per-stage placeholders in `placeholderFor`, the `HappyBlock` button titles and the first-run overlay copy in `App.tsx` are written in components. Copy reviews and future translation miss them.

**Fix.** Add `term.finetune` to `TERM_HELP`, and use `<Info term="term.finetune" />`. Move the stage placeholders into `STAGE_TEXT` as a `placeholder` field.

**Effort.** S.

**Safe.** Yes.

---

## Prioritised cleanup list

### Do now (S, safe or a plain bug fix)

1. B1 and B2: flush the job before setting `Version.job_id`; never overwrite a "rejected" version in the worker's `finally`; add the two tests from H7.
2. B8: key the Fine-tune reset on `target.id` and the params content.
3. B3: use `publish_threadsafe` in the feedback endpoint.
4. B9: rename the "height" guide key to "height_m"; fix the texture help sentence.
5. B12: use a local `alive` flag in `useAsync`.
6. P3 (part): `selectinload(Asset.versions)` in `list_assets`; debounce ProjectPage's reload.
7. P6 and P7: reuse the cutout stats; extract alpha once; count with `func.count()`.
8. H2: delete the dead material branch in export, `EventBus.history`, the unused `reference` import and the redundant local imports.
9. D5: one timestamp helper.
10. B5: pass the source version explicitly in turnaround refine and adjust.
11. B6: on startup, resume or pause autopilot states left "running".

### Do next (M)

1. P1: throttle message-only progress updates on the backend; reload the asset only on job status transitions on the frontend.
2. B7: re-queue the running job on graceful shutdown and stop the worker loop properly.
3. B4: give the project import its own report file.
4. P2: move image work and the synchronous parts of async endpoints off the event loop.
5. P4 and P5: poll autopilot only without the websocket; use one long-lived httpx client per instance; give the launcher a cheap ping endpoint.
6. H1 and U1: move the pipeline helpers into `vgac/pipeline.py`; split `StagePanel.tsx`; add an `AssetContext`.
7. D1, D2, D3: one source for the model list, the stage list and the check policy, served through `/api/stages`; the frontend copies become mock-only fallbacks.
8. H3: instance names in config; `group_per_vertex` passed to `rig_checks`.

### Later (L or risky)

1. D8: a shared `_common.py` for the Blender scripts. Each script needs a manual Blender run to verify.
2. H6 (generator): generate `api.ts` types from the OpenAPI schema.
3. P3 (summary shape): a slim per-asset summary payload for the project grid and gallery. This changes the API contract, so update docs/API.md and the mock together.
4. B11: per-category stage skipping in the YAML instead of the open walk-back. This could reject calls that work today.
5. H4: a "cancelled" version status. Touches `VERSION_STATUSES`, the UI status words and autopilot's final-state checks.
