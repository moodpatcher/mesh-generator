"""Step 5, runs INSIDE Blender (blender -b --factory-startup --python blender_anim.py -- task.json): renders one cycle of
a clip of the rigged model, walking in place, from a few views, as PNG frames (make_gif.py joins them). Task: {glb,
out (a folder), clip ("walk-loop"), frames (30), size ([w, h] per view), views ({name: yaw degrees})}.
Workbench render with the model's own texture: fast, flat and honest (what a PS1/PS2 engine would show), so a bad
weight or a bad bone shows up as it is. Uses only Blender's own Python."""

import json
import math
import os
import sys
import traceback

import bpy
from mathutils import Vector

VIEWS = {"three-quarter": 35.0, "side": 90.0}  # yaw from the front; the front of the model faces -Y in Blender


def render(task):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=task["glb"])
    arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    mesh = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
    if arm is None or mesh is None:
        raise RuntimeError("the model has no armature / mesh: nothing to animate")
    clip = task.get("clip", "walk-loop")
    act = next((a for a in bpy.data.actions if a.name == clip or a.name.endswith(clip)), None)
    if act is None:
        raise RuntimeError(f"no clip '{clip}' (clips: {[a.name for a in bpy.data.actions]})")
    arm.animation_data_create()
    arm.animation_data.action = act
    f0, f1 = act.frame_range
    pts = [mesh.matrix_world @ v.co for v in mesh.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    height = hi.z - lo.z
    center = (lo + hi) / 2

    scene = bpy.context.scene
    w, h = task.get("size", [400, 560])
    scene.render.resolution_x, scene.render.resolution_y = w, h
    # Cycles on the CPU, a few samples, the same light as blender_check.py: the Workbench engine needs OpenGL, which a
    # headless container emulates in software (8 minutes for 60 frames); this takes about a second a frame
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 12
    scene.cycles.use_denoising = False
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.16, 0.17, 0.2, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
    bpy.ops.mesh.primitive_plane_add(size=40 * height, location=(center.x, center.y, lo.z))
    floor = bpy.context.active_object
    fm = bpy.data.materials.new("floor")
    fm.use_nodes = True
    fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.09, 0.09, 0.1, 1)
    floor.data.materials.append(fm)
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = height * 1.25
    scene.collection.objects.link(cam)
    scene.camera = cam

    os.makedirs(task["out"], exist_ok=True)
    n = int(task.get("frames", 30))
    target = Vector((center.x, center.y, lo.z + height * 0.52))
    for name, deg in task.get("views", VIEWS).items():
        a = math.radians(deg)
        cam.location = target + Vector((math.sin(a), -math.cos(a), 0.12)) * height * 4
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        sun.rotation_euler = (math.radians(50), 0, a + math.radians(-30))
        for i in range(n):  # one cycle: the end of the clip equals its start, so it is left out
            fr = f0 + (f1 - f0) * i / n
            scene.frame_set(int(math.floor(fr)), subframe=fr - math.floor(fr))
            scene.render.filepath = os.path.join(task["out"], f"{name}-{i:04d}.png")
            bpy.ops.render.render(write_still=True)
    return {"clip": clip, "frames": n, "views": list(task.get("views", VIEWS)), "clip_seconds": round((f1 - f0) / scene.render.fps, 3)}


def main():
    path = sys.argv[sys.argv.index("--") + 1]
    task = json.loads(open(path, encoding="utf-8").read())
    try:
        result = {"ok": True, **render(task)}
    except Exception:
        result = {"ok": False, "error": traceback.format_exc(limit=8)[-3000:]}
    with open(path + ".out.json", "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
