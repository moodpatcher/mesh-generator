"""Step 3, runs INSIDE Blender (blender -b --factory-startup --python blender_post.py -- task.json): the high-res mesh
-> a finished low-poly model. Task: {op: "image3d", glb, out, triangles, texture, height, remesh, manifold,
reference (the keyed image, for the orientation), ps1}.
  1. import the high-res GLB; clean up: duplicate vertices, tiny / floating pieces, normals; scale to real meters
     (height), the soles on the ground, centered horizontally;
  2. low-poly: Collapse decimation to the triangle budget - Blender's QEM-based collapse follows the curvature, so the
     budget goes to the silhouette and the sharp parts; if that stalls (the non-manifold edges and tiny tunnels of
     a generated mesh), or when asked up front: first carve a solid body around the mesh (_carve) and decimate that;
  3. Smart UV Project, then a Cycles bake (Emit, color only) selected-to-active: the color of the high-res mesh onto
     a texture x texture atlas;
  4. orientation: of the four side views, the one that best matches the reference outline becomes the front (glTF +Z);
  5. ps1 (when asked): 15-bit color (5 bits per channel) with a 4x4 Bayer dither on the atlas;
  6. GLB export (Y up) of the low-poly mesh only.
The result goes next to the task (<task>.out.json). Uses only Blender's own Python (bmesh, numpy).
The same mesh with the same settings gives a bit-identical result (measured: md5 of the positions, UVs, texture)."""

import json
import math
import sys
import traceback

import bmesh
import bpy


def _islands(bm):
    """The connected face groups (by edge adjacency)."""
    seen, out = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, isl = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            isl.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        out.append(isl)
    return out


def _tris(obj) -> int:
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def _emit_color(mat):
    """The material's color (the first image texture, or the Principled base color) straight into an Emission shader."""
    if mat is None:
        return
    mat.use_nodes = True
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    img = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None), None)
    if img is not None:
        nt.links.new(img.outputs["Color"], emit.inputs["Color"])
    else:
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            emit.inputs["Color"].default_value = bsdf.inputs["Base Color"].default_value
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])


def _apply(obj, mod):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


