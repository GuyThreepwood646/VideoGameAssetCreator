"""Spring bones (docs/HAIR-RESEARCH.md 3.2 (f), stage 2): parts that hang free sway when the character moves.

The rig step runs blender/springs.py on the animated GLB after the moves are made (rig.py _springs): it finds the parts
that hang clear of the body, adds a chain of 2 to 4 bones to each, fades the skin weights onto them and bakes a small
spring simulation into every clip. This module is the backend's side: whether it runs, the Blender arguments, the
metrics, the check sentences' values, "What was taken as a hanging part" (the version file springs_found), and what
the hand-off says about it (springs.json, the manifest block, README lines for Godot and Unreal).

A one-mesh model allows only parts that hang clear of the body to move: a ponytail or a braid away from the neck, a
cape hem, a tail, cloth ends. Hair resting on the shoulders is welded to them and a bob has nothing that hangs; the
pass stands back with a plain reason and never promises movement there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

# Categories the pass looks at. Other rigged categories get the check with the reason it stands back.
CATEGORIES = ("humanoid",)
RIGGED = ("humanoid", "quadruped", "multileg", "winged", "serpent")
FILES = ("springs", "springs_found", "springs_report")
# The worst a looping clip's first and last frames of a chain may differ, and the deepest a chain may pass into a
# hit box beyond where it rests, in metres; over either the check fails softly.
SEAM_MAX_M = 0.002
INTO_BODY_MAX_M = 0.04

KIND_WORDS = {"Hair": "the hair (a ponytail or a braid)", "Cape": "the cape", "Tail": "the tail",
              "Cloth": "the hanging cloth (a scarf end or a sash)"}
KIND_SHORT = {"Hair": "hair", "Cape": "cape", "Tail": "tail", "Cloth": "hanging cloth"}

REASONS = {
    "off": "they are turned off",
    "category": "they are made for people-shaped characters only so far",
    "tail_moves": "the tail already moves with the built-in moves, so it is left to them",
    "no_rig": "there is no skeleton to add them to",
    "no_clips": "there are no moves to bake the sway into",
    "none_found": "nothing hangs clear of the body, so nothing can sway. Hair that rests on the shoulders or the head "
                  "is joined to them in the 3D model",
    "error": "something went wrong on the way",
    "library_rests": "the hairstyle rests on the head, so it moves with the head",
}


def enabled(category: str | None, params: dict[str, Any], anim_report: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    """(run the pass?, the reason key when not). The setting `springs` is on by default for humanoids. For other
    rigged categories the pass stands back: a tail that the built-in moves already animate is left to them."""
    if category not in RIGGED:
        return False, None
    if category not in CATEGORIES:
        tail = (anim_report or {}).get("tail") or []
        return False, "tail_moves" if tail else "category"
    v = params.get("springs", True)
    if v in (False, "off", "false", 0):
        return False, "off"
    return True, None


# What hangs from the head (blender/springs.py describe_part). A non-hair part hanging from the head is a hat's brim,
# a hood or a helmet far more often than a headscarf's end: Vivi's hat swayed as "a scarf end" (his rig versions 1 and
# 2). head_sway: "auto" keeps such parts with the head unless the description names something that should sway (a
# headscarf, ribbon, veil); "hair" never sways them; "all" sways them as before. Headwear named in the description (a
# hat, helmet, hood) also asks more of a head part before it sways as hair (blender HAIR_SHARE_HEADWEAR).
HEAD_SWAY_CHOICES = ("auto", "hair", "all")
HEADWEAR_WORDS = ("hat", "hats", "helmet", "helm", "hood", "hooded", "cap", "crown", "turban", "beanie", "bonnet", "tiara",
                  "headdress", "headpiece", "beret", "fedora", "sombrero", "witch hat", "wizard hat", "cowl", "circlet")
SWAY_WORDS = ("headscarf", "head scarf", "bandana", "ribbon", "ribbons", "veil", "tassel", "tassels", "streamer", "streamers",
              "feather", "feathers", "plume", "kerchief")


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    import re

    t = " " + re.sub(r"[^a-z]+", " ", (text or "").lower()) + " "
    return any(f" {w} " in t for w in words)


def head_options(params: dict[str, Any], description: str) -> dict[str, Any]:
    """{head_sway (the choice), head_cloth ("stiff" or "sway"), headwear (bool), why}: what the hanging-part search does
    with non-hair parts on the head."""
    choice = params.get("head_sway") if params.get("head_sway") in HEAD_SWAY_CHOICES else "auto"
    headwear = _has_word(description, HEADWEAR_WORDS)
    if choice == "all":
        cloth, why = "sway", "chosen"
    elif choice == "hair":
        cloth, why = "stiff", "chosen"
    elif _has_word(description, SWAY_WORDS):
        cloth, why = "sway", "described"
    else:
        cloth, why = "stiff", "default"
    return {"head_sway": choice, "head_cloth": cloth, "headwear": headwear, "why": why}


def blender_args(anim_glb: Path, out_glb: Path, work: Path, clips: Path | None, hitboxes: Path | None,
                 hair_points: Path | None, metallic: Path | None, fps: int = 24,
                 library_hair: tuple[str, Path, Path | None] | None = None, head: dict[str, Any] | None = None) -> list[Any]:
    """springs.py's command line. library_hair (object name, hair_mesh.npz, hair_parts.json) only when the hair comes
    from the library: the hair object is left out of the hanging-part search and its hanging groups get chains."""
    args: list[Any] = [anim_glb, out_glb, "--report", work / "springs_report.json", "--doc", work / "springs.json",
                       "--pictures", work / "springs_pictures", "--fps", fps]
    for flag, p in (("--clips", clips), ("--hitboxes", hitboxes), ("--hair-points", hair_points), ("--metallic", metallic)):
        if p is not None and Path(p).is_file():
            args += [flag, p]
    if head is not None:
        args += ["--head-cloth", head.get("head_cloth") or "stiff"]
        if head.get("headwear"):
            args += ["--headwear"]
    if library_hair is not None:
        obj, mesh, parts = library_hair
        args += ["--hair-object", obj, "--hair-mesh", mesh]
        if parts is not None and Path(parts).is_file():
            args += ["--hair-parts", parts]
    return args


# Hair from the library: the words follow the hairstyle's family (the clean-up step's hair_library.family), not the
# detected part's guess. Today's detected hanging parts keep KIND_WORDS.
LIBRARY_FAMILY_WORDS = {"ponytail": "the ponytail", "braid": "the braid", "long": "the long hair"}
LIBRARY_HAIR_WORDS = "the hair"


def library_hair_words(family: str | None) -> str:
    return LIBRARY_FAMILY_WORDS.get(str(family or "").lower(), LIBRARY_HAIR_WORDS)


def what_sways(chains: list[dict[str, Any]], family: str | None = None) -> str:
    """"the hair (a ponytail or a braid) and the cape": the kinds that sway, each once, in plain words. Chains on hair
    from the library ("library": true) take the hairstyle's family words instead ("the ponytail", "the long hair")."""
    words: list[str] = []
    for c in chains:
        k = c.get("kind")
        w = library_hair_words(family) if c.get("library") else KIND_WORDS.get(k)
        if w and w not in words:
            words.append(w)
    if not words:
        return ""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def left_alone(rejected: list[dict[str, Any]], limit: int = 3) -> list[str]:
    """Plain sentences for the parts that were looked at and left alone (the longest first, one per reason)."""
    out, seen = [], set()
    for r in sorted(rejected or [], key=lambda r: -float(r.get("length") or 0)):
        why = r.get("why")
        if not why or r.get("reason") in seen:
            continue
        seen.add(r.get("reason"))
        h = r.get("top")
        where = f"A part hanging from about {float(h):.2f} m" if isinstance(h, (int, float)) else "A part"
        out.append(f"{where} was left alone: {why[0].lower() + why[1:]}")
        if len(out) >= limit:
            break
    return out


