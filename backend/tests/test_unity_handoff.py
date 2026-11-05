"""Hand-off of the project import to an open Unity Editor (export.unity_project_open, handoff_to_editor,
collect_unity_import, and _deliver's choice between the hand-off and the batch import)."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from vgac.stages import export

REPO = Path(__file__).resolve().parents[2]
IMPORTER = REPO / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"
NAME = "Chest_1234abcd"


class _Handle:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _lock(proj: Path) -> Path:
    (proj / "Temp").mkdir(parents=True, exist_ok=True)
    p = proj / "Temp" / "UnityLockfile"
    p.write_bytes(b"")
    return p


# --- detection --------------------------------------------------------------------------------------------------------

def test_project_open_detection(tmp_path):
    assert export.unity_project_open(tmp_path) is False  # no Temp/UnityLockfile: closed
    _lock(tmp_path)
    h = _Handle()
    assert export.unity_project_open(tmp_path, opener=lambda p: h) is False  # opens fine: a stale lock, closed
    assert h.closed

    def held(p):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    assert export.unity_project_open(tmp_path, opener=held) is True

    def vanished(p):
        raise FileNotFoundError(p)

    assert export.unity_project_open(tmp_path, opener=vanished) is False
    assert export.unity_project_open(tmp_path) is False  # the real opener on a lock nobody holds


# --- a fake export context ---------------------------------------------------------------------------------------------

def _export_dir(d: Path, humanoid_files: bool = True) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{NAME}.fbx").write_bytes(b"fbx")
    (d / f"{NAME}_unreal.fbx").write_bytes(b"unreal")
    for t in ("base_color.png", "normal.png", "ao.png", f"T_{NAME}_MetallicSmoothness.png", f"T_{NAME}_ORM.png"):
        (d / t).write_bytes(b"png")
    (d / "colliders.json").write_text('{"chosen": "box", "parts": []}')
    if humanoid_files:
        (d / "hitboxes.json").write_text('{"hitboxes": []}')
        (d / "anim_clips.json").write_text('{"clips": []}')
    return d


class _Ctx:
    def __init__(self, tmp: Path, proj: Path, unity_exe: Path | None, clips_lib: Path | None = None):
        self.dir = _export_dir(tmp / "version")
        self.metrics: dict = {}
        self.files: dict = {}
        self.unity_calls: list = []
        self.version = SimpleNamespace(id="v123", files={}, metrics=self.metrics, params={})
        self.project = SimpleNamespace(name="Proj", unity_project_path=str(proj))
        self.asset = SimpleNamespace(name="Chest", id="1234abcd99", category="prop_rigid")
        self.category = {"rig_engine": "skintoken_mixamo"}
        self.settings = SimpleNamespace(export_path=str(tmp / "exports"), library_path=str(tmp / "library"), unity_project=str(REPO / "unity-sandbox"),
                                        unity_exe=str(unity_exe) if unity_exe else None, clips_libraries={"ual1": str(clips_lib)} if clips_lib else {})
        self._rig = SimpleNamespace(metrics={"bones": 50}, params={})

    def stage_version(self, s):
        return self._rig if s == "rig" else None

    def file(self, key):
        return self.dir / f"{NAME}.fbx" if key == "fbx" else None

    def set_metrics(self, **v):
        self.metrics.update(v)

    def progress(self, f, m):
        pass

    def register(self, key, p):
        self.files[key] = str(p)

    async def unity(self, method, args, flags, log_name, timeout=3600, project_path=None):
        self.unity_calls.append((method, dict(args), list(flags), project_path))
        (self.dir / "unity_report.json").write_text(json.dumps({"ok": True, "prefab": f"Assets/VGAC/{NAME}/{NAME}.prefab", "importer_version": "6"}))
        return ""


def _setup(tmp_path, open_: bool, monkeypatch, unity: bool = True):
    proj = tmp_path / "Game"
    (proj / "Assets").mkdir(parents=True)
    exe = tmp_path / "Unity.exe"
    exe.write_bytes(b"")
    lib = tmp_path / "clips" / "ual1.fbx"
    lib.parent.mkdir()
    lib.write_bytes(b"lib")
    monkeypatch.setattr(export, "unity_project_open", lambda p: open_)
    return _Ctx(tmp_path, proj, exe if unity else None, lib), proj, lib


def _deliver(sc):
    asyncio.run(export._deliver(sc, NAME, {"unity": True, "clips_library": "ual1", "movement_shape": "upright"}))


# --- the hand-off ------------------------------------------------------------------------------------------------------

def test_open_project_gets_a_request_not_a_batch_run(tmp_path, monkeypatch):
    sc, proj, lib = _setup(tmp_path, True, monkeypatch)
    _deliver(sc)
    assert sc.unity_calls == []  # Unity is never launched against an open project
    folder = proj / "Assets" / "VGAC" / NAME / "_incoming"
    req = json.loads((folder / "vgac_import.json").read_text(encoding="utf-8"))
    # Every argument the batch call would have passed, file arguments as names inside the folder.
    args = req["args"]
    assert "-nographics" not in args and "-vgacHumanoid" in args
    pairs = dict(zip(args[args.index("-vgacInput"):][::2], args[args.index("-vgacInput"):][1::2]))
    assert pairs["-vgacInput"] == f"Assets/VGAC/{NAME}/_incoming"
    assert pairs["-vgacName"] == NAME
    assert pairs["-vgacCategory"] == "prop_rigid"
    assert pairs["-vgacColliders"] == "colliders.json"
    assert pairs["-vgacHitboxes"] == "hitboxes.json" and pairs["-vgacMovementShape"] == "upright"
    assert pairs["-vgacClipInfo"] == "anim_clips.json"
    assert pairs["-vgacClips"] == str(lib)  # the shared library is read where it is
    assert req["arguments"] == pairs
    assert req["importer_version"] == str(export.importer_version(IMPORTER)) and int(req["importer_version"]) >= 6
    assert len(req["request_id"]) == 32 and req["source_version"] == "v123"
    assert req["prefab"] == f"Assets/VGAC/{NAME}/{NAME}.prefab"
    for f in (f"{NAME}.fbx", "base_color.png", "normal.png", "ao.png", f"T_{NAME}_MetallicSmoothness.png", f"T_{NAME}_ORM.png",
              "colliders.json", "hitboxes.json", "anim_clips.json"):
        assert (folder / f).is_file(), f
    assert not (folder / f"{NAME}_unreal.fbx").exists()  # only the importer's own FBX
    assert not list(folder.glob("*.tmp~"))
    # The importer script in the project is current (a newer one in an open Editor just recompiles).
    assert export.importer_version(proj / "Assets" / "Editor" / "VgacImport.cs") == export.importer_version(IMPORTER)
    m = sc.metrics["unity_project_import"]
    assert m["ok"] is None and m["state"] == "sent" and m["request_id"] == req["request_id"]
    assert m["project"] == str(proj) and Path(m["folder"]) == folder
    assert m["note"] == "Sent to your open Unity project. It finishes importing when you switch to Unity."


def test_new_request_clears_an_old_result(tmp_path, monkeypatch):
    sc, proj, _ = _setup(tmp_path, True, monkeypatch)
    folder = export.handoff_folder(proj, NAME)
    folder.mkdir(parents=True)
    (folder / "unity_report.json").write_text(json.dumps({"ok": True, "request_id": "old"}))
    (folder / "vgac_import.done.json").write_text("{}")
    (folder / "vgac_import.done.json.meta").write_text("guid")
    _deliver(sc)
    assert not (folder / "unity_report.json").exists() and not (folder / "vgac_import.done.json").exists()
    assert not (folder / "vgac_import.done.json.meta").exists()
    assert export.collect_unity_import(sc.version) is None  # nothing new yet


def test_old_importer_in_project_cannot_take_a_handoff(tmp_path):
    proj = tmp_path / "Game"
    src = _export_dir(tmp_path / "v")
    m = export.handoff_to_editor(src, proj, NAME, {"-vgacInput": src, "-vgacName": NAME}, ["-nographics"],
                                 {"action": "kept_newer", "version": 5, "previous": 5})
    assert m["ok"] is False and m["state"] == "failed" and "version 5" in m["error"]
    assert not (export.handoff_folder(proj, NAME) / "vgac_import.json").exists()


# --- collecting the result ---------------------------------------------------------------------------------------------

def _sent(tmp_path, monkeypatch):
    sc, proj, _ = _setup(tmp_path, True, monkeypatch)
    _deliver(sc)
    m = sc.metrics["unity_project_import"]
    return sc, Path(m["folder"]), m["request_id"]


def test_collect_picks_up_the_editor_report(tmp_path, monkeypatch):
    sc, folder, rid = _sent(tmp_path, monkeypatch)
    assert export.collect_unity_import(sc.version) is None  # the Editor has not run yet
    (folder / "unity_report.json").write_text(json.dumps({"ok": True, "request_id": "someone-else", "prefab": "x"}))
    assert export.collect_unity_import(sc.version) is None  # another request's report
    prefab = f"Assets/VGAC/{NAME}/{NAME}.prefab"
    (folder / "unity_report.json").write_text(json.dumps({"ok": True, "request_id": rid, "prefab": prefab, "importer_version": "6", "error": ""}))
    got = export.collect_unity_import(sc.version)
    assert got["ok"] is True and got["state"] == "imported" and got["prefab"] == prefab and got["error"] == ""
    assert got["request_id"] == rid and "note" not in got
    assert sc.version.metrics["unity_project_import"] == got
    assert export.collect_unity_import(sc.version) is None  # collected once


def test_collect_reports_a_failure(tmp_path, monkeypatch):
    sc, folder, rid = _sent(tmp_path, monkeypatch)
    (folder / "unity_report.json").write_text(json.dumps({"ok": False, "request_id": rid, "prefab": "", "error": "System.Exception: FBX not imported"}))
    got = export.collect_unity_import(sc.version)
    assert got["ok"] is False and got["state"] == "failed" and "FBX not imported" in got["error"] and got["prefab"] == ""


def test_collect_ignores_versions_without_a_handoff():
    assert export.collect_unity_import(SimpleNamespace(metrics={})) is None
    assert export.collect_unity_import(SimpleNamespace(metrics={"unity_project_import": {"ok": True, "prefab": "p"}})) is None


# --- the batch path is unchanged --------------------------------------------------------------------------------------

def test_closed_project_keeps_the_batch_import(tmp_path, monkeypatch):
    sc, proj, _ = _setup(tmp_path, False, monkeypatch)
    _deliver(sc)
    assert len(sc.unity_calls) == 1
    method, args, flags, project_path = sc.unity_calls[0]
    assert method == "VgacImport.Run" and Path(project_path) == proj and "-nographics" in flags
    assert args["-vgacInput"] == sc.dir and args["-vgacName"] == NAME
    m = sc.metrics["unity_project_import"]
    assert m["ok"] is True and m["prefab"] == f"Assets/VGAC/{NAME}/{NAME}.prefab" and "state" not in m
    assert not (proj / "Assets" / "VGAC").exists()


def test_missing_unity_keeps_the_plain_error(tmp_path, monkeypatch):
    sc, proj, _ = _setup(tmp_path, True, monkeypatch, unity=False)

    async def no_unity(*a, **k):
        raise RuntimeError("unity_exe not configured or missing: None")

    sc.unity = no_unity
    _deliver(sc)
    m = sc.metrics["unity_project_import"]
    assert m["ok"] is False and "unity_exe not configured" in m["error"] and "state" not in m
    assert not (proj / "Assets" / "VGAC").exists()


def test_not_a_unity_project_keeps_the_plain_error(tmp_path, monkeypatch):
    sc, proj, _ = _setup(tmp_path, True, monkeypatch)
    (proj / "Assets").rmdir()
    _deliver(sc)
    assert sc.metrics["unity_project_import"] == {"ok": False, "error": f"not a Unity project: {proj}"}


def test_importer_source_has_the_watcher():
    src = IMPORTER.read_text(encoding="utf-8")
    assert export.importer_version(IMPORTER) >= export.HANDOFF_MIN_IMPORTER
    for needle in ("class VgacImportWatcher : AssetPostprocessor", "OnPostprocessAllAssets", "[InitializeOnLoadMethod]",
                   '[MenuItem("Tools/VGAC/Import pending assets")]', "public static void ImportPending()", '"vgac_import.done.json"',
                   '"vgac_import.json"', "static Report Import(ImportArgs a)"):
        assert needle in src, needle
