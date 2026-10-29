"""Step 3b, runs INSIDE Blender (blender -b --factory-startup --python blender_rig.py -- task.json): the finished static
model -> the same model with a humanoid skeleton, skin weights and two basic clips. Task: {op: "rig", glb, out, names
("mixamo" | "godot"), bone_prefix}.
  1. import model.glb (A-pose character, front +Z, soles at Y = 0, real meters);
  2. skeleton: 22 bones (hips, 3 spine, neck, head, and per side: shoulder, arm, forearm, hand, upleg, leg, foot, toe),
     placed from the mesh itself - the torso width, the hand tips, the leg centers - with body proportions as the
     fallback. Fingers are not rigged (the hand is one bone);
  3. weights: Blender's automatic (heat) weights, then at most 4 bones per vertex, normalized; a vertex the heat
     solver left without weight goes to its nearest bone;
  4. checks: unweighted vertices, max influences, and a pose test (the left arm and the left leg are rotated; any
     vertex of the other side that moves is a leak);
  5. two procedural clips, "idle-loop" and "walk-loop" (30 fps, the last frame equals the first), so a game engine
     that plays the clips of the model file has something to play;
  6. GLB export (Y up) with the skin and the clips.
The result goes next to the task (<task>.out.json). Uses only Blender's own Python (numpy)."""

import json
import math
import sys
import traceback

import bmesh
import bpy
import numpy as np
from mathutils import Quaternion, Vector

FPS = 30

# key -> (parent, connected to the parent's tail). The keys are the Mixamo names without the prefix.
CHAIN = [("Hips", None, False), ("Spine", "Hips", True), ("Spine1", "Spine", True), ("Spine2", "Spine1", True),
         ("Neck", "Spine2", True), ("Head", "Neck", True)]
for _s in ("Left", "Right"):
    CHAIN += [(_s + "Shoulder", "Spine2", False), (_s + "Arm", _s + "Shoulder", True),
              (_s + "ForeArm", _s + "Arm", True), (_s + "Hand", _s + "ForeArm", True),
              (_s + "UpLeg", "Hips", False), (_s + "Leg", _s + "UpLeg", True), (_s + "Foot", _s + "Leg", True),
              (_s + "ToeBase", _s + "Foot", True)]

GODOT = {"Spine1": "Chest", "Spine2": "UpperChest", "Arm": "UpperArm", "ForeArm": "LowerArm", "UpLeg": "UpperLeg",
         "Leg": "LowerLeg", "ToeBase": "Toes"}


def bone_name(key, names, prefix):
    if names == "godot":
        for mix, god in GODOT.items():
            if key.endswith(mix) and (key == mix or key[:-len(mix)] in ("Left", "Right")):
                key = key[:-len(mix)] + god
                break
        return key
    return prefix + key


def _component_halfwidth(xs, gap):
    """Half-width of the connected group of x values around 0 (the torso, without the arms of an A-pose)."""
    xs = np.sort(xs)
    i = int(np.argmin(np.abs(xs)))
    lo = hi = i
    while lo > 0 and xs[lo] - xs[lo - 1] < gap:
        lo -= 1
    while hi < len(xs) - 1 and xs[hi + 1] - xs[hi] < gap:
        hi += 1
    return max(abs(xs[lo]), abs(xs[hi]))


