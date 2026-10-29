"""Step 4, runs INSIDE Blender (blender -b --factory-startup --python blender_check.py -- task.json): measurements
and pictures of the finished model. Task: {glb, reference (keyed PNG), out (folder), close: [height fractions]}.

Measures: triangle count, height, the soles (min Z), the offset from the horizontal center, open / non-manifold
edges (welded), texture size, texel density (px/m), the outline match with the reference (front, IoU) and from all
four sides. Renders: front, three-quarter, side, back - the full figure, plus close-ups at the close fractions
(e.g. 0.5 = hips, 0.72 = shoulders, 0.9 = head). The result: <task>.out.json."""

import json
import math
import os
import sys
import traceback

import bmesh
import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_post import iou, mesh_arrays, ref_mask, silhouette, VIEWS  # noqa: E402

SHOTS = {"front": 0, "three-quarter": 35, "side": 90, "back": 180}


def measure(meshes, ref_path, scan=False):
    obj = meshes[0]
    if len(meshes) > 1:
        for o in bpy.context.scene.objects:
            o.select_set(o in meshes)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.join()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    co, tri = mesh_arrays(obj)
    lo, hi = co.min(0), co.max(0)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bad = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    me = obj.data
    area = sum(p.area for p in me.polygons)
    uv_area, tex = 0.0, []
    uvl = me.uv_layers.active
    for slot in obj.material_slots:
        m = slot.material
        if m and m.use_nodes:
            tex += [tuple(n.image.size) for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]
    if uvl is not None:
        uv = np.empty(len(me.loops) * 2, np.float64)
        uvl.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        lt = np.empty(len(me.loop_triangles) * 3, np.int64)
        me.loop_triangles.foreach_get("loops", lt)
        lt = lt.reshape(-1, 3)
        a, b = uv[lt[:, 1]] - uv[lt[:, 0]], uv[lt[:, 2]] - uv[lt[:, 0]]
        uv_area = float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2)
    texels = uv_area * (tex[0][0] * tex[0][1] if tex else 0)
    out = {"triangles": int(len(tri)), "height_m": round(float(hi[2] - lo[2]), 4),
           "min_z_m": round(float(lo[2]), 4),
           "center_offset_m": round(float(np.hypot((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)), 4),
           "width_m": round(float(hi[0] - lo[0]), 4), "depth_m": round(float(hi[1] - lo[1]), 4),
           "open_or_nonmanifold_edges": int(bad), "textures": sorted(set(tex)),
           "texel_density_px_per_m": round(math.sqrt(texels / area), 1) if area > 0 else 0.0}
    if ref_path:
        ref = ref_mask(ref_path)
        out["view_iou"] = {v: round(iou(silhouette(co, tri, r, (0, 0, 1)), ref), 3) for v, (r, _) in VIEWS.items()}
        out["silhouette_iou"] = out["view_iou"]["front"]
        if scan:
            out["best_view"] = best_view(co, tri, ref)
    return obj, out


def best_view(co, tri, ref):
    """The best outline match over all viewing angles: yaw every 15 degrees, pitch 0 and 20 degrees from above. An
    object reference is a three-quarter view from slightly above, so the front IoU alone undervalues a good model."""
    best = {"iou": -1.0}
    for pitch in (0, 20):
        for yaw in range(0, 360, 15):
            y, p = math.radians(yaw), math.radians(pitch)
            f = -np.array([math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)])  # camera forward
            r = np.cross(f, (0, 0, 1))
            r /= np.linalg.norm(r)
            u = np.cross(r, f)
            s = iou(silhouette(co, tri, r, u), ref)
            if s > best["iou"]:
                best = {"iou": round(s, 3), "yaw_deg": yaw, "pitch_deg": pitch}
    return best


def render(obj, out_dir, close):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    center, height = (lo + hi) / 2, max(hi.z - lo.z, 1e-6)
    for slot in obj.material_slots:  # sharp texels, like on a PS1/PS2
        if slot.material and slot.material.use_nodes:
            for n in slot.material.node_tree.nodes:
                if n.type == "TEX_IMAGE":
                    n.interpolation = "Closest"
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    scene.render.resolution_x, scene.render.resolution_y = 720, 960
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
    cam.data.lens = 70
    scene.collection.objects.link(cam)
    scene.camera = cam
    images = []
    for tag, at in [("full", None)] + [(f"closeup-{int(round(c * 100))}", c) for c in close]:
        dist = height * (0.7 if at else 3.4)
        target = Vector((center.x, center.y, lo.z + height * (at if at else 0.5)))
        for name, deg in SHOTS.items():
            a = math.radians(deg)
            cam.location = target + Vector((math.sin(a) * dist, -math.cos(a) * dist, height * 0.25 * (0.4 if at else 1)))
            cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
            sun.rotation_euler = (math.radians(50), 0, a + math.radians(-30))
            path = os.path.join(out_dir, f"{tag}-{name}.png")
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            images.append(path)
    return images


def _load(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)  # bottom row first, like Blender


def _fit(a, h, bg=(0.16, 0.17, 0.2)):
    """a resized to height h (nearest neighbor), composited over bg."""
    k = h / a.shape[0]
    w = max(1, round(a.shape[1] * k))
    yi = np.minimum((np.arange(h) / k).astype(int), a.shape[0] - 1)
    xi = np.minimum((np.arange(w) / k).astype(int), a.shape[1] - 1)
    a = a[yi][:, xi]
    al = a[..., 3:4]
    out = np.ones_like(a)
    out[..., :3] = a[..., :3] * al + np.array(bg, np.float32) * (1 - al)
    return out


def sheets(out_dir, images, ref_path):
    """One picture per shot group: the four views side by side; the full figure also with the reference in front."""
    groups = {}
    for p in images:  # <tag>-<view>.png, in the SHOTS order
        name = os.path.basename(p)[:-4]
        view = next(v for v in SHOTS if name.endswith("-" + v))
        groups.setdefault(name[:-len(view) - 1], []).append(p)
    made = []
    for tag, paths in groups.items():
        views = [_load(p) for p in paths]
        h = views[0].shape[0]
        row = ([_fit(_load(ref_path), h)] if tag == "full" and ref_path else []) + views
        sheet = np.concatenate(row, axis=1)
        img = bpy.data.images.new(f"sheet-{tag}", sheet.shape[1], sheet.shape[0], alpha=False)
        img.pixels.foreach_set(sheet.reshape(-1))
        path = os.path.join(out_dir, f"sheet-{tag}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        made.append(path)
    return made


def run(task):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=task["glb"])
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("the model has no mesh")
    for o in bpy.context.scene.objects:
        o.select_set(o in meshes)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    obj, out = measure(meshes, task.get("reference"), bool(task.get("scan_views")))
    os.makedirs(task["out"], exist_ok=True)
    out["images"] = render(obj, task["out"], task.get("close", []))
    out["sheets"] = sheets(task["out"], out["images"], task.get("reference"))
    return out


def main():
    path = sys.argv[sys.argv.index("--") + 1]
    task = json.loads(open(path, encoding="utf-8").read())
    try:
        result = {"ok": True, **run(task)}
    except Exception:
        result = {"ok": False, "error": traceback.format_exc(limit=8)[-3000:]}
    with open(path + ".out.json", "w", encoding="utf-8") as f:
        json.dump(result, f)


main()
