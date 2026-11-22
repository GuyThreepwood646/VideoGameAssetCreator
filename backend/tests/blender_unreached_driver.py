"""Run inside Blender by tests/test_wp86_92_checks.py: two planes, the source covering `--cover` of the target's width.
Prints UNREACHED_JSON {...} from rebake.measure_unreached with the real bakes' ray settings."""
import json
import os
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
cover = float(argv[0])
sys.path.insert(0, argv[1])
import rebake  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 1


def plane(name, x0, x1):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x0, -0.5, 0), (x1, -0.5, 0), (x1, 0.5, 0), (x0, 0.5, 0)], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    for i, c in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
        uv.data[i].uv = c
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    return ob


target = plane("target", -0.5, 0.5)
source = plane("source", -0.5, -0.5 + cover)
source.location.z = 0.001
bake = scene.render.bake
bake.use_cage = False
bake.cage_extrusion = 0.01
bake.max_ray_distance = 0.02
print("UNREACHED_JSON " + json.dumps(rebake.measure_unreached(source, target, 128)))
