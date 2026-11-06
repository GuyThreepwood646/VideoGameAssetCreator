Hair library test fixture (lane 1 of docs/HAIR-LIBRARY-PLAN.md). Made by tools/hairlib_build.py and backend/vgac/blender/hairfit.py; do not edit by hand.

lib/                  A two-style library in the plan's on-disk format (section 4), textures at 256 px (normals 128):
  catalogue.json        entries for q_buzzed (Quaternius, solid shell, 830 triangles, with a 750-triangle lite variant)
                        and mh_ponytail01 (MakeHuman system pack, hair cards with alpha cutout, thinned to 4,500
                        triangles, 3 hang groups); plus "refused" (styles whose own file is not CC0) and "left_out".
  curation.yaml         copy of the curation file the fixture was built from.
  styles/<id>.npz       q, rho_r, tris, uv, role, flow (and q_buzzed_lite.npz).
  tex/<sha8>_grey.png   grey LA textures (median hair luminance 0.5; alpha kept for cards); <sha8>_normal.png for the shell.
  thumbs/               256 px front and three-quarter renders on the reference head.
  CREDITS.txt           credits and the "Left out" list.
hair_mesh.npz         The attach output (section 6) of mh_ponytail01 fitted to the wig trial's bald ponytail character
                      (round two's numbers: volume 1.525, dz_head 0, length 0.65, not mirrored): verts (N,3) float32
                      glTF Y-up metres in the bald mesh's own model space, tris (T,3) int32, uv (T,3,2) corner UVs,
                      role (N,) int8 (0 on the head, 1 scalp cap, 2+k hang group k), flow (N,3) float32. Hair first,
                      cap after (cap_first false).
hair_parts.json       {style, groups:[{k, kind:"Hair", path (root first, same frame as verts), length_m}], cap_first, extra_height_m}.
hair_base_color.png   The recoloured hair texture of that fit (RGBA, 256 px, alpha for cutout).
