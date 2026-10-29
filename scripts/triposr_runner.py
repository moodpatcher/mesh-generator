"""Runs in the TripoSR container (or a local TripoSR install):
    python triposr_runner.py task.json
Task: {weights, image, out, resolution, decimation_target}. The output is one JSON per line, like in
trellis2_runner: {"step", "progress", "text"}; at the end {"done": true} or {"error": ..., "oom": bool}.
The call follows TripoSR's run.py (TSR.from_pretrained -> model([image]) -> extract_mesh); the background-free image
goes onto gray, the foreground fills 85% of the image (as in run.py). The weights (stabilityai/TripoSR, MIT, open
download) are downloaded on the first run, later from the cache."""

import json
import os
import sys
import time


def say(**kw):
    print(json.dumps(kw), flush=True)


def weights(repo: str) -> str:
    from huggingface_hub import snapshot_download

    try:  # already downloaded: no network
        return snapshot_download(repo, local_files_only=True, allow_patterns=["config.yaml", "model.ckpt"])
    except Exception:
        pass
    say(step="weights", progress=None, text=repo)
    return snapshot_download(repo, allow_patterns=["config.yaml", "model.ckpt"])


def prepare(path: str, ratio: float = 0.85):
    """RGBA -> the foreground cropped to a square at ratio, on a 50% gray background (as in TripoSR's run.py)."""
    import numpy as np
    from PIL import Image

    img = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32) / 255
    ys, xs = np.where(img[..., 3] > 0)
    if len(xs):
        img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = img.shape[:2]
    side = int(max(h, w) / ratio)
    canvas = np.zeros((side, side, 4), np.float32)
    y0, x0 = (side - h) // 2, (side - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = img
    rgb = canvas[..., :3] * canvas[..., 3:4] + (1 - canvas[..., 3:4]) * 0.5
    return Image.fromarray((rgb * 255).clip(0, 255).astype(np.uint8))


def stub_unused():
    """tsr.utils imports rembg (background removal) and imageio (video) at the top, but the image -> 3D path calls
    neither: stubs take their place (they are not in the container) and raise on any use."""
    import types

    class Stub(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            raise RuntimeError(f"{self.__name__} is not installed in the TripoSR container")

    for name in ("rembg", "imageio"):
        sys.modules.setdefault(name, Stub(name))


CELL = 4  # side of a texel / triangle cell (as in trellis2_runner)


def export(model, scene_code, mesh, out: str, target: int):
    """The marching cubes mesh -> reduced (fast_simplification) -> a textured GLB. Every triangle gets its own
    CELL x CELL cell in the atlas; the texel colors come from TripoSR's color field (query_triplane) - the moderngl
    bake_texture is not needed (it would want a headless OpenGL context). An intermediate file: the post-processing
    re-UVs and re-bakes it anyway."""
    import numpy as np
    import torch
    import trimesh
    from PIL import Image

    if len(mesh.faces) > target:
        import fast_simplification

        v, f = fast_simplification.simplify(np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces),
                                            target_reduction=1 - target / len(mesh.faces))
    else:
        v, f = np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces)
    n = len(f)
    if not n:
        raise RuntimeError("TripoSR produced an empty mesh")
    g = int(np.ceil(np.sqrt(n)))
    size = g * CELL
    t = np.arange(CELL, dtype=np.float32) / (CELL - 1)
    tv, tu = np.meshgrid(t, t, indexing="ij")
    s = np.maximum(tu + tv, 1.0)
    w1, w2 = (tu / s).reshape(-1), (tv / s).reshape(-1)
    w = np.stack([1 - w1 - w2, w1, w2], 1)  # (C*C, 3)
    tri = v[f]  # (n, 3, 3)
    pos = np.einsum("kj,njd->nkd", w, tri).reshape(-1, 3)
    dev = next(model.parameters()).device
    col = np.zeros((len(pos), 3), np.float32)
    with torch.no_grad():
        for i in range(0, len(pos), 1 << 18):
            p = torch.from_numpy(pos[i:i + (1 << 18)]).to(dev)
            col[i:i + len(p)] = model.renderer.query_triplane(model.decoder, p, scene_code)["color"].float().cpu()
    rgb = (col.clip(0, 1) * 255).astype(np.uint8).reshape(n, CELL, CELL, 3)
    img = np.zeros((g * g, CELL, CELL, 3), np.uint8)
    img[:n] = rgb
    img = img.reshape(g, g, CELL, CELL, 3).transpose(0, 2, 1, 3, 4).reshape(size, size, 3)
    cx, cy = np.arange(n) % g, np.arange(n) // g
    corner = np.array([[0.5, 0.5], [CELL - 0.5, 0.5], [0.5, CELL - 0.5]])
    px = np.stack([cx, cy], 1)[:, None, :] * CELL + corner[None]
    uv = np.stack([px[..., 0] / size, 1 - px[..., 1] / size], -1).reshape(-1, 2)
    P = tri.reshape(-1, 3).astype(np.float64)
    # Z up -> Y up, the input image's viewpoint (+X in TripoSR) -> +Z, the glTF "front" - turned 180 degrees from
    # the gradio view's rotation (to_gradio_3d_orientation) (checked on a test character: that way it showed its back)
    x, y, z = P[:, 0].copy(), P[:, 1].copy(), P[:, 2].copy()
    P[:, 0], P[:, 1], P[:, 2] = y, z, x
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(img), metallicFactor=0.0,
                                              roughnessFactor=1.0)
    tm = trimesh.Trimesh(vertices=P, faces=np.arange(3 * n).reshape(-1, 3), process=False,
                         visual=trimesh.visual.TextureVisuals(uv=uv, material=mat))
    tm.export(out)


def main():
    task = json.loads(open(sys.argv[1], encoding="utf-8").read())
    import torch

    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    try:
        path = weights(task.get("weights", "stabilityai/TripoSR"))
        say(step="load", progress=None, text=torch.cuda.get_device_name(0) if dev != "cpu" else "CPU (slow)")
        stub_unused()
        from tsr.system import TSR

        model = TSR.from_pretrained(path, config_name="config.yaml", weight_name="model.ckpt")
    except OSError as e:
        say(error=f"model download failed – the first run needs internet once, later runs are offline: {e}"[:400])
        return 4
    model.renderer.set_chunk_size(8192)
    model.to(dev)
    try:
        say(step="shape", progress=None, text="TripoSR")
        t0 = time.monotonic()
        with torch.no_grad():
            codes = model([prepare(task["image"])], device=dev)
            mesh = model.extract_mesh(codes, False, resolution=int(task.get("resolution", 256)))[0]
        say(step="export", progress=None, text=f"{len(mesh.faces)} faces in {time.monotonic() - t0:.0f} s")
        export(model, codes[0], mesh, task["out"], int(task.get("decimation_target", 100000)))
    except torch.cuda.OutOfMemoryError as e:
        say(error=f"out of GPU memory ({e})".split("\n")[0][:300], oom=True)
        return 3
    say(done=True, out=task["out"])
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    sys.exit(main())
