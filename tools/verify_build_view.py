"""Offline, read-only: for the realistic test's stored runs ("RCT Realistic PC", "RCT Realistic Showcase", "RCT2 Realistic
PC" in library/vgac.sqlite), measures the pose of the `quarter` and `quarter2` views against the person's picture
(turnaround.pose_check with the three-quarter limits) and shows which view the 3D step would be built from
(stages/mesh.pick_build_view). Expected: only M1-clothed loses the pose in `quarter` and switches to `quarter2`.

    cd backend && .venv/Scripts/python.exe ../tools/verify_build_view.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import verify_face_d2 as base  # noqa: E402

from vgac import imaging  # noqa: E402
from vgac.stages import mesh, turnaround  # noqa: E402

LIB = base.LIB


def main() -> None:
    switched = []
    for project in ("RCT Realistic PC", "RCT Realistic Showcase", "RCT2 Realistic PC"):
        for name, st in base.runs(project).items():
            files = st["turnaround"]["files"]
            ref = imaging.pose_extents(LIB / st["concept"]["files"]["cutout"])
            c = {v: turnaround.pose_check(v, imaging.pose_extents(LIB / files[f"{v}_cutout"]), ref) for v in turnaround.THREEQ_VIEWS}
            view, why = mesh.pick_build_view(c["quarter"], c["quarter2"])
            if view != "quarter":
                switched.append(name)
            print(f"{project[:6]:7s}{name:20s} quarter arm {c['quarter']['value']:.2f} leg {c['quarter']['leg_ratio']:.2f} "
                  f"{'ok  ' if c['quarter']['ok'] else 'FAIL'} | quarter2 arm {c['quarter2']['value']:.2f} leg {c['quarter2']['leg_ratio']:.2f} "
                  f"{'ok  ' if c['quarter2']['ok'] else 'FAIL'} -> build from {view} ({why})")
    print("switched to quarter2:", switched)


if __name__ == "__main__":
    main()