def metrics(report: dict[str, Any] | None, on: bool, reason: str | None, seconds: float | None = None,
            error: str | None = None, family: str | None = None) -> dict[str, Any]:
    """metrics.springs from springs_report.json: what sways, the bones added, the seam and depth numbers per clip."""
    rep = report or {}
    chains = rep.get("chains") or []
    clips = rep.get("clips") or {}
    seam = [v.get("seam_m") for cl in clips.values() for v in (cl.get("chains") or {}).values() if v.get("seam_m") is not None]
    beyond = [v.get("into_beyond_rest_m") or 0.0 for cl in clips.values() for v in (cl.get("chains") or {}).values()]
    into = [v.get("into_hitbox_m") or 0.0 for cl in clips.values() for v in (cl.get("chains") or {}).values()]
    swing = {name: round(max([v.get("swing_deg") or 0.0 for v in (cl.get("chains") or {}).values()] or [0.0]), 1)
             for name, cl in clips.items()}
    if error:
        reason = "error"
    elif on and not chains and reason is None:
        reason = rep.get("reason") or "none_found"
    out = {"on": bool(on), "made": bool(chains) and not error, "reason": reason,
           "chains": [{"name": c.get("name"), "kind": c.get("kind"), "parent": c.get("parent"), "bones": c.get("bones"),
                       "confidence": c.get("confidence"), "verts": c.get("verts"),
                       "length_m": round(float((c.get("part") or {}).get("length") or 0), 3),
                       "gap_cm": (c.get("part") or {}).get("gap_cm"),
                       **({"library": True} if c.get("library") else {})} for c in chains],
           "bones_added": int(rep.get("bones_added") or 0), "what": what_sways(chains, family),
           "left_alone": left_alone(rep.get("rejected") or []),
           "seam_max_m": round(max(seam), 5) if seam else None,
           "into_body_max_m": round(max(beyond), 4) if beyond else None,
           "into_hitbox_max_m": round(max(into), 4) if into else None,
           "swing_deg": swing, "seconds": seconds, "find_seconds": rep.get("find_seconds"), "bake_seconds": rep.get("bake_seconds"),
           "hair_points": rep.get("hair_points"), "error": (error or "")[:300] or None}
    if rep.get("hair_into_body"):
        # hair from the library: how deep the hair's surface went into the body in idle, walk and run (metres)
        out["hair_into_body"] = rep["hair_into_body"]
    return out


