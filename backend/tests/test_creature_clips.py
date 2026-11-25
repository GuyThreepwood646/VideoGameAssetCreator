"""Hand-made creature clips: which clips a category takes from the pack, that every one is fetched by setup, and the rig
stage's choice and fallback to the generated cycle (fake stage context; the Blender script is run by hand on stored rigs)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from tests.test_animation_quality import FakeSC
from vgac import reference
from vgac.stages import rig

ROOT = Path(__file__).resolve().parents[2]
DOWNLOADS = json.loads((ROOT / "setup" / "downloads.json").read_text(encoding="utf-8"))
PACK = DOWNLOADS["creature_clip_packs"]["mesh2motion"]
CREATURES = ("quadruped",)


def _pack_dir(tmp_path: Path) -> Path:
    root = tmp_path / "mesh2motion"
    for f in PACK["files"]:
        p = root / f["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"glb")
    return root


def test_every_hand_made_clip_is_fetched_by_setup_and_credited():
    listed = {f["path"] for f in PACK["files"]}
    assert PACK["licence"] == "CC0 1.0" and PACK["base_url"].startswith("https://raw.githubusercontent.com/Mesh2Motion/")
    assert all(len(f["sha256"]) == 64 and f["size"] > 0 for f in PACK["files"])
    for cid in CREATURES:
        cat = reference.category(cid)
        spec = cat["hand_made_clips"]
        assert spec["pack"] == "mesh2motion" and reference.model_record(spec["pack"])["licence"] == "CC0"
        for r in spec["clips"]:
            animal, _, clip = r["from"].partition("/")
            assert f"{animal}/animations/{clip}.glb" in listed, (cid, r)
            assert r["name"] in cat["clips"]  # a hand-made clip replaces a clip the category makes
    used = {f"{r['from'].partition('/')[0]}/animations/{r['from'].partition('/')[2]}.glb"
            for cid in CREATURES for r in reference.category(cid)["hand_made_clips"]["clips"]}
    assert used == listed  # setup fetches nothing that is not used


def test_hand_made_rows_follow_the_moves_knob_and_the_files_on_disk(tmp_path):
    root = _pack_dir(tmp_path)
    quad = reference.category("quadruped")
    rows = rig.hand_made_rows(quad, None, root)
    assert [r["name"] for r in rows] == ["idle", "walk", "run", "turn_left", "turn_right", "attack", "death"]
    assert Path(rows[1]["file"]).parts[-3:] == ("fox", "animations", "walk.glb")
    assert rows[1]["loop"] is True and rows[5]["loop"] is False and rows[1]["pack"] == "mesh2motion"
    assert [r["name"] for r in rig.hand_made_rows(quad, ["walk", "death", "hit"], root)] == ["walk", "death"]
    (root / "fox" / "animations" / "walk.glb").unlink()
    assert "walk" not in [r["name"] for r in rig.hand_made_rows(quad, None, root)]  # no file: the generated walk stays
    assert rig.hand_made_rows(quad, None, None) == []
    for other in ("multileg", "serpent", "winged"):  # a category with none keeps its generated cycles
        assert rig.hand_made_rows(reference.category(other), None, root) == []


class CreatureSC(FakeSC):
    def __init__(self, tmp_path, category, clips_dir, fail=False):
        super().__init__(tmp_path, category, {})
        self.settings = SimpleNamespace(clips_libraries={}, blender_timeout_seconds=60, creature_clips_dir=clips_dir)
        self.fail = fail

    async def blender(self, script, args, log_name=None, timeout=None):
        if script != "creature_clips.py":
            return await super().blender(script, args, log_name, timeout)
        self.calls.append((script, [str(a) for a in args]))
        rep = Path(args[args.index("--report") + 1])
        if self.fail:
            rep.write_text(json.dumps({"ok": False, "error": "two legs read as the same role"}))
            raise RuntimeError("blender exited 2")
        names = [r["name"] for r in json.loads(Path(args[args.index("--spec") + 1]).read_text())]
        rep.write_text(json.dumps({"ok": True, "clips": names + ["hit"], "library_clips": names, "missing_clips": []}))


def _scripts(sc):
    """The Blender scripts the stage ran, without the grip guard's stretch probe (humanoid hands only)."""
    return [c[0] for c in sc.calls if c[0] != "skin_stretch.py"]


def _quad(tmp_path, clips_dir, fail=False, sub="stage"):
    (tmp_path / sub).mkdir(exist_ok=True)
    return CreatureSC(tmp_path / sub, {**reference.category("quadruped")}, clips_dir, fail)


def test_creature_takes_the_hand_made_clips_and_credits_the_pack(tmp_path):
    sc = _quad(tmp_path, _pack_dir(tmp_path))
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {}))
    assert _scripts(sc) == ["procanim.py", "creature_clips.py", "render_anim.py"]
    args = next(c[1] for c in sc.calls if c[0] == "creature_clips.py")
    assert args[0] == args[1] == str(sc.dir / "anim.glb") and args[args.index("--classes") + 1].endswith("anim_anim.json")
    m = sc.version.metrics
    assert m["clips_source"] == "library" and m["library_clips"][:2] == ["idle", "walk"] and m["procedural_clips"] == ["hit"]
    assert m["retarget_error"] is None and sc.files["anim_retarget"].name == "anim_retarget.json"
    assert [x["id"] for x in m["models"]] == ["mesh2motion"] and m["models"][0]["role"] == "moves"


def test_creature_keeps_the_generated_cycles_when_asked_or_when_nothing_fits(tmp_path):
    sc = _quad(tmp_path, _pack_dir(tmp_path), sub="stage_p")
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {"clips_source": "procedural"}))
    assert _scripts(sc) == ["procanim.py", "render_anim.py"]
    assert sc.version.metrics["clips_source"] == "procedural" and sc.version.metrics["library_skipped"]
    for n, clips_dir in enumerate((tmp_path / "no_such_folder", None)):
        sc = _quad(tmp_path, clips_dir, sub=f"stage{n}")
        asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {}))
        assert _scripts(sc) == ["procanim.py", "render_anim.py"]
        assert sc.version.metrics["clips_source"] == "procedural" and "found on disk" in sc.version.metrics["library_skipped"]


def test_creature_script_failure_leaves_the_generated_cycles(tmp_path):
    sc = _quad(tmp_path, _pack_dir(tmp_path), fail=True)
    asyncio.run(rig._animate(sc, tmp_path / "rig.glb", {}))
    m = sc.version.metrics
    assert "same role" in m["retarget_error"] and m["clips_source"] == "procedural" and m["library_clips"] == []
    assert _scripts(sc)[-1] == "render_anim.py"  # the strip is still made