def landmarks(P):
    """Joint positions from the vertices P (N x 3, Blender: X = the character's left, front = -Y, Z up)."""
    H = float(P[:, 2].max())

    def slab(a, b):
        return P[(P[:, 2] >= a * H) & (P[:, 2] < b * H)]

    chest = slab(0.66, 0.74)
    yc = float(chest[:, 1].mean()) if len(chest) else 0.0
    half = float(np.clip(_component_halfwidth(chest[:, 0], 0.03 * H), 0.07 * H, 0.20 * H)) if len(chest) else 0.13 * H
    lm = {"yc": yc, "H": H, "half": half}
    for side, name in ((1.0, "Left"), (-1.0, "Right")):
        sh = Vector((side * half * 0.95, yc, 0.82 * H))
        arm = P[(side * P[:, 0] > half * 1.1) & (P[:, 2] < 0.88 * H)]
        if len(arm):
            tip = Vector(arm[int(np.argmax(np.linalg.norm(arm - np.array(sh), axis=1)))])
        else:
            tip = None
        if tip is None or (tip - sh).length < 0.25 * H:  # fused arms or a cut-off hand: the A-pose proportions
            tip = sh + Vector((side * 0.707, 0.0, -0.707)) * (0.44 * H)
        lm[name + "Sh"], lm[name + "Tip"] = sh, tip
        lm[name + "Elbow"] = sh.lerp(tip, 0.42)
        lm[name + "Wrist"] = sh.lerp(tip, 0.76)

        def center(a, b, side=side):
            s = slab(a, b)
            s = s[side * s[:, 0] > 0.01 * H]
            return (float(s[:, 0].mean()), float(s[:, 1].mean())) if len(s) else (side * 0.05 * H, yc)

        hx, hy = center(0.30, 0.44)
        kx, ky = center(0.25, 0.32)
        ax, ay = center(0.02, 0.07)
        lm[name + "Hip"] = Vector((hx, hy, 0.52 * H))
        lm[name + "Knee"] = Vector((kx, ky, 0.285 * H))
        lm[name + "Ankle"] = Vector((ax, ay, 0.045 * H))
        foot = P[(P[:, 2] < 0.06 * H) & (side * P[:, 0] > 0.01 * H)]
        toe_y = float(foot[:, 1].min()) if len(foot) else ay - 0.1 * H
        toe_y = min(toe_y, ay - 0.04 * H)
        lm[name + "Ball"] = Vector((ax, ay + 0.7 * (toe_y - ay), 0.02 * H))
        lm[name + "Toe"] = Vector((ax, toe_y, 0.015 * H))
    return lm


def bone_ends(lm):
    """key -> (head, tail)."""
    H, yc = lm["H"], lm["yc"]

    def V(z, x=0.0):
        return Vector((x, yc, z * H))

    e = {"Hips": (V(0.52), V(0.57)), "Spine": (V(0.57), V(0.64)), "Spine1": (V(0.64), V(0.72)),
         "Spine2": (V(0.72), V(0.82)), "Neck": (V(0.82), V(0.87)), "Head": (V(0.87), V(1.0))}
    for side, s in ((1.0, "Left"), (-1.0, "Right")):
        e[s + "Shoulder"] = (V(0.84, side * 0.02 * H / H), lm[s + "Sh"])
        e[s + "Arm"] = (lm[s + "Sh"], lm[s + "Elbow"])
        e[s + "ForeArm"] = (lm[s + "Elbow"], lm[s + "Wrist"])
        e[s + "Hand"] = (lm[s + "Wrist"], lm[s + "Tip"])
        e[s + "UpLeg"] = (lm[s + "Hip"], lm[s + "Knee"])
        e[s + "Leg"] = (lm[s + "Knee"], lm[s + "Ankle"])
        e[s + "Foot"] = (lm[s + "Ankle"], lm[s + "Ball"])
        e[s + "ToeBase"] = (lm[s + "Ball"], lm[s + "Toe"])
    return e


