"""No export writes an Unreal file or an Unreal manifest entry (PL-46: the Unreal output was removed; Unity comes first).

A source-level check: the modules that build the files, the manifest and the delivered keys carry no Unreal output
name. Unreal may return later, with a way to test it first.
"""

import re
from pathlib import Path

from vgac.stages import export

ROOT = Path(__file__).resolve().parents[1]
BUILDERS = ("vgac/blender/pack.py", "vgac/stages/export.py", "vgac/library.py", "vgac/items.py", "vgac/springs.py",
            "vgac/hairmat.py")
UNREAL_OUTPUT = re.compile(r"_unreal|UCX_|unreal_collision|[\"']unreal[\"']|UNREAL_")


def test_no_export_writes_an_unreal_file():
    assert not any("unreal" in k for k in export.DELIVER_KEYS)
    for rel in BUILDERS:
        assert not UNREAL_OUTPUT.search((ROOT / rel).read_text(encoding="utf-8")), rel
