# workflows

ComfyUI API-format JSON graphs, one file per engine, as `POST /prompt` accepts them. They are written from the
graph builders in `backend/vgac/graphs/` with default parameters by

    cd backend
    .venv\Scripts\python -m vgac.workflows

and are reference copies: the backend builds graphs in code at run time (prompt, seed, input image name and
output prefix substituted), so edit the builder and regenerate rather than editing these files.
`backend/tests/test_graphs.py` checks that the files match the builders and that the builders match the bake-off
scripts they were ported from.

| File | Builder | Instance | Ported from |
|---|---|---|---|
| `zimage_concept.json` | `graphs.concept.zimage_graph` | B | `bakeoff/gen_concepts.py` |
| `zbase_concept.json`, `klein_concept.json`, `anima_concept.json`, `pixel_concept.json`, `chroma_concept.json` | `graphs.concept.*` | B | comfyui-game-art skill `stylemodels.py` |
| `qwen_angles.json` | `graphs.qwen_edit.angle_graph` | B | `bakeoff/turnaround.py` |
| `qwen_edit.json` | `graphs.qwen_edit.edit_graph` (angles LoRA removed) | B | `bakeoff/turnaround.py` |
| `birefnet_cutout.json` | `graphs.birefnet.cutout_graph` | B | `bakeoff/turnaround.py` |
| `trellis_convert.json` | `graphs.trellis.convert_graph` | A | `bakeoff/convert.py` |
| `hy3d_mv.json` | `graphs.hy3d.hy3d_graph` | A | `bakeoff/mesh.py` |
| `skintoken_rig.json` | `graphs.skintoken.rig_graph` | B | `bakeoff/rig.py` |
| `material_surface.json` | `graphs.material.t2i_graph` | B | `bakeoff/material.py` |
| `judge_local.json` | `graphs.judge.judge_graph` | B | `bakeoff/judge.py` |

Model file names use the Windows subfolders of this machine's ComfyUI model folders (for example
`Z-Image\z-image-turbo-fp8-aio.safetensors`).
