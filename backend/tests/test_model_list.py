"""The model list stays complete: every model file a graph (or other code) names is on the list, with a source, a size, a
SHA-256 and a destination folder that matches the sub-folder the graph names (ComfyUI matches the exact relative name).

The list is setup/downloads.json (where each file comes from) plus backend/vgac/data/models.yaml (`models:` and
`companions:`, which the setup check reads) plus data/addons.yaml (the LoRAs). If this test fails because a graph got a
new model file: add the file to downloads.json with its size and SHA-256 from the Hugging Face file page, add it to
models.yaml, and add a loader line to preflight.MODEL_LOADERS (or say why not, in the comment there).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from vgac import preflight

BACKEND = Path(__file__).resolve().parent.parent
PROJECT = BACKEND.parent
VGAC = BACKEND / "vgac"
DOWNLOADS = json.loads((PROJECT / "setup" / "downloads.json").read_text(encoding="utf-8"))
MODELS_YAML = yaml.safe_load((VGAC / "data" / "models.yaml").read_text(encoding="utf-8"))
ADDONS_YAML = yaml.safe_load((VGAC / "data" / "addons.yaml").read_text(encoding="utf-8"))

# Source files that name ComfyUI model files.
SOURCES = sorted((VGAC / "graphs").glob("*.py")) + [VGAC / "stages" / "concept.py", VGAC / "facemarks.py"]

# Names in the code that no graph can reach today. Each must stay a known, written-down exception.
DEAD_REFERENCES = {
    # anima_graph(turbo=False); nothing calls it that way (workflows.py and the concept stage use the default turbo=True).
    "anima-aesthetic-v1.1.safetensors",
}
# models.yaml / companions that are deliberately not asked of an engine (comments in preflight.MODEL_LOADERS say why).
NOT_ASKED = {"skintoken_vae", "face_model"}
# Companions whose file the graphs do not name directly (the node pack loads it).
NODE_PACK_FILES = {"last.ckpt"}

EXT = r"(?:safetensors|pth|ckpt)"
_REF = re.compile(r'(?:(?:"(?P<lit>[^"\\\n]+)"|\b(?P<var>Q|VGAC))\s*\+\s*(?:BS\s*\+\s*)?)?"(?P<file>[^"\\\n]+?\.' + EXT + r')"')
_PREFIX_VARS = {"Q": "Qwen", "VGAC": "VGAC"}


def referenced() -> list[tuple[str, str, str]]:
    """[(source file name, sub-folder the graph names or "", base file name)] for every model file the code names."""
    out = []
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        for m in _REF.finditer(text):
            sub = m.group("lit") or _PREFIX_VARS.get(m.group("var") or "", "")
            if sub and re.search(r"\.(?:safetensors|pth|ckpt)$", sub):  # a literal that is itself a file name
                sub = ""
            name = m.group("file").replace("\\", "/")
            if "/" in name:
                name = name.rsplit("/", 1)[-1]
            out.append((path.name, sub, name))
    return out


def _slash(s: str) -> str:
    return s.replace("\\", "/").strip("/")


def listed_downloads() -> dict[str, list[dict]]:
    """base file name -> the downloads.json entries that save under it (models and addons; by_hand entries have none)."""
    out: dict[str, list[dict]] = {}
    for section in ("models", "addons"):
        for key, d in DOWNLOADS[section].items():
            if d.get("save_as"):
                out.setdefault(d["save_as"], []).append({"id": key, **d})
    return out


def dest_subfolder(d: dict) -> str:
    """The folder below ComfyUI's model type folder that downloads.json puts the file in ("" for the type folder)."""
    parts = _slash(d["dest"]).split("/")
    if parts and parts[-1].casefold() == d["save_as"].casefold():
        parts = parts[:-1]
    return "/".join(parts[1:])


def test_the_scan_finds_the_known_references():
    names = {n for _s, _p, n in referenced()}
    # a floor, so a broken regex cannot make the real test pass by finding nothing
    for must in ("z-image-turbo-fp8-aio.safetensors", "qwen_2.5_vl_7b_fp8_scaled.safetensors", "qwen_image_vae.safetensors",
                 "trellis_2_shape_vae_bf16.safetensors", "dino_v3_vit_l.safetensors", "grpo_1400.ckpt", "ae.safetensors",
                 "4xRealWebPhoto_v4_dat2.pth", "RealESRGAN_x2plus.pth", "mediapipe_face_fp32.safetensors",
                 "qie2511_turnaround_sheet_tarn59.safetensors", "Hunyuan3D-2mv.fp16.safetensors", "gemma_3_12B_it.safetensors"):
        assert must in names, must
    assert len(names) >= 25


