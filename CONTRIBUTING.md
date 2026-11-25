# Contributing to VGAC

Thank you for helping. This page is the short version of how to work on the code. Using the app is covered in the [README](README.md).

## What you need to work on the code

| Tool | Version | Needed for |
|---|---|---|
| Git | any | everything |
| Python | 3.13 or newer | the backend and its tests |
| Node.js | 22.12.0 or newer (npm comes with it) | the frontend and the launcher's tests |
| Windows | 10 or 11 | the starter (it is built with the C# compiler that ships with Windows) and a few tests that run PowerShell |

The exact minimums, and why each is what it is, live in `backend/vgac/data/minimum_versions.json`.

**You do not need a graphics card, ComfyUI, Blender or Unity** to change most of the code. All the tests run without them: they use stand-ins, and the few tests that need a real Blender or a downloaded model file skip themselves with a reason. You only need the full install from the README if you want to make real assets with your changes.

## Set up from a fresh clone

Run these in PowerShell from the project folder.

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
cd frontend; npm ci; cd ..
```

The Python install is about 1 GB and takes a few minutes. `npm ci` takes under a minute. The launcher in `desktop/` needs no install to run its tests.

## Run the tests

| What | Command (from the project folder) | Time |
|---|---|---|
| Backend, in parallel (quicker) | `cd backend` then `.venv\Scripts\python -m pytest -q -n 3 --dist loadfile tests` | about 2,990 test functions (`def test_` count under `backend/tests`); same results as a serial run (pytest-xdist is pinned in `requirements.txt`) |
| Backend, serial | `cd backend` then `.venv\Scripts\python -m pytest -q tests` | the same tests, one after another |
| Backend, one file | `.venv\Scripts\python -m pytest -q tests\test_minimums.py` | seconds |
| Frontend: type check and build | `cd frontend` then `npm run build` | under 10 seconds (it runs the type check first, then builds) |
| Frontend: its own tests | `cd frontend` then `npm test` | a few seconds |
| Starter (the .exe that starts the app) | `starter\build.cmd test` | about 1 minute |
| Launcher (Electron) | `node desktop\appbridge.test.js` and `node desktop\machine.test.js` | a second each |

Run the part you changed while you work, and all of them before you open a pull request. The backend run is long; if your change is small, run the files that cover it and say so in the pull request. A few backend tests skip on a machine without Blender or the downloaded animation packs. That is normal. A skip is not a failure, but a test that fails is.

To see your change in the real app, `npm run dev:mock` in `frontend/` runs the screens against a built-in pretend backend with no setup at all. `docs/ADVANCED.md` has the rest of running from source.

## How we use git

- **One branch for each piece of work.** Branch from the latest `master`. Name it for what it does (`fix-hair-colour`, `add-narrow-layout`).
- **Keep pull requests small.** One idea each. Unrelated clean-ups go in their own pull request.
- **Nothing is committed straight to `master`.** Everything arrives through a pull request.
- **A pull request is ready to merge when:**
  1. the tests pass (the checks on the pull request run them for you);
  2. its description says what changed and why, in plain words;
  3. its description says what you checked by running it, and what you could not check (for example "I have no graphics card, so the real build was not run").
- **We squash-merge**, so each pull request becomes one commit on `master`. Write the pull request title as you would a commit message.
- Add tests for new behaviour and for bugs you fix. The existing tests show the style.

## House rules you would not guess

- **No research, planning or audit documents in the repository.** Keep those to yourself. Only guides for people and the running-state documents are committed. `docs/` ignores new files unless they are added to the list in `.gitignore`, and that list is for user guides.
- **Plain words in anything a person reads.** The app is for enthusiasts of all skill levels. Say "Clean up for the game", not `prep`; say "PC and console", not `pc_mid`. Two tests guard this: `backend/tests/test_ui_plain_names.py` scans the frontend source for raw step and platform names used as labels, and `backend/tests/test_hardware_setup.py` (`test_no_jargon_in_messages`) does the same for the setup messages. If one fails, use the plain name helpers (`stagePlain`, `platformPlain`) instead of the raw key.
- **Downloads come only from the publisher's own host** and are pinned by SHA-256 in `setup/downloads.json`. Never add a mirror, a re-upload or a file without a hash.
- **No personal paths or machine settings in tracked files.** No user names, e-mail addresses or folders from your computer. Settings for your machine go in `local/` (for example `local/machine.json`), which git ignores. `backend/config.yaml` holds only defaults that are true on any machine.
- **Keep `ARCHITECTURE.md` current.** If you add a module, change how parts start, move where files live or change the order of a pipeline step, update it in the same pull request, briefly.

## Where things are

[ARCHITECTURE.md](ARCHITECTURE.md) is the best place to start: it shows the parts (launcher, backend, frontend, the graphics engines) and how a job flows through them. `backend/vgac/` is the server, `frontend/src/` the screens, `starter/` the Windows starter, `desktop/` the Electron launcher, and `setup/` the install helpers. In `docs/`, [BACKEND.md](docs/BACKEND.md) is the code map for the backend, [API.md](docs/API.md) the full API, [ADVANCED.md](docs/ADVANCED.md) the settings and running from source, [FIRST-TIME-SETUP.md](docs/FIRST-TIME-SETUP.md) the complete install checklist, and [BUILD-THE-EXE.md](docs/BUILD-THE-EXE.md) how the launcher is built.

By contributing you agree that your work is released under the project's licence, the GNU General Public License version 3 or later ([LICENSE](LICENSE)).