def build_armature(lm, names, prefix):
    arm_data = bpy.data.armatures.new("Armature")
    arm = bpy.data.objects.new("Armature", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    ends = bone_ends(lm)
    for key, parent, connect in CHAIN:
        h, t = ends[key]
        h, t = Vector(h), Vector(t)
        if (t - h).length < 1e-3:
            t = h + Vector((0.0, 0.0, 1e-3))
        eb = arm_data.edit_bones.new(bone_name(key, names, prefix))
        eb.head, eb.tail, eb.roll = h, t, 0.0
        eb.use_deform = True
        if parent:
            eb.parent = arm_data.edit_bones[bone_name(parent, names, prefix)]
            eb.use_connect = connect and (eb.parent.tail - h).length < 1e-4
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def _segment_dist(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def skin(mesh, arm):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    mesh.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    bpy.context.view_layer.objects.active = mesh
    mesh.select_set(True)
    arm.select_set(False)
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=4)
    me = mesh.data
    names = [vg.name for vg in mesh.vertex_groups]
    deform = {b.name for b in arm.data.bones}
    unweighted = [v.index for v in me.vertices if sum(g.weight for g in v.groups if mesh.vertex_groups[g.group].name in deform) <= 1e-6]
    before = len(unweighted)
    if unweighted:  # the heat solver failed there (thin / touching parts): the nearest bone takes the vertex
        segs = {b.name: (np.array(b.head_local), np.array(b.tail_local)) for b in arm.data.bones}
        pts = np.array([me.vertices[i].co for i in unweighted])
        dist = np.stack([_segment_dist(pts, a, b) for a, b in segs.values()])
        keys = list(segs)
        for n in keys:
            if n not in mesh.vertex_groups:
                mesh.vertex_groups.new(name=n)
        for col, i in enumerate(unweighted):
            mesh.vertex_groups[keys[int(np.argmin(dist[:, col]))]].add([i], 1.0, "REPLACE")
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
    unweighted_after, max_inf = 0, 0
    for v in me.vertices:
        gs = [g for g in v.groups if g.weight > 1e-6]
        max_inf = max(max_inf, len(gs))
        if sum(g.weight for g in gs) <= 1e-6:
            unweighted_after += 1
    return {"unweighted_before": before, "unweighted": unweighted_after, "max_influences": max_inf,
            "vertex_groups": len(names)}


def rot_basis(pb, axis, angle):
    """The pose rotation of a bone that turns it by `angle` about an ARMATURE axis (its parents at rest)."""
    rest = pb.bone.matrix_local.to_quaternion()
    return rest.inverted() @ Quaternion(axis, angle) @ rest


def set_rot(pb, axis, angle):
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = rot_basis(pb, axis, angle)


def reset_pose(arm):
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)


def evaluated(mesh):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh.evaluated_get(dg)
    me = ev.to_mesh()
    P = np.array([v.co for v in me.vertices])
    ev.to_mesh_clear()
    return P


def pose_test(mesh, arm, lm, P0, names, prefix):
    """Rotate the left arm and the left leg: vertices of the other side that follow are leaks."""
    H, half = lm["H"], lm["half"]
    pb = arm.pose.bones
    reset_pose(arm)
    set_rot(pb[bone_name("LeftArm", names, prefix)], (1, 0, 0), math.radians(60))
    set_rot(pb[bone_name("LeftUpLeg", names, prefix)], (1, 0, 0), math.radians(45))
    bpy.context.view_layer.update()
    P1 = evaluated(mesh)
    moved = np.linalg.norm(P1 - P0, axis=1) > 0.01
    right_arm = (P0[:, 0] < -half * 1.1) & (P0[:, 2] > 0.45 * H)
    right_leg = (P0[:, 0] < -0.01 * H) & (P0[:, 2] < 0.45 * H)
    left_moved = int((moved & (P0[:, 0] > 0)).sum())
    leak = int((moved & (right_arm | right_leg)).sum())
    reset_pose(arm)
    bpy.context.view_layer.update()
    return {"left_moved": left_moved, "leak_vertices": leak}