def check_values(m: dict[str, Any] | None) -> dict[str, Any] | None:
    """The rig check `springs`: (ok, value, limit, vars), or None when it does not apply (no skeleton). It passes when
    the pass stood back (with the reason) and when the chains were made with clean seams and no part pushed into the
    body; it fails softly on an error, a seam that jumps or a part that passes into the body."""
    if not m:
        return None
    reason = m.get("reason")
    vars_: dict[str, Any] = {"reason": reason or "", "why": REASONS.get(reason or "", ""), "what": m.get("what") or "",
                             "bones": m.get("bones_added") or 0, "chains": len(m.get("chains") or []),
                             "left_alone": " ".join(m.get("left_alone") or []),
                             "plural": len({c.get("kind") for c in m.get("chains") or []}) > 1}
    if not m.get("made"):
        ok = reason != "error"
        vars_["skipped"] = True
        return {"ok": ok, "value": 0, "limit": ">=0", "vars": vars_}
    seam, deep = m.get("seam_max_m"), m.get("into_body_max_m")
    problem = ""
    if seam is not None and seam > SEAM_MAX_M:
        problem = "seam"
        vars_["seam_cm"] = round(seam * 100, 1)
    elif deep is not None and deep > INTO_BODY_MAX_M:
        problem = "into_body"
        vars_["deep_cm"] = round(deep * 100, 1)
    vars_["problem"] = problem
    vars_["deep_cm"] = vars_.get("deep_cm", round((deep or 0) * 100, 1))
    return {"ok": not problem, "value": m.get("bones_added") or 0, "limit": ">0", "vars": vars_}


# --- What was taken as a hanging part -----------------------------------------------------------------------------------


