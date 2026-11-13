"""Check a built hair library folder.

    backend/.venv/Scripts/python.exe -I tools/hairlib_verify.py <library folder> [--against <other library folder>]

A thin command line over backend/vgac/hairlib_builder.py (verify and compare). Exit 0 when everything passes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from vgac.hairlib_builder import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["verify", *sys.argv[1:]]))
