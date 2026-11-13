"""Offline, read-only: runs step 2's pose_held check (stages/turnaround.py) over the stored views of the realistic test
(projects "RCT Realistic PC", "RCT Realistic Showcase", "RCT2 Realistic PC" in library/vgac.sqlite) and lists which
front and back views it would flag, with the arm and leg reach of each view as a share of the picture's. Also prints the
side and three-quarter ratios for information (they are not checked).

    cd backend && .venv/Scripts/python.exe ../tools/verify_pose_held.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import verify_face_d2 as base  # noqa: E402

from vgac import imaging  # noqa: E402
from vgac.stages import turnaround  # noqa: E402

LIB = base.LIB


def main() -> None:
    flagged = total = 0
    for project in ("RCT Realistic PC", "RCT Realistic Showcase", "RCT2 Realistic PC"):
        for name, st in base.runs(project).items():
            files = st["turnaround"]["files"]
            ref = imaging.pose_extents(LIB / st["concept"]["files"]["cutout"])
            line = f"{project[:6]:7s}{name:20s}"
            for view in ("front", "back", "quarter", "quarter2", "left", "right"):
                ext = imaging.pose_extents(LIB / files[f"{view}_cutout"])
                pc = turnaround.pose_check(view, ext, ref)
                if view in turnaround.POSE_VIEWS:
                    total += 1
                    flagged += not pc["ok"]
                    line += f" | {view} arm {pc['value']:.2f} leg {pc['leg_ratio']:.2f} {'FLAG' if not pc['ok'] else 'ok'}"
                else:
                    line += f" | {view} arm {pc['value']:.2f}"
            print(line)
    print(f"flagged {flagged} of {total} front and back views")


if __name__ == "__main__":
    main()
