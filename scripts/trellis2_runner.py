"""Runs in the TRELLIS.2 environment (the container, or a local TRELLIS.2 install):
    python trellis2_runner.py task.json
Task: {weights, image, out, seed, pipeline_type, low_vram, decimation_target, texture_size}. The output is one JSON
per line: {"step", "progress", "text"} for progress; at the end {"done": true} or {"error": ..., "oom": bool}.
The first run downloads the weights (under HF_HOME), later runs use the cache, offline.
The call follows the TRELLIS.2 README example (Trellis2ImageTo3DPipeline -> export)."""

import json
import os
import sys
import time


def say(**kw):
    print(json.dumps(kw), flush=True)


def weights(repo: str) -> str:
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    last = [0.0]

    class Progress(tqdm):
        def update(self, n=1):
            r = super().update(n)
            if self.total and time.monotonic() - last[0] > 0.5:
                last[0] = time.monotonic()
                say(step="weights", progress=min(1.0, self.n / self.total), text=f"{self.n}/{self.total} files")
            return r

    try:  # already downloaded: no network
        return snapshot_download(repo, local_files_only=True)
    except Exception:
        pass
    say(step="weights", progress=0.0, text=repo)
    return snapshot_download(repo, tqdm_class=Progress)


class NoRembg:
    """Replaces the background remover (the pipeline would load briaai/RMBG-2.0: gated download, and CC BY-NC - not
    for commercial use). The kit always passes a background-free (RGBA) image, so it is not needed; a fully opaque
    image passes through unchanged."""

    def __init__(self, *a, **k):
        pass

    def to(self, *a, **k):
        return self

    def cpu(self):
        return self

    def __call__(self, image):
        return image.convert("RGBA")


TIMM_DINOV3 = "vit_large_patch16_dinov3.lvd1689m"  # the same Meta weights from timm, without an account or token


class TimmDinoV3:
    """TRELLIS.2's DinoV3FeatureExtractor with timm's DINOv3 ViT-L/16 (the same DINOv3 License; Meta's gated repo -
    facebook/dinov3-vitl16-pretrain-lvd1689m - would need an account and a token). Gives the same output: the block
    outputs - [cls, 4 registers, patches] - with a layer_norm without affine (without the model's own final norm)."""

    def __init__(self, model_name: str = "", image_size: int = 512):
        import timm
        from torchvision import transforms

        self.model = timm.create_model(TIMM_DINOV3, pretrained=True, num_classes=0, dynamic_img_size=True).eval()
        self.image_size = image_size
        self.transform = transforms.Compose([transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                                                  std=[0.229, 0.224, 0.225])])

    def to(self, device):
        self.model.to(device)

    def cuda(self):
        self.model.cuda()

    def cpu(self):
        self.model.cpu()

    def extract_features(self, image):
        import torch.nn.functional as F

        m = self.model
        x = m.patch_embed(image.to(m.patch_embed.proj.weight.dtype))
        x, rope = m._pos_embed(x)
        x = m.norm_pre(x)
        for blk in m.blocks:
            x = blk(x, rope=rope)
        return F.layer_norm(x, x.shape[-1:])

    def __call__(self, image):
        import numpy as np
        import torch
        from PIL import Image

        with torch.no_grad():
            if isinstance(image, list):
                image = [i.resize((self.image_size, self.image_size), Image.LANCZOS) for i in image]
                image = [torch.from_numpy(np.array(i.convert("RGB")).astype(np.float32) / 255).permute(2, 0, 1)
                         for i in image]
                image = torch.stack(image)
            return self.extract_features(self.transform(image.cuda()))