def make_clips(arm, lm, names, prefix):
    scene = bpy.context.scene
    scene.render.fps = FPS
    H = lm["H"]
    pb = arm.pose.bones
    X = (1, 0, 0)
    if not arm.animation_data:
        arm.animation_data_create()

    def P(key):
        return pb[bone_name(key, names, prefix)]

    def key_all(act, n, pose):
        arm.animation_data.action = act
        for f in range(n + 1):
            reset_pose(arm)
            pose(2 * math.pi * f / n)
            for b in arm.pose.bones:
                b.keyframe_insert("rotation_quaternion", frame=f + 1)
                if b.location.length > 0:
                    b.keyframe_insert("location", frame=f + 1)

    def idle(ph):
        s = math.sin(ph)
        set_rot(P("Spine1"), X, math.radians(1.5) * s)
        set_rot(P("Spine2"), X, math.radians(1.0) * s)
        set_rot(P("Head"), X, -math.radians(1.0) * s)
        for side in ("Left", "Right"):
            set_rot(P(side + "Arm"), X, math.radians(1.5) * s)

    def walk(ph):
        for side, shift in (("Left", 0.0), ("Right", math.pi)):
            p = ph + shift
            set_rot(P(side + "UpLeg"), X, -math.radians(28) * math.sin(p))
            set_rot(P(side + "Leg"), X, math.radians(40) * max(0.0, math.cos(p)))
            set_rot(P(side + "Foot"), X, -math.radians(10) * max(0.0, math.cos(p)))
            set_rot(P(side + "Arm"), X, math.radians(22) * math.sin(p))
            set_rot(P(side + "ForeArm"), X, -math.radians(15 + 8 * max(0.0, -math.sin(p))))
        bob = -0.012 * H * (1 + math.cos(2 * ph)) / 2  # lowest as a foot lands
        hips = P("Hips")
        hips.location = hips.bone.matrix_local.to_quaternion().inverted() @ Vector((0.0, 0.0, bob))
        set_rot(P("Spine1"), X, math.radians(1.5) * math.cos(ph))

    clips = []
    for name, n, fn in (("idle-loop", 60, idle), ("walk-loop", 30, walk)):
        act = bpy.data.actions.new(name)
        key_all(act, n, fn)
        act.use_fake_user = True
        clips.append({"name": name, "frames": n, "seconds": n / FPS})
    reset_pose(arm)
    arm.animation_data.action = None
    return clips


def rig(task):
    names = task.get("names", "mixamo")
    prefix = task.get("bone_prefix", "mixamorig:") if names == "mixamo" else ""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=task["glb"])
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("the model has no mesh")
    for o in bpy.context.scene.objects:
        o.select_set(o in meshes)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    if len(meshes) > 1:
        bpy.ops.object.join()
    mesh = bpy.context.view_layer.objects.active
    for o in [o for o in bpy.context.scene.objects if o is not mesh]:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # the glTF import splits vertices at every UV / normal seam; the heat solver needs the surface connected (the UVs
    # live on the loops, so the atlas is not touched)
    bm = bmesh.new()
    bm.from_mesh(mesh.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.to_mesh(mesh.data)
    bm.free()

    P0 = np.array([v.co for v in mesh.data.vertices])
    lm = landmarks(P0)
    arm = build_armature(lm, names, prefix)
    info = skin(mesh, arm)
    info["bones"] = len(arm.data.bones)
    info.update(pose_test(mesh, arm, lm, P0, names, prefix))
    info["clips"] = make_clips(arm, lm, names, prefix)
    info["bone_names"] = names

    for o in bpy.context.scene.objects:
        o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=task["out"], export_format="GLB", use_selection=True, export_yup=True,
                              export_skins=True, export_animations=True, export_animation_mode="ACTIONS",
                              export_force_sampling=True)
    return {"out": task["out"], **info}


def main():
    path = sys.argv[sys.argv.index("--") + 1]
    task = json.loads(open(path, encoding="utf-8").read())
    try:
        result = {"ok": True, **{"rig": rig}[task["op"]](task)}
    except Exception:
        result = {"ok": False, "error": traceback.format_exc(limit=8)[-3000:]}
    with open(path + ".out.json", "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