def test_every_file_the_code_names_is_in_downloads_json():
    have = listed_downloads()
    missing = sorted({n for _s, _p, n in referenced() if n not in have and n not in DEAD_REFERENCES})
    assert not missing, f"named by the code but not in setup/downloads.json: {missing}"


def test_every_file_the_code_names_is_in_models_yaml_or_addons_yaml():
    listed = {str(m.get("file")) for m in (MODELS_YAML["models"] | MODELS_YAML["companions"]).values() if m.get("file")}
    listed |= {Path(a["file"]).name for a in ADDONS_YAML["addons"] if a.get("file")}
    missing = sorted({n for _s, _p, n in referenced() if n not in listed and n not in DEAD_REFERENCES})
    assert not missing, f"named by the code but not in models.yaml or addons.yaml: {missing}"


def test_the_download_folder_matches_the_sub_folder_the_graph_names():
    have = listed_downloads()
    bad = []
    for src, sub, name in referenced():
        if not sub or name not in have:
            continue
        if not any(dest_subfolder(d).casefold() == _slash(sub).casefold() for d in have[name]):
            bad.append(f"{src}: {sub}/{name} but downloads.json puts it in {[dest_subfolder(d) for d in have[name]]}")
    assert not bad, bad


def test_every_models_yaml_file_has_a_download_entry():
    have = listed_downloads()
    by_id = {**DOWNLOADS["models"], **DOWNLOADS["addons"]}
    missing = []
    for section in ("models", "companions"):
        for mid, m in MODELS_YAML[section].items():
            f = str(m.get("file") or "")
            if not f.lower().endswith((".safetensors", ".pth", ".ckpt")):
                continue  # tools and packs (Blender, Unity, animation packs, the hair library) are listed elsewhere
            d = by_id.get(mid) or next(iter(have.get(f, [])), None)
            if not d or d.get("save_as") != f:
                missing.append(f"{section}.{mid}: {f}")
    assert not missing, f"in models.yaml but not in downloads.json under that name: {missing}"


def test_every_entry_has_a_source_a_size_and_a_hash():
    bad = []
    for section in ("models", "addons"):
        for key, d in DOWNLOADS[section].items():
            if d.get("by_hand"):
                continue
            ok = (str(d.get("url", "")).startswith("https://") and str(d.get("page", "")).startswith("https://")
                  and isinstance(d.get("size"), int) and d["size"] > 0 and re.fullmatch(r"[0-9a-f]{64}", str(d.get("sha256", "")))
                  and d.get("save_as") and d.get("dest"))
            if not ok:
                bad.append(f"{section}.{key}")
    for key, d in DOWNLOADS["project_files"].items():
        if not (d["url"].startswith("https://") and d["size"] > 0 and re.fullmatch(r"[0-9a-f]{64}", d["sha256"]) and d["dest"] and d["save_as"]):
            bad.append(f"project_files.{key}")
    assert not bad, bad


def test_every_companion_is_checked_by_the_setup_check():
    companions = set(MODELS_YAML["companions"])
    assert not (companions - set(preflight.MODEL_LOADERS) - NOT_ASKED), companions - set(preflight.MODEL_LOADERS) - NOT_ASKED
    # every checked id is a real models.yaml entry with a file, and the loader names a stage the graphs use
    models = preflight.load_models()
    for mid in preflight.MODEL_LOADERS:
        assert models.get(mid, {}).get("file"), mid
    for mid, m in MODELS_YAML["companions"].items():
        for user in m.get("used_by", []):
            assert user in MODELS_YAML["models"] or user in {"judge", "lip points"} or user in MODELS_YAML["companions"], (mid, user)


def test_companions_do_not_leak_into_the_concept_list_or_credits():
    assert not any(m.get("concept") for m in MODELS_YAML["companions"].values())
    assert set(MODELS_YAML["models"]).isdisjoint(MODELS_YAML["companions"])


def test_every_listed_file_is_used_by_something():
    """A file on the list that no graph or code names is dead weight for the person downloading it (the installer's
    sets are built from this list). Entries the node packs, the app or the add-on registry load are named here."""
    used = {n for _s, _p, n in referenced()}
    used |= {Path(a["file"]).name for a in ADDONS_YAML["addons"] if a.get("file")}
    used |= NODE_PACK_FILES
    unused = sorted(d["save_as"] for sec in ("models", "addons") for d in DOWNLOADS[sec].values() if d.get("save_as") and d["save_as"] not in used)
    assert not unused, f"on the list but no graph or code names them: {unused}"
