"""Write a small valid GLB to library/samples/sample.glb for the viewer to load.

Run from the backend folder: .venv\\Scripts\\python scripts\\make_sample_glb.py
"""

from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "library" / "samples" / "sample.glb"


def main() -> None:
    body = trimesh.creation.box(extents=(1.0, 1.4, 0.6))
    body.apply_translation((0, 0.7, 0))
    head = trimesh.creation.icosphere(subdivisions=2, radius=0.35)
    head.apply_translation((0, 1.75, 0))
    base = trimesh.creation.cylinder(radius=0.9, height=0.1, sections=32)
    # trimesh cylinders run along Z; lay it flat so Y is up like the rest of the scene.
    base.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))
    base.apply_translation((0, -0.05, 0))

    for mesh, rgba in ((body, (90, 140, 200, 255)), (head, (220, 180, 140, 255)), (base, (120, 120, 120, 255))):
        mesh.visual.vertex_colors = np.tile(np.array(rgba, dtype=np.uint8), (len(mesh.vertices), 1))

    scene = trimesh.Scene()
    scene.add_geometry(body, node_name="body")
    scene.add_geometry(head, node_name="head")
    scene.add_geometry(base, node_name="base")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = scene.export(file_type="glb")
    OUT.write_bytes(data)
    print("wrote", OUT, len(data), "bytes")


if __name__ == "__main__":
    main()
