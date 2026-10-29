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


HEAD_WEIGHT = 0.5  # decimation vertex group weight of the head (the body is 1.0): a vertex with weight 0 is never
#                    collapsed and a higher one collapses more readily, so the face keeps more of the triangles


def head_triangles(obj) -> int:
    zs = [v.co.z for v in obj.data.vertices]
    hz = HEAD_Z * max(zs)
    return sum(1 for p in obj.data.polygons if p.center.z > hz)


def _decimate(low, target: int, head: bool = False) -> list[int]:
    _apply(low, low.modifiers.new("tri", "TRIANGULATE"))
    passes = [_tris(low)]
    if head:  # a character: the head (and the neck) keep more of the budget
        hz = HEAD_Z * max(v.co.z for v in low.data.vertices)
        vg = low.vertex_groups.new(name="head")
        vg.add([v.index for v in low.data.vertices if v.co.z <= hz], 1.0, "REPLACE")
        vg.add([v.index for v in low.data.vertices if v.co.z > hz], HEAD_WEIGHT, "REPLACE")
    for i in range(6):
        n = _tris(low)
        if n <= target:
            break
        m = low.modifiers.new("decimate", "DECIMATE")
        m.decimate_type = "COLLAPSE"
        m.use_collapse_triangulate = True
        if head:
            m.vertex_group = "head"
            m.vertex_group_factor = 1.0
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
        passes = _decimate(low, target, manifold)
        if passes[-1] <= target * 1.05 and not (manifold and _bad_edges(low)):
            return low, passes, False
        bpy.data.objects.remove(low, do_unlink=True)
    # the carving grid decides what survives of the face: 9 mm (192) merged the hair strands and flattened the nose
    low = _carve(hi, 160 if target <= 2000 else 320 if target <= 6000 else 384)
    return low, _decimate(low, target, manifold), True


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


# --- UV layout -----------------------------------------------------------------------------------------------------------

HEAD_Z = 0.855    # fraction of the height: the faces above it (head, neck) are unwrapped on their own ...
HEAD_BOOST = 1.6  # ... and get this much more UV length (2.6x the texels) - the face is what people look at
SMOOTH_ANGLE = 55.0  # shade smooth by angle (a low-poly model with flat normals looks faceted in every engine)
ANGLE = 66.0      # Smart UV Project: the larger, the fewer islands, but the less compact (they pack worse)


