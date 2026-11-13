"""Build the hair library from the CC0 hair packs by hand (the app does the same from its "Get the hair library" screen).

    backend/.venv/Scripts/python.exe -I tools/hairlib_build.py --packs runtime/hairpacks [--out DIR] [--blender EXE] ...

A thin command line over backend/vgac/hairlib_builder.py, where the code lives."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from vgac.hairlib_builder import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["build", *sys.argv[1:]]))