def _carve(hi, n: int):
    """A solid body around the mesh: the surface samples go onto a voxel grid with n divisions, flooded from the
    grid's border; whatever cannot be reached from the outside is the body. Its boundary faces are smoothed with Voxel
    Remesh: a closed, one-piece, genus-zero mesh - the generators' inner faces, holes, tiny tunnels and non-manifold
    edges disappear, so the Collapse decimation runs all the way to the budget. Gaps thinner than the grid
    (size / n) grow together."""
    import numpy as np
    me = hi.data
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    me.calc_loop_triangles()
    tri = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    tri = tri.reshape(-1, 3)
    lo, up = co.min(0), co.max(0)
    step = float((up - lo).max()) / n
    org = lo - 2 * step
    dims = tuple(np.ceil((up - lo) / step).astype(int) + 4)
    shell = np.zeros(dims, bool)
    a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
    # samples: vertices, edge midpoints, centroids - on a dense surface (the generators' are) this reaches every
    # touched voxel; triangles larger than the grid are also sampled inside
    pts = [co, (a + b) / 2, (b + c) / 2, (a + c) / 2, (a + b + c) / 3]
    edge = np.maximum.reduce([np.linalg.norm(a - b, axis=1), np.linalg.norm(b - c, axis=1),
                              np.linalg.norm(a - c, axis=1)])
    big = edge > step / 2
    if big.any():
        k = int(min(64, np.ceil(2 * edge[big].max() / step) + 1))
        for i in range(k + 1):
            for j in range(k + 1 - i):
                u, v = i / k, j / k
                pts.append(a[big] + u * (b[big] - a[big]) + v * (c[big] - a[big]))
    for p in pts:
        ijk = np.floor((p - org) / step).astype(int)
        shell[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True
    free = ~shell
    out = np.zeros(dims, bool)
    out[0], out[-1], out[:, 0], out[:, -1], out[:, :, 0], out[:, :, -1] = (True,) * 6
    out &= free
    count = out.sum()
    while True:
        g = out.copy()
        g[1:] |= out[:-1]
        g[:-1] |= out[1:]
        g[:, 1:] |= out[:, :-1]
        g[:, :-1] |= out[:, 1:]
        g[:, :, 1:] |= out[:, :, :-1]
        g[:, :, :-1] |= out[:, :, 1:]
        g &= free
        new = g.sum()
        out = g
        if new == count:
            break
        count = new
    solid = ~out
    verts, faces, index = [], [], {}

    def vid(key):
        if key not in index:
            index[key] = len(verts)
            verts.append(tuple(org + np.array(key) * step))
        return index[key]

    corners = {0: ((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)), 1: ((0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)),
               2: ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))}
    for ax in range(3):
        for side in (1, 0):
            nb = np.zeros_like(solid)
            dst, src = [slice(None)] * 3, [slice(None)] * 3
            dst[ax], src[ax] = (slice(0, -1), slice(1, None)) if side else (slice(1, None), slice(0, -1))
            nb[tuple(dst)] = solid[tuple(src)]
            for cell in np.argwhere(solid & ~nb):
                base = cell.copy()
                base[ax] += side
                q = [vid((int(base[0] + d[0]), int(base[1] + d[1]), int(base[2] + d[2]))) for d in corners[ax]]
                faces.append(q if side else q[::-1])
    mesh = bpy.data.meshes.new("lowpoly")
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    low = bpy.data.objects.new("lowpoly", mesh)
    bpy.context.scene.collection.objects.link(low)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    m = low.modifiers.new("remesh", "REMESH")
    m.mode = "VOXEL"
    m.voxel_size = step
    _apply(low, m)
    # floating crumbs smaller than the voxel size are not needed
    bm = bmesh.new()
    bm.from_mesh(mesh)
    isl = _islands(bm)
    top = max((len(i) for i in isl), default=0)
    bmesh.ops.delete(bm, geom=[f for i in isl if len(i) < 0.01 * top for f in i], context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    return low


def _decimate(low, target: int) -> list[int]:
    _apply(low, low.modifiers.new("tri", "TRIANGULATE"))
    passes = [_tris(low)]
    for i in range(6):
        n = _tris(low)
        if n <= target:
            break
        m = low.modifiers.new("decimate", "DECIMATE")
        m.decimate_type = "COLLAPSE"
        m.use_collapse_triangulate = True
        m.ratio = max(1e-4, target / n * (0.98 if i > 0 else 1.0))
        _apply(low, m)
        passes.append(_tris(low))
        if passes[-1] >= n * 0.98:  # no further progress
            break
    return passes


def _bad_edges(obj) -> int:
    """Open and non-manifold edges of the welded mesh (the glTF import splits it at the UV seams)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    n = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    return n


def _lowpoly(hi, target: int, remesh: bool, manifold: bool = False):
    """Low-poly from the high-res mesh: first a Collapse decimation of a copy; if that stalls above the budget (the
    non-manifold edges and tiny tunnels of generated meshes cause this), or when asked up front: decimate a solid
    carved around it. manifold (characters): carve also when the decimated mesh is open or double-layered - when
    skinned, the inner layer shows through in motion and the holes tear (on one test character: 8 cm tears and a
    black wedge at the head; carved it is closed and stays under 6 cm). Returns (low, passes, carved)."""
    if not remesh:
        low = hi.copy()
        low.data = hi.data.copy()
        low.name = "lowpoly"
        bpy.context.scene.collection.objects.link(low)
        passes = _decimate(low, target)
        if passes[-1] <= target * 1.05 and not (manifold and _bad_edges(low)):
            return low, passes, False
        bpy.data.objects.remove(low, do_unlink=True)
    low = _carve(hi, 128 if target <= 2000 else 192 if target <= 6000 else 256)
    return low, _decimate(low, target), True


SIL = 96  # grid of the outline comparison


def _fit_box(mask, size=SIL):
    """The mask cropped to its bounds, fitted proportionally into size x size, centered (nearest neighbor)."""
    import numpy as np
    ys, xs = np.nonzero(mask)
    out = np.zeros((size, size), bool)
    if not len(ys):
        return out
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = crop.shape
    k = (size - 1) / max(h, w)
    nh, nw = max(1, round(h * k)), max(1, round(w * k))
    yi = np.minimum((np.arange(nh) / k).astype(int), h - 1)
    xi = np.minimum((np.arange(nw) / k).astype(int), w - 1)
    a = crop[yi][:, xi]
    oy, ox = (size - nh) // 2, (size - nw) // 2
    out[oy:oy + nh, ox:ox + nw] = a
    return out


def silhouette(co, tri, right, up, size=SIL):
    """The orthographic outline of the mesh (bool image, scaled to its bounds). co: (N,3), tri: (M,3)."""
    import numpy as np
    x, y = co @ np.asarray(right, float), co @ np.asarray(up, float)
    n = size * 2
    k = (n - 1) / max(x.max() - x.min(), y.max() - y.min(), 1e-9)
    px, py = (x - x.min()) * k, (n - 1) - (y - y.min()) * k
    im = np.zeros((n, n), bool)
    for t in tri:
        ax, ay, bx, by, cx, cy = px[t[0]], py[t[0]], px[t[1]], py[t[1]], px[t[2]], py[t[2]]
        x0, x1 = int(max(0, min(ax, bx, cx))), min(n - 1, int(max(ax, bx, cx)) + 1)
        y0, y1 = int(max(0, min(ay, by, cy))), min(n - 1, int(max(ay, by, cy)) + 1)
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(d) < 1e-12:
            im[int(ay), int(ax)] = True
            continue
        u = ((bx - gx) * (cy - gy) - (by - gy) * (cx - gx)) / d
        v = ((cx - gx) * (ay - gy) - (cy - gy) * (ax - gx)) / d
        inside = (u >= -0.02) & (v >= -0.02) & (1 - u - v >= -0.02)
        im[y0:y1 + 1, x0:x1 + 1] |= inside
    return _fit_box(im, size)


def ref_mask(path):
    """The alpha of the keyed reference image (alpha > 0.5), fitted to its bounds."""
    import numpy as np
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    a = px.reshape(h, w, 4)[::-1, :, 3]  # Blender stores the rows bottom-up
    return _fit_box(a > 0.5)


def iou(a, b):
    return float((a & b).sum() / max((a | b).sum(), 1))


# In Blender (Z up) the glTF front (+Z) faces -Y: in the front view right is +X and up is +Z.
# yaw: rotate this many degrees around Z to bring that view to the front
VIEWS = {"front": ((1, 0, 0), 0.0), "right": ((0, 1, 0), -90.0), "back": ((-1, 0, 0), 180.0),
         "left": ((0, -1, 0), 90.0)}  # right: the camera on the +X side; -90 degrees turns that side towards -Y


def mesh_arrays(obj):
    import numpy as np
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    me.calc_loop_triangles()
    tri = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    return co.reshape(-1, 3), tri.reshape(-1, 3)


def view_scores(obj, ref):
    co, tri = mesh_arrays(obj)
    return {v: round(iou(silhouette(co, tri, r, (0, 0, 1)), ref), 3) for v, (r, _) in VIEWS.items()}


def orient(low, ref_path):
    """The outlines of the front and the back are mirror images of each other: the outline only decides the axis -
    the generator's direction (the image's viewpoint -> front) stays, unless the side axis fits better."""
    ref = ref_mask(ref_path)
    scores = view_scores(low, ref)
    side = max(scores["right"], scores["left"])
    best = "front" if max(scores["front"], scores["back"]) >= side - 0.02 else max(("right", "left"), key=scores.get)
    deg = VIEWS[best][1]
    if deg:
        low.rotation_euler = (0, 0, math.radians(deg))
        for o in bpy.context.scene.objects:
            o.select_set(o is low)
        bpy.context.view_layer.objects.active = low
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    return {"view": best, "iou": scores}


BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def ps1_colors(img):
    """15-bit color (5 bits per channel, like the PS1's VRAM) with a 4x4 ordered dither."""
    import numpy as np
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    bayer = (np.array(BAYER, np.float32) + 0.5) / 16 - 0.5
    d = np.tile(bayer, (h // 4 + 1, w // 4 + 1))[:h, :w, None]
    rgb = np.floor(np.clip(px[..., :3] * 31 + d, 0, 31) + 0.5) / 31
    px[..., :3] = rgb
    img.pixels.foreach_set(px.reshape(-1))
    img.update()


def image3d(task):
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
    hi = bpy.context.view_layer.objects.active
    for o in [o for o in bpy.context.scene.objects if o is not hi]:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    hi_tris = _tris(hi)

    # clean-up
    bm = bmesh.new()
    bm.from_mesh(hi.data)
    co = [v.co for v in bm.verts]
    lo = [min(c[i] for c in co) for i in range(3)]
    up = [max(c[i] for c in co) for i in range(3)]
    extent = math.dist(lo, up) or 1.0
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=extent * 1e-6)
    bm.faces.ensure_lookup_table()
    isl = _islands(bm)
    total = len(bm.faces)
    drop, removed = [], 0
    for faces in isl:
        vs = {v for f in faces for v in f.verts}
        a = [min(v.co[i] for v in vs) for i in range(3)]
        b = [max(v.co[i] for v in vs) for i in range(3)]
        if len(faces) < 4 or (len(faces) < 0.002 * total and math.dist(a, b) < 0.05 * extent):
            drop += faces
            removed += 1
    if len(drop) < total:
        bmesh.ops.delete(bm, geom=drop, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(hi.data)
    bm.free()

    # scale: Z up in Blender (the glTF import turns Y into Z)
    zs = [v.co.z for v in hi.data.vertices]
    s = float(task["height"]) / max(max(zs) - min(zs), 1e-9)
    hi.scale = (s, s, s)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    vs = [v.co for v in hi.data.vertices]
    cx = (min(v.x for v in vs) + max(v.x for v in vs)) / 2
    cy = (min(v.y for v in vs) + max(v.y for v in vs)) / 2
    hi.location = (-cx, -cy, -min(v.z for v in vs))
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
    size = max(hi.dimensions)

    # low-poly
    target = int(task["triangles"])
    low, passes, remeshed = _lowpoly(hi, target, bool(task.get("remesh")), bool(task.get("manifold")))
    z0 = min(v.co.z for v in low.data.vertices)  # on the ground also after the remesh / decimation
    for v in low.data.vertices:
        v.co.z -= z0

    # UV + atlas
    tex = int(task["texture"])
    while low.data.uv_layers:
        low.data.uv_layers.remove(low.data.uv_layers[0])
    low.data.uv_layers.new(name="UVMap")
    for o in bpy.context.scene.objects:
        o.select_set(o is low)
    bpy.context.view_layer.objects.active = low
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=2.0 / tex)
    bpy.ops.object.mode_set(mode="OBJECT")
    img = bpy.data.images.new("atlas", tex, tex, alpha=False)
    mat = bpy.data.materials.new("atlas")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    node = nodes.new("ShaderNodeTexImage")
    node.image = img
    node.interpolation = "Closest"
    bsdf = nodes["Principled BSDF"]
    mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    # matte, no shine (a PS1/PS2 model has no highlights; the default 0.5 roughness gives bright triangle spots on the
    # low-poly faces)
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Metallic"].default_value = 0.0
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.0
    nodes.active = node
    low.data.materials.clear()
    low.data.materials.append(mat)

    # bake: the color of the high-res mesh onto the low-poly atlas - the source materials as light-independent
    # emission (so both PBR and unlit glTF materials give their color, without shadows)
    for slot in hi.material_slots:
        _emit_color(slot.material)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    hi.select_set(True)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.object.bake(type="EMIT", use_selected_to_active=True, cage_extrusion=size * 0.02,
                        max_ray_distance=size * 0.06, margin=4)
    if task.get("ps1"):
        ps1_colors(img)
    img.pack()

    bpy.data.objects.remove(hi, do_unlink=True)
    ori = orient(low, task["reference"]) if task.get("reference") else {"view": "front", "iou": {}}
    for o in bpy.context.scene.objects:
        o.select_set(o is low)
    bpy.ops.export_scene.gltf(filepath=task["out"], export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=True)
    return {"out": task["out"], "triangles": _tris(low), "hi_triangles": hi_tris, "pieces_removed": removed,
            "pieces": len(isl) - removed, "scale": round(s, 6), "remeshed": remeshed,
            "decimate_passes": list(passes), "front_view": ori["view"], "view_iou": ori["iou"]}


def main():
    path = sys.argv[sys.argv.index("--") + 1]
    task = json.loads(open(path, encoding="utf-8").read())
    try:
        result = {"ok": True, **{"image3d": image3d}[task["op"]](task)}
    except Exception:
        result = {"ok": False, "error": traceback.format_exc(limit=8)[-3000:]}
    with open(path + ".out.json", "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":  # blender_check.py imports the outline functions
    main()
