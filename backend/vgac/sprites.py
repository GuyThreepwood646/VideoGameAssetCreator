"""Pack the frames rendered by blender/sprites.py into one sheet per clip plus atlas.json (bakeoff/pack_sprites.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image


def pack_sprites(out_dir: Path | str) -> dict[str, Any]:
    d = Path(out_dir)
    atlas = json.loads((d / "atlas_frames.json").read_text(encoding="utf-8"))
    size = atlas["size"]
    out: dict[str, Any] = {"size": size, "directions": atlas["directions"], "clips": {}}
    for name, clip in atlas["clips"].items():
        sheet = Image.new("RGBA", (size * clip["cols"], size * clip["rows"]), (0, 0, 0, 0))
        for e, f in zip(clip["entries"], clip["files"]):
            sheet.paste(Image.open(f).convert("RGBA"), (e["x"], e["y"]))
        sheet_path = d / f"{name}.png"
        sheet.save(sheet_path)
        out["clips"][name] = {"sheet": sheet_path.name, "rows": clip["rows"], "cols": clip["cols"], "frames": clip["entries"]}
    (d / "atlas.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