def _uv_islands(bm, uv):
    """The faces grouped by UV island (faces sharing an edge whose two UVs coincide)."""
    parent = list(range(len(bm.faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for e in bm.edges:
        if len(e.link_faces) != 2:
            continue
        f0, f1 = e.link_faces
        ids = {e.verts[0].index, e.verts[1].index}
        a = {l.vert.index: l[uv].uv for l in f0.loops if l.vert.index in ids}
        b = {l.vert.index: l[uv].uv for l in f1.loops if l.vert.index in ids}
        if all((a[k] - b[k]).length < 1e-5 for k in a):
            parent[find(f0.index)] = find(f1.index)
    groups = {}
    for f in bm.faces:
        groups.setdefault(find(f.index), []).append(f)
    return list(groups.values())


def unwrap(low, tex, head_boost):
    """UV layout: head and body are unwrapped separately (no island crosses the neck), the head islands are scaled up,
    then everything is packed tightly (rotation allowed). Returns {islands, head_islands}."""
    for o in bpy.context.scene.objects:
        o.select_set(o is low)
    bpy.context.view_layer.objects.active = low
    me = low.data
    H = max(v.co.z for v in me.vertices)
    is_head = [p.center.z > HEAD_Z * H for p in me.polygons] if head_boost else [False] * len(me.polygons)
    margin = max(4.0, tex / 256) / tex
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(me)
    bm.faces.ensure_lookup_table()
    for part in ((True, False) if head_boost else (False,)):
        for f in bm.faces:
            f.select_set(is_head[f.index] == part)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)
        bpy.ops.uv.smart_project(angle_limit=math.radians(ANGLE), island_margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    n_head = 0
    bm = bmesh.new()
    bm.from_mesh(me)
    uv = bm.loops.layers.uv.active
    isl = _uv_islands(bm, uv)
    for faces in isl:
        # every island gets the same texel density (each Smart UV call fitted its islands to the whole unit square,
        # which would give the small head a far higher density than the body); the head islands then get the boost
        a_uv = 0.0
        for f in faces:
            pts = [l[uv].uv for l in f.loops]
            a_uv += abs(sum(pts[i].x * pts[(i + 1) % len(pts)].y - pts[(i + 1) % len(pts)].x * pts[i].y
                            for i in range(len(pts)))) / 2
        a_3d = sum(f.calc_area() for f in faces)
        k = math.sqrt(a_3d / a_uv) if a_uv > 1e-12 and a_3d > 1e-12 else 1.0  # 1 UV unit per meter
        if head_boost and sum(f.calc_center_median().z > HEAD_Z * H for f in faces) * 2 > len(faces):
            k *= HEAD_BOOST
            n_head += 1
        loops = [l for f in faces for l in f.loops]
        us = [l[uv].uv for l in loops]
        cx = (min(u.x for u in us) + max(u.x for u in us)) / 2
        cy = (min(u.y for u in us) + max(u.y for u in us)) / 2
        for l in loops:
            u = l[uv].uv
            l[uv].uv = (cx + (u.x - cx) * k, cy + (u.y - cy) * k)
    bm.to_mesh(me)
    bm.free()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        bpy.context.scene.tool_settings.use_uv_select_sync = True
    except AttributeError:
        pass
    try:
        bpy.ops.uv.pack_islands(rotate=True, rotate_method="AXIS_ALIGNED", margin=margin)
    except TypeError:
        bpy.ops.uv.pack_islands(margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    return {"islands": len(isl), "head_islands": n_head, **texel_density(low, tex)}


def texel_density(low, tex):
    """Median texels per meter over the triangles (and over the head triangles), and how much of the atlas is used."""
    import numpy as np
    co, tri = mesh_arrays(low)
    me = low.data
    loops = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("loops", loops)
    uvs = np.empty(len(me.loops) * 2, np.float64)
    me.uv_layers.active.data.foreach_get("uv", uvs)
    u = uvs.reshape(-1, 2)[loops.reshape(-1, 3)] * tex
    a_uv = np.abs((u[:, 1, 0] - u[:, 0, 0]) * (u[:, 2, 1] - u[:, 0, 1])
                  - (u[:, 1, 1] - u[:, 0, 1]) * (u[:, 2, 0] - u[:, 0, 0])) / 2
    a_3d = np.linalg.norm(np.cross(co[tri[:, 1]] - co[tri[:, 0]], co[tri[:, 2]] - co[tri[:, 0]]), axis=1) / 2
    ok = a_3d > 1e-9
    dens = np.sqrt(a_uv[ok] / a_3d[ok])
    head = co[tri].mean(axis=1)[:, 2][ok] > HEAD_Z * co[:, 2].max()
    return {"px_per_m": round(float(np.median(dens[~head])), 1),
            "head_px_per_m": round(float(np.median(dens[head])), 1) if head.any() else None,
            "uv_area_used": round(float(a_uv.sum() / tex / tex), 3)}


# --- texture: the reference projected onto the front, color matched ------------------------------------------------------

def _atlas_samples(low, tex):
    """For every atlas texel inside a triangle: its 3D position and (smooth) normal. Rows are bottom-up like
    Image.pixels."""
    import numpy as np
    co, tri = mesh_arrays(low)
    me = low.data
    n = len(me.loop_triangles)
    loops = np.empty(n * 3, np.int64)
    me.loop_triangles.foreach_get("loops", loops)
    loops = loops.reshape(-1, 3)
    uvs = np.empty(len(me.loops) * 2, np.float64)
    me.uv_layers.active.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2)
    fn = np.cross(co[tri[:, 1]] - co[tri[:, 0]], co[tri[:, 2]] - co[tri[:, 0]])
    vn = np.zeros_like(co)
    for k in range(3):
        np.add.at(vn, tri[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    mask = np.zeros((tex, tex), bool)
    pos = np.zeros((tex, tex, 3))
    nrm = np.zeros((tex, tex, 3))
    for t in range(n):
        p = uvs[loops[t]] * tex
        x0, x1 = max(int(math.floor(p[:, 0].min())), 0), min(int(math.ceil(p[:, 0].max())), tex - 1)
        y0, y1 = max(int(math.floor(p[:, 1].min())), 0), min(int(math.ceil(p[:, 1].max())), tex - 1)
        d = (p[1, 0] - p[0, 0]) * (p[2, 1] - p[0, 1]) - (p[1, 1] - p[0, 1]) * (p[2, 0] - p[0, 0])
        if abs(d) < 1e-12 or x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w1 = ((gx - p[0, 0]) * (p[2, 1] - p[0, 1]) - (gy - p[0, 1]) * (p[2, 0] - p[0, 0])) / d
        w2 = ((p[1, 0] - p[0, 0]) * (gy - p[0, 1]) - (p[1, 1] - p[0, 1]) * (gx - p[0, 0])) / d
        w0 = 1 - w1 - w2
        inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
        ys, xs = np.nonzero(inside)
        if not len(ys):
            continue
        a, b, c = w0[inside, None], w1[inside, None], w2[inside, None]
        v = tri[t]
        mask[ys + y0, xs + x0] = True
        pos[ys + y0, xs + x0] = a * co[v[0]] + b * co[v[1]] + c * co[v[2]]
        nn = a * vn[v[0]] + b * vn[v[1]] + c * vn[v[2]]
        nrm[ys + y0, xs + x0] = nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-12)
    return mask, pos, nrm


def _depth_map(co, tri, scale, x0, z0, w, h):
    """Front depth (the smallest Y: the camera looks along +Y) of the mesh on a w x h grid, 1 px = 1 / scale m."""
    import numpy as np
    zb = np.full((h, w), np.inf)
    px, py = (co[:, 0] - x0) * scale, (co[:, 2] - z0) * scale
    for t in tri:
        ax, ay, bx, by, cx, cy = px[t[0]], py[t[0]], px[t[1]], py[t[1]], px[t[2]], py[t[2]]
        d = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(d) < 1e-9:
            continue
        x0i, x1i = max(int(math.floor(min(ax, bx, cx))), 0), min(int(math.ceil(max(ax, bx, cx))), w - 1)
        y0i, y1i = max(int(math.floor(min(ay, by, cy))), 0), min(int(math.ceil(max(ay, by, cy))), h - 1)
        if x1i < x0i or y1i < y0i:
            continue
        gx, gy = np.meshgrid(np.arange(x0i, x1i + 1) + 0.5, np.arange(y0i, y1i + 1) + 0.5)
        w1 = ((gx - ax) * (cy - ay) - (gy - ay) * (cx - ax)) / d
        w2 = ((bx - ax) * (gy - ay) - (by - ay) * (gx - ax)) / d
        w0 = 1 - w1 - w2
        inside = (w0 >= -1e-3) & (w1 >= -1e-3) & (w2 >= -1e-3)
        if not inside.any():
            continue
        depth = w0 * co[t[0], 1] + w1 * co[t[1], 1] + w2 * co[t[2], 1]
        sub = zb[y0i:y1i + 1, x0i:x1i + 1]
        sub[inside] = np.minimum(sub[inside], depth[inside])
    return zb


def _bilinear(a, px, py):
    import numpy as np
    h, w = a.shape[:2]
    px = np.clip(px, 0, w - 1.001)
    py = np.clip(py, 0, h - 1.001)
    x0, y0 = px.astype(np.int64), py.astype(np.int64)
    fx, fy = px - x0, py - y0
    if a.ndim == 3:
        fx, fy = fx[:, None], fy[:, None]
    return (a[y0, x0] * (1 - fx) * (1 - fy) + a[y0, x0 + 1] * fx * (1 - fy) + a[y0 + 1, x0] * (1 - fx) * fy
            + a[y0 + 1, x0 + 1] * fx * fy)


def _erode(m, n):
    for _ in range(n):
        o = m.copy()
        o[1:] &= m[:-1]
        o[:-1] &= m[1:]
        o[:, 1:] &= m[:, :-1]
        o[:, :-1] &= m[:, 1:]
        m = o
    return m


def _blur(a, n):
    import numpy as np
    for _ in range(n):
        p = np.pad(a, 1, mode="edge")
        a = sum(p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)) / 9.0
    return a


def _dilate(rgb, mask, n):
    """Fill the texels around the islands with the colors next to them (the margin, so no seam shows through the
    texture filter)."""
    import numpy as np
    rgb, m = rgb.copy(), mask.copy()
    for _ in range(n):
        acc = np.zeros_like(rgb)
        cnt = np.zeros(m.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            sm = np.roll(m, (dy, dx), (0, 1))
            acc += np.roll(rgb, (dy, dx), (0, 1)) * sm[..., None]
            cnt += sm
        new = (~m) & (cnt > 0)
        rgb[new] = acc[new] / cnt[new, None]
        m |= new
    return rgb


def project_reference(low, img, ref_path, tex, gate_mode="smooth"):
    """The reference photo is sharper and truer than the generator's colors (the 512 mode makes them dark and soft).
    So the keyed reference is projected onto every texel that faces the front, unoccluded (BVH ray), and blended in
    by how directly it faces; head and body are first aligned to the photo separately (the generated shape is not
    exactly the photo's: a search over offset + scale that maximizes the correlation with the baked colors). The
    baked colors elsewhere (sides, back) get the per-channel gain that makes them match where both are known."""
    import numpy as np
    co, tri = mesh_arrays(low)
    H = float(co[:, 2].max())
    mask, pos, nrm = _atlas_samples(low, tex)
    px = np.empty(tex * tex * 4, np.float32)
    img.pixels.foreach_get(px)
    G = px.reshape(tex, tex, 4)
    ref_img = bpy.data.images.load(ref_path)
    rw, rh = ref_img.size
    rp = np.empty(rw * rh * 4, np.float32)
    ref_img.pixels.foreach_get(rp)
    bpy.data.images.remove(ref_img)
    ref = rp.reshape(rh, rw, 4)[::-1]  # top-down
    ys, xs = np.nonzero(ref[..., 3] > 0.5)
    rx0, rx1, ry0, ry1 = xs.min(), xs.max(), ys.min(), ys.max()
    k = (ry1 - ry0) / H
    xc, cxr = (co[:, 0].min() + co[:, 0].max()) / 2, (rx0 + rx1) / 2
    solid = _erode(ref[..., 3] > 0.95, 5).astype(np.float32)
    lum = lambda c: c @ np.array([0.30, 0.59, 0.11])  # noqa: E731
    ref_lum = _blur(lum(ref[..., :3]), 3)
    # the photo blurred over about 3 cm, with the transparent background left out (so the edge does not go dark)
    wa = (ref[..., 3:4] > 0.95).astype(np.float32)
    ref_blur = np.dstack([_blur(ref[..., c] * wa[..., 0], 8) for c in range(3)]) / np.maximum(_blur(wa[..., 0], 8)[..., None], 1e-3)

    P, Nn, Gc = pos[mask], nrm[mask], G[..., :3][mask]
    face = -Nn[:, 1]  # the camera looks along +Y: the front faces -Y
    # unoccluded: nothing nearer to the camera at that spot (a depth map of the front, 1 px = about 1 mm)
    ds = 1500.0 / H
    dx0, dz0 = co[:, 0].min() - 0.01, co[:, 2].min() - 0.01
    zb = _depth_map(co, tri, ds, dx0, dz0, int((co[:, 0].max() - dx0) * ds) + 3, int((co[:, 2].max() - dz0) * ds) + 3)
    ix = np.clip(((P[:, 0] - dx0) * ds).astype(int), 0, zb.shape[1] - 1)
    iz = np.clip(((P[:, 2] - dz0) * ds).astype(int), 0, zb.shape[0] - 1)
    vis = (face > 0.3) & (P[:, 1] <= zb[iz, ix] + 0.008)
    head = P[:, 2] > HEAD_Z * H

    def to_ref(Pi, s, dx, dz, piv):
        X = piv[0] + (Pi[:, 0] - piv[0]) * s
        Z = piv[1] + (Pi[:, 2] - piv[1]) * s
        return cxr + (X - xc + dx) * k, ry1 - (Z + dz) * k

    def align(sel, span, step, scales):
        idx = np.nonzero(sel & vis & (face > 0.6))[0]
        if len(idx) < 150:
            return (1.0, 0.0, 0.0), None
        idx = idx[::max(1, len(idx) // 8000)]
        Pi, g = P[idx], lum(Gc[idx])
        piv = (Pi[:, 0].mean(), Pi[:, 2].mean())
        def score(s, dx, dz):
            u, v = to_ref(Pi, s, dx, dz, piv)
            ok = _bilinear(solid, u, v) > 0.99
            if ok.mean() < 0.6:
                return -9.0
            r = _bilinear(ref_lum, u, v)[ok]
            gg = g[ok]
            ncc = float(((gg - gg.mean()) * (r - r.mean())).mean() / (gg.std() * r.std() + 1e-6))
            return ncc * float(ok.mean())

        base = score(1.0, 0.0, 0.0)
        best, best_p = base, (1.0, 0.0, 0.0)
        grid = np.arange(-span, span + 1e-9, step)
        for s in scales:  # coarse, then a fine search around the best
            for dx in grid:
                for dz in grid:
                    sc = score(s, dx, dz)
                    if sc > best:
                        best, best_p = sc, (s, float(dx), float(dz))
        for _ in range(2):
            s0, x0, z0 = best_p
            for s in (s0 - step, s0 - step / 2, s0, s0 + step / 2, s0 + step):
                for dx in (x0 - step / 2, x0, x0 + step / 2):
                    for dz in (z0 - step / 2, z0, z0 + step / 2):
                        sc = score(s, dx, dz)
                        if sc > best:
                            best, best_p = sc, (float(s), float(dx), float(dz))
            step /= 2
        if best < base + 0.01:
            best_p = (1.0, 0.0, 0.0)  # no clear gain: keep the plain fit
        return best_p, round(best, 3)

    # 1. a global fit per part (offset + scale): the generated shape is not exactly the photo's
    regions = {"head": head, "upper": ~head & (P[:, 2] > 0.52 * H), "legs": ~head & (P[:, 2] <= 0.52 * H)}
    fits = {"head": align(head, 0.04, 0.005, (0.90, 0.95, 1.0, 1.05, 1.10, 1.15, 1.20)),
            "upper": align(regions["upper"], 0.06, 0.01, (0.96, 0.98, 1.0, 1.02, 1.04)),
            "legs": align(regions["legs"], 0.06, 0.01, (0.96, 0.98, 1.0, 1.02, 1.04))}
    U, V = np.zeros(len(P)), np.zeros(len(P))
    for name, sel in regions.items():
        if sel.any():
            s, dx, dz = fits[name][0]
            Pi = P[sel]
            U[sel], V[sel] = to_ref(Pi, s, dx, dz, (Pi[:, 0].mean(), Pi[:, 2].mean()))

    # 2. a local flow field on top: a grid of cells (about 2.5 cm), each finds the small shift (+-1.2 cm) at which the
    # photo best correlates with the generator's baked colors there; cells without contrast keep 0, the field is
    # smoothed. This is what puts the lips, the eyes, the belt straps on the mesh where they belong.
    flow_cells = 0
    idx = np.nonzero(vis & (face > 0.6) & (Gc.max(axis=1) > 0.012))[0]
    if len(idx) > 2000:
        idx = idx[::max(1, len(idx) // 200000)]
        cell = max(0.025, 14.0 / (0.42 * tex))
        x_min = float(P[:, 0].min())
        cx = np.floor((P[idx, 0] - x_min) / cell).astype(int)
        cz = np.floor(P[idx, 2] / cell).astype(int)
        nx, nz = int(cx.max()) + 1, int(cz.max()) + 1
        cid = cz * nx + cx
        g = lum(Gc[idx])
        step = 0.004
        offs = [(i * step, j * step) for i in range(-3, 4) for j in range(-3, 4)]
        cnt = np.bincount(cid, minlength=nx * nz).astype(np.float64)

        def stats(w, a, b):
            n = np.bincount(cid, w, nx * nz)
            sa, sb = np.bincount(cid, w * a, nx * nz), np.bincount(cid, w * b, nx * nz)
            saa, sbb, sab = (np.bincount(cid, w * a * a, nx * nz), np.bincount(cid, w * b * b, nx * nz),
                             np.bincount(cid, w * a * b, nx * nz))
            nn = np.maximum(n, 1e-9)
            cov = sab / nn - sa / nn * sb / nn
            va, vb = saa / nn - (sa / nn) ** 2, sbb / nn - (sb / nn) ** 2
            return n, cov / np.sqrt(np.maximum(va * vb, 1e-8)), np.sqrt(np.maximum(vb, 0))

        nccs = np.full((len(offs), nx * nz), -1.0)
        std0 = None
        for oi, (dx, dz) in enumerate(offs):
            u, v = U[idx] + dx * k, V[idx] - dz * k
            w = (_bilinear(solid, u, v) > 0.99).astype(np.float64)
            n, ncc, sd = stats(w, g, _bilinear(ref_lum, u, v))
            nccs[oi] = np.where((n >= 40) & (n >= 0.7 * cnt), ncc, -1.0)
            if dx == 0 and dz == 0:
                std0 = sd
        c0 = offs.index((0.0, 0.0))
        best = nccs.argmax(0)
        gain_ncc = nccs.max(0) - nccs[c0]
        conf = ((gain_ncc > 0.04) & (nccs.max(0) > 0.45) & (std0 > 0.02)).astype(np.float64)
        FX = np.array([offs[b][0] for b in best]) * conf
        FZ = np.array([offs[b][1] for b in best]) * conf
        flow_cells = int(conf.sum())
        FX, FZ, C = FX.reshape(nz, nx), FZ.reshape(nz, nx), conf.reshape(nz, nx)

        def box(a):
            p = np.pad(a, 1)
            return sum(p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3))

        for _ in range(3):  # smooth: each cell takes the confidence-weighted mean of its neighborhood
            num_x, num_z, den = box(FX * C), box(FZ * C), box(C)
            FX = np.where(den > 1e-6, num_x / np.maximum(den, 1e-6), 0.0)
            FZ = np.where(den > 1e-6, num_z / np.maximum(den, 1e-6), 0.0)
            C = np.clip(den / 4.0, 0.0, 1.0)
        fx = np.clip((P[:, 0] - x_min) / cell - 0.5, 0, nx - 1.001)
        fz = np.clip(P[:, 2] / cell - 0.5, 0, nz - 1.001)
        x0c, z0c = fx.astype(int), fz.astype(int)
        ax, az = fx - x0c, fz - z0c

        def samp(F):
            return (F[z0c, x0c] * (1 - ax) * (1 - az) + F[z0c, x0c + 1] * ax * (1 - az)
                    + F[z0c + 1, x0c] * (1 - ax) * az + F[z0c + 1, x0c + 1] * ax * az)

        U, V = U + samp(FX) * k, V - samp(FZ) * k
    R = _bilinear(ref[..., :3], U, V).astype(np.float32)
    A = _bilinear(solid, U, V).astype(np.float32)
    wf = np.clip((face - 0.30) / 0.50, 0.0, 1.0)
    W = (wf * wf * (3 - 2 * wf) * vis * A).astype(np.float32)
    def fit_gains(strong):
        gain = np.ones(3)
        gains = np.ones((len(P), 3))
        clusters = 0
        if strong.sum() >= 200:
            gain = np.clip(R[strong].mean(0) / np.maximum(Gc[strong].mean(0), 1e-4), 0.6, 2.0)
            gains[:] = gain
            # a hue shift is not the same everywhere (the blond hair came out red, the jacket only darker): a gain
            # per color group of the generator's colors (k-means), applied to every texel by its closest groups
            S, SR = Gc[strong], R[strong]
            sel = np.linspace(0, len(S) - 1, min(len(S), 20000)).astype(int)
            S, SR = S[sel], SR[sel]
            cen = S[np.linspace(0, len(S) - 1, 10).astype(int)].copy()
            for _ in range(10):
                lab = np.argmin(((S[:, None] - cen[None]) ** 2).sum(-1), axis=1)
                for c in range(len(cen)):
                    if (lab == c).any():
                        cen[c] = S[lab == c].mean(0)
            cg = np.tile(gain, (len(cen), 1))
            for c in range(len(cen)):
                if (lab == c).sum() >= 40:
                    cg[c] = np.clip(SR[lab == c].mean(0) / np.maximum(S[lab == c].mean(0), 1e-4), 0.5, 2.5)
                    clusters += 1
            for i in range(0, len(P), 200000):  # in chunks: a 2048 atlas has millions of texels
                d2 = ((Gc[i:i + 200000, None] - cen[None]) ** 2).sum(-1)
                wt = np.exp(-(d2 - d2.min(axis=1, keepdims=True)) / 0.004)
                gains[i:i + 200000] = (wt[..., None] * cg[None]).sum(1) / wt.sum(1, keepdims=True)
        return gain, gains, clusters

    # Consistency gate: where the photo and the shape disagree (the photo's hair lands on the mesh's cheek because the
    # generated head is a little different), the photo's color is a large area of a quite different color than the
    # generator's there. Compare the blurred photo with the corrected generator color: a big, smooth difference means
    # a misprojection and the weight goes down; the small features (eyes, lips, buckles) are lost in the blur, so
    # they pass.
    Rb = _bilinear(ref_blur, U, V).astype(np.float32)
    W0, gate = W, np.ones(len(P), np.float32)
    for _ in range(2):  # the gains are fitted on the texels that pass, the gate on the corrected colors
        strong = (W0 * gate > 0.6) & (Gc.max(axis=1) > 0.012)
        gain, gains, clusters = fit_gains(strong)
        Gp = np.clip(Gc * gains, 0, 1)
        dl = np.abs(lum(Rb) - lum(Gp))
        dc = np.abs((Rb - lum(Rb)[:, None]) - (Gp - lum(Gp)[:, None])).sum(1)
        # (a stricter gate on the head was tried: it also removed the eyes and the lips, which differ most from the
        # generator's soft colors)
        gate = np.clip(1 - (dl + 0.5 * dc - 0.10) / 0.20, 0.0, 1.0).astype(np.float32)
        if gate_mode == "off":
            gate[:] = 1.0
        elif gate_mode == "smooth":
            # texel by texel the gate flickers where the photo's dark strap edge meets the generator's soft one (ragged
            # fringes): smooth it over the atlas (neighbors in the atlas are neighbors on the surface)
            gi = np.zeros(mask.shape, np.float32)
            gi[mask] = gate
            mf = mask.astype(np.float32)
            r = max(3, tex // 128)
            gate = (_blur(gi, r) / np.maximum(_blur(mf, r), 1e-3))[mask].astype(np.float32)
            # the gate is for the head (the photo's hair lands on the cheek); on the body it only eats the dark
            # straps and hems of the photo (measured: ragged fringes), where the fit is good anyway
            gate = np.where(head, gate, 1.0).astype(np.float32)
    W = W0 * gate
    out = Gp * (1 - W[:, None]) + R * W[:, None]
    # texels the bake missed (no ray hit: pure black) and that the photo does not cover are filled from their
    # neighbors instead
    good = (Gc.max(axis=1) > 0.012) | (W > 0.05)
    full = G.copy()
    full[..., :3][mask] = out
    valid = np.zeros_like(mask)
    valid[mask] = good
    full[..., :3] = _dilate(full[..., :3], valid, max(10, tex // 64))
    full[..., 3] = 1.0
    img.pixels.foreach_set(full.reshape(-1))
    img.update()
    return {"projected": True, "bake_holes": int((~good).sum()), "front_texels": round(float((W > 0.5).sum() / max(len(P), 1)), 3),
            "gain": [round(float(x), 3) for x in gain], "color_groups": clusters,
            "flow_cells": flow_cells,
            "gated_texels": round(float(((W0 > 0.3) & (gate < 0.5)).sum() / max(int((W0 > 0.3).sum()), 1)), 3),
            "fit": {n: {"scale_offset_m": [round(c, 4) for c in f[0]], "ncc": f[1]} for n, f in fits.items()}}


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
    uv_info = unwrap(low, tex, bool(task.get("manifold")))
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
                        max_ray_distance=size * 0.06, margin=max(8, tex // 64))

    bpy.data.objects.remove(hi, do_unlink=True)
    ori = orient(low, task["reference"]) if task.get("reference") else {"view": "front", "iou": {}}
    tex_info = {"projected": False, **uv_info}
    if task.get("project") and task.get("reference"):  # the front of the reference photo onto the atlas
        tex_info.update(project_reference(low, img, task["reference"], tex, task.get("gate", "smooth")))
    if task.get("ps1"):
        ps1_colors(img)
    img.pack()
    for o in bpy.context.scene.objects:
        o.select_set(o is low)
    if not task.get("flat"):
        bpy.context.view_layer.objects.active = low
        try:  # smooth by angle: soft shading on the round parts, the hard edges (belt, boots, hair ends) stay sharp
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(SMOOTH_ANGLE))
        except (AttributeError, RuntimeError):
            bpy.ops.object.shade_smooth()
    bpy.ops.export_scene.gltf(filepath=task["out"], export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=True)
    return {"out": task["out"], "triangles": _tris(low), "hi_triangles": hi_tris, "pieces_removed": removed,
            "pieces": len(isl) - removed, "scale": round(s, 6), "remeshed": remeshed,
            "decimate_passes": list(passes), "head_triangles": head_triangles(low),
            "front_view": ori["view"], "view_iou": ori["iou"],
            "texture": tex_info}


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