def found_sheet(report: dict[str, Any], pic_dir: Path, dest: Path, cell: int = 360, family: str | None = None) -> Path | None:
    """The model from behind, the side, the back three-quarter and the front with what sways tinted green and each
    chain drawn on it (joints as dots, root to tip), with a caption line: what sways, or why nothing does."""
    pics = (report or {}).get("pictures") or {}
    if not pics:
        return None
    tiles = []
    for label, info in pics.items():
        p = Path(pic_dir) / info["file"]
        if not p.is_file():
            continue
        im = Image.open(p).convert("RGB")
        sx = cell / im.width
        im = im.resize((cell, cell), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        for line in info.get("lines") or []:
            pts = [(x * sx, y * sx) for x, y in line]
            d.line(pts, fill=(255, 200, 0), width=3)
            for i, (x, y) in enumerate(pts):
                r = 4 if i else 5
                d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 60, 60) if i == 0 else (255, 200, 0))
        tiles.append((label, im))
    if not tiles:
        return None
    chains = report.get("chains") or []
    if chains:
        caption = "Sways: " + what_sways(chains, family) + f" ({sum(len(c.get('bones') or []) for c in chains)} bones)"
    else:
        caption = "Nothing sways: " + REASONS.get(report.get("reason") or "none_found", REASONS["none_found"])
    sheet = Image.new("RGB", (cell * len(tiles), cell + 44), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        sheet.paste(im, (i * cell, 44))
        d.text((i * cell + 8, 26), label, fill=(230, 230, 230))
    d.text((8, 6), caption[:180], fill=(255, 255, 255))
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return Path(dest)


# --- the hand-off ------------------------------------------------------------------------------------------------------


def read_doc(path: Path | str | None) -> dict[str, Any]:
    if path is None or not Path(path).is_file():
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def manifest_block(doc: dict[str, Any], unity_report: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the manifest says about the spring bones, or None when there are none."""
    chains = (doc or {}).get("chains") or []
    if not chains:
        return None
    u = unity_report or {}
    return {"file": "springs.json", "chains": [{"name": c["name"], "kind": c["kind"], "parent": c["parent"], "bones": c["bones"]}
                                               for c in chains],
            "bones": sum(len(c["bones"]) for c in chains), "baked_into_clips": True,
            "unity": {"script": "VgacSpringBones", "chains": u.get("springChains"), "warning": u.get("springWarning") or None} if u else None,
            "godot": "by hand: SpringBoneSimulator3D (Godot 4.4 and later); see README.txt; not tried here",
            "unreal": "by hand: AnimDynamics or Spring Controller nodes in the Animation Blueprint; see README.txt; not tried here"}


def readme_lines(name: str, doc: dict[str, Any]) -> list[str]:
    """README.txt lines for the spring bones: what is in the files and how to make the sway react to the game in Godot
    and Unreal (not tried here)."""
    chains = (doc or {}).get("chains") or []
    if not chains:
        return []
    lines = ["Spring bones (small extra bones that sway on their own)", ""]
    for c in chains:
        lines.append(f"- {c['name']}: {KIND_SHORT.get(c['kind'], c['kind'].lower())}, bones {', '.join(c['bones'])}, "
                     f"hanging from {c['parent']}; stiffness {c['stiffness']} Hz, damping {c['damping']}, drag {c['drag']}, "
                     f"gravity {c['gravity']}.")
    lines += ["",
              "The sway is baked into the character's own moves in the FBX and every GLB, so it plays with nothing to "
              "install. It replays the same way every time; it does not react to wind, sudden stops or blends between moves.",
              "springs.json lists the chains, their settings and the hit boxes as the shapes they must not pass through "
              "(glTF model space, Y up, metres).",
              "",
              "Unity: the importer adds VgacSpringBones (Assets/VgacScripts/VgacSpringBones.cs) to the prefab, so the chains "
              "sway during the shared Humanoid moves as well and react to the game.",
              "",
              "Godot 4.4 and later (not tried here): to make the sway react to the game, add a SpringBoneSimulator3D node "
              "as a child of the Skeleton3D. For each chain in springs.json add a setting with the first bone as Root Bone "
              "and the last bone as End Bone, End Bone Direction from the parent bone, Extend End Bone on with its length "
              "set to the last bone's length. Use Stiffness about 2, Drag about 0.4 and Gravity about 0.2 as a start, "
              "and add the hit boxes as SpringBoneCollision3D capsules (children of the simulator, attached to the bones "
              "named in springs.json). The node then overrides the baked sway on those bones.",
              "",
              "Unreal Engine (not tried here): in the character's Animation Blueprint add one AnimDynamics node per chain "
              "(Chain on, Bound Bone the first bone, Chain End the last bone), or a Spring Controller per bone for a "
              "simpler bounce, after the Locomotion pose. Add the hit boxes as Planar or sphere limits if a chain cuts "
              "into the body. The baked sway plays without any of this.",
              ""]
    return lines