def block_nvdiffrast():
    """o_voxel imports nvdiffrast (NVIDIA non-commercial license), but the image -> 3D path does not use it: a stub
    takes its place that raises on any call - so nothing of it can run."""
    import types

    class Stub(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            raise RuntimeError("nvdiffrast is disabled (non-commercial license) – this code path needs it")

    root = Stub("nvdiffrast")
    root.__path__ = []
    sub = Stub("nvdiffrast.torch")
    root.__dict__["torch"] = sub
    sys.modules["nvdiffrast"] = root
    sys.modules["nvdiffrast.torch"] = sub


CELL = 4  # side of a texel / triangle cell


def export(mesh, out: str, target: int):
    """The TRELLIS.2 mesh -> a textured GLB, WITHOUT nvdiffrast (o_voxel.postprocess.to_glb bakes with nvdiffrast,
    which is under NVIDIA's non-commercial license). Only MIT parts: CuMesh (simplification, cuBVH) and FlexGEMM
    (sampling the color volume). Every triangle gets its own CELL x CELL cell in the atlas; the color of the cell's
    texels comes from the 3D color volume (as in to_glb: first projected back onto the original surface). This is
    only an intermediate file - the post-processing re-UVs and re-bakes it for the requested budget anyway."""
    import cumesh
    import numpy as np
    import torch
    import trimesh
    from flex_gemm.ops.grid_sample import grid_sample_3d
    from PIL import Image

    # the remesh branch of o_voxel.postprocess.to_glb (remesh=True): dual contouring rebuilds the raw mesh in a band
    # around the surface (and projects it back) - an almost closed mesh (14 boundary edges on a test character), but
    # about 2000 non-manifold edges and 1000 tiny tunnels remain; the carve in blender_post.py gets past those
    cm = cumesh.CuMesh()
    cm.init(mesh.vertices.cuda().float(), mesh.faces.cuda().int())
    cm.fill_holes(max_hole_perimeter=3e-2)
    v0, f0 = cm.read()
    v0, f0 = v0.cuda().float(), f0.cuda().int()
    bvh = cumesh.cuBVH(v0, f0)
    res = int(round(1.0 / float(mesh.voxel_size)))
    cm.init(*cumesh.remeshing.remesh_narrow_band_dc(
        v0, f0, center=torch.zeros(3, device="cuda"), scale=(res + 3) / res, resolution=res, band=1,
        project_back=0.9, bvh=bvh))
    # the DC mesh is NOT simplified here (CuMesh.simplify would leave more non-manifold edges); the post-processing
    # reduces it to the budget. Only a very large mesh is reduced (at the limit of a 4096^2 atlas)
    cell = CELL
    if cm.num_faces > (2048 // cell) ** 2:
        cell = 2
    if cm.num_faces > (4096 // cell) ** 2:
        cm.simplify((4096 // cell) ** 2)
    say(step="export", progress=None, text=f"{cm.num_faces} faces")
    v, f = cm.read()
    v, f = v.cuda().float(), f.cuda().long()
    n = len(f)
    g = int(np.ceil(np.sqrt(n)))
    size = g * cell
    # texel centers of a cell: barycentric weights in the triangle v0=(.5,.5), v1=(c-.5,.5), v2=(.5,c-.5)
    t = (torch.arange(cell, device="cuda").float()) / (cell - 1)
    tv, tu = torch.meshgrid(t, t, indexing="ij")
    s = (tu + tv).clamp(min=1.0)
    w1, w2 = (tu / s).reshape(-1), (tv / s).reshape(-1)
    w = torch.stack([1 - w1 - w2, w1, w2], 1)  # (C*C, 3)
    tri = v[f]  # (n, 3, 3)
    pos = torch.einsum("kj,njd->nkd", w, tri).reshape(-1, 3)
    _, fid, uvw = bvh.unsigned_distance(pos, return_uvw=True)
    pos = (v0[f0[fid.long()].long()] * uvw.unsqueeze(-1)).sum(1)
    aabb = torch.tensor([[-0.5] * 3, [0.5] * 3], device="cuda")
    vs = torch.tensor([mesh.voxel_size] * 3, device="cuda", dtype=torch.float32)
    grid = ((aabb[1] - aabb[0]) / vs).round().int()
    coords = mesh.coords.cuda().int()
    attrs = mesh.attrs.cuda().float()
    col = torch.zeros(len(pos), attrs.shape[1], device="cuda")
    for i in range(0, len(pos), 1 << 21):
        p = pos[i:i + (1 << 21)]
        col[i:i + len(p)] = grid_sample_3d(attrs, torch.cat([torch.zeros_like(coords[:, :1]), coords], 1),
                                           shape=torch.Size([1, attrs.shape[1], *grid.tolist()]),
                                           grid=((p - aabb[0]) / vs).reshape(1, -1, 3), mode="trilinear")
    rgb = (col[:, mesh.layout["base_color"]].clamp(0, 1) * 255).byte().reshape(n, cell, cell, 3).cpu().numpy()
    img = np.zeros((g * g, cell, cell, 3), np.uint8)
    img[:n] = rgb
    img = img.reshape(g, g, cell, cell, 3).transpose(0, 2, 1, 3, 4).reshape(size, size, 3)
    cx, cy = np.arange(n) % g, np.arange(n) // g
    corner = np.array([[0.5, 0.5], [cell - 0.5, 0.5], [0.5, cell - 0.5]])
    px = np.stack([cx, cy], 1)[:, None, :] * cell + corner[None]  # (n, 3, 2) in pixels, from the top
    uv = np.stack([px[..., 0] / size, 1 - px[..., 1] / size], -1).reshape(-1, 2)
    P = tri.reshape(-1, 3).cpu().numpy()
    P[:, 1], P[:, 2] = P[:, 2].copy(), -P[:, 1].copy()  # Z up -> Y up (as in to_glb)
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(img), metallicFactor=0.0,
                                              roughnessFactor=1.0)
    tm = trimesh.Trimesh(vertices=P, faces=np.arange(3 * n).reshape(-1, 3), process=False,
                         visual=trimesh.visual.TextureVisuals(uv=uv, material=mat))
    tm.export(out)  # with a PNG texture


DARK = 0.1  # a pixel darker than this (0..1 luminance) counts as "black"
DARK_SHARE = 0.25  # if this share of the subject is that dark, the background is not black


def prepare(image, bg: float | None = None):
    """TRELLIS.2's preprocess_image (crop to the bounding box of alpha > 0.8, at most 1024 px), but with a choosable
    background. The original premultiplies with the alpha, i.e. gives a black background: a figure in black clothes
    (shirt, trousers) then melts into it, the model sees only a face, arms and shoes in a dark square, and builds a
    block (in one test a 4.1 million triangle box). For a dark subject (more than DARK_SHARE of it below DARK) the
    background is mid gray, like in TripoSR - the silhouette shows on it. bg: forced background (0..1), None:
    automatic. Returns (RGB image, the background)."""
    import numpy as np
    from PIL import Image

    im = image.convert("RGBA")
    scale = min(1, 1024 / max(im.size))
    if scale < 1:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
    a = np.asarray(im).astype(np.float32) / 255
    alpha = a[..., 3]
    ys, xs = np.nonzero(alpha > 0.8)
    if not len(ys):
        raise RuntimeError("the image has no visible subject (empty alpha)")
    if bg is None:
        lum = a[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
        bg = 0.5 if float((lum[alpha > 0.8] < DARK).mean()) > DARK_SHARE else 0.0
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    size = int(max(xs.max() - xs.min(), ys.max() - ys.min()))
    rgb = a[..., :3] * alpha[..., None] + bg * (1 - alpha[..., None])
    out = Image.fromarray((rgb * 255).astype(np.uint8))
    # a crop beyond the image border would fill with black: paste onto a canvas of the background color
    x0, y0 = int(round(cx - size / 2)), int(round(cy - size / 2))
    canvas = Image.new("RGB", (size, size), (int(bg * 255),) * 3)
    canvas.paste(out, (-x0, -y0))
    return canvas, bg


def main():
    task = json.loads(open(sys.argv[1], encoding="utf-8").read())
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    import torch

    if not torch.cuda.is_available():
        say(error="no CUDA GPU is visible inside the TRELLIS.2 environment")
        return 2
    try:
        path = weights(task["weights"])
        say(step="load", progress=None, text=torch.cuda.get_device_name(0))
        from PIL import Image

        block_nvdiffrast()
        from trellis2.pipelines import Trellis2ImageTo3DPipeline, rembg

        from trellis2.modules import image_feature_extractor

        rembg.BiRefNet = NoRembg
        image_feature_extractor.DinoV3FeatureExtractor = TimmDinoV3  # no account / token
        pipe = Trellis2ImageTo3DPipeline.from_pretrained(path)
    except OSError as e:  # first run without network
        say(error=f"model download failed – the first run needs internet once, later runs are offline: {e}"[:400])
        return 4
    pipe.low_vram = bool(task.get("low_vram", True))
    pipe.cuda()
    image, bg = prepare(Image.open(task["image"]), task.get("background"))
    say(step="shape", progress=None, text=task["pipeline_type"] + (" (dark subject: gray background)" if bg else ""))
    try:
        mesh = pipe.run(image, seed=int(task.get("seed", 42)), pipeline_type=task["pipeline_type"],
                        preprocess_image=False)[0]
        say(step="export", progress=None, text="")
        export(mesh, task["out"], int(task["decimation_target"]))
    except torch.cuda.OutOfMemoryError as e:
        say(error=f"out of GPU memory ({e})".split("\n")[0][:300], oom=True)
        return 3
    say(done=True, out=task["out"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
