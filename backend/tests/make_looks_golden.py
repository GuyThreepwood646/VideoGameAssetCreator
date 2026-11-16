"""Writes tests/data/looks_golden.json: for the looks this branch must leave alone, what reference.recipe() and the step 3
and step 4 decisions give on the code it runs against. Run once on master's code (VGAC_CODE=<master backend>) to make
the fixture tests/test_looks_lowtex.py compares the branch with (paint standard D1: nothing changes for other looks).

usage: python tests/make_looks_golden.py [out.json]"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CODE = os.environ.get("VGAC_CODE") or str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, CODE)

UNCHANGED = ("realistic_pbr", "painted", "cel", "clay", "pixel")
PREP_PARAMS = ({}, {"face_detail": False}, {"source_project": True}, {"shading": "flat"}, {"head_triangles": False})


def decisions() -> dict:
    from vgac import headtris, reference, srcproject
    from vgac.stages import face_detail, mesh, prep

    out: dict = {}
    for style in UNCHANGED:
        for cat in [c["id"] for c in reference.categories()]:
            for plat in [p["id"] for p in reference.platforms()]:
                try:
                    rec = reference.recipe(style, cat, plat)
                except KeyError:
                    continue
                budget = reference.budget(cat, plat)
                targets = mesh.resolve_targets(rec, {}, budget, cat)
                mp = {**targets, "target_faces_source": targets["target_faces_source"]}
                look = prep.texture_look(rec, None)
                row = {"recipe_id": rec["id"], "mesh": rec["mesh"], "surface": rec["surface"], "targets": targets,
                       "head_applies": headtris.applies(rec, cat), "triangle_limit": prep.prep_triangle_limit(rec, mp, budget),
                       "texture_look": look, "srcproject_on": srcproject.on({}, cat, look),
                       "face_skip": face_detail.skip_reason(cat, rec, {}, True),
                       "plans": [headtris.plan(rec, cat, p, prep.resolve_shading(rec, p)["shading"], mp) for p in PREP_PARAMS]}
                out[f"{style}|{cat}|{plat}"] = row
    return out


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "data" / "looks_golden.json"
    d = decisions()  # one line per look, category and platform, so a change shows as one line in a review
    lines = [f"{json.dumps(k)}: {json.dumps(d[k], sort_keys=True, default=str)}" for k in sorted(d)]
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{\n" + ",\n".join(lines) + "\n}\n")
    print("wrote", dest)
