"""Step 1: the image without its background (RGBA), cropped to the subject, padded to a square (6% margin).

    python key.py input.png output.png

Three cases:
- the image is already transparent (PNG with alpha): its alpha stays, but scattered leftovers (e.g. the checkerboard
  grid of images from the web) are zeroed - otherwise TRELLIS.2 sees the grid as a surface and builds a box instead
  of the subject;
- magenta (255, 0, 255) background: chroma key with a soft edge, the magenta spill removed;
- any other single-color background: the color of the four corners is the background (soft-edged key).

Needs only Pillow + numpy (in Docker: the kep3d-tools image)."""

import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CHROMA = (255, 0, 255)


def chroma_key(img, lo=60.0, hi=140.0):
    a = np.asarray(img.convert("RGBA"), np.float32)
    rgb = a[..., :3]
    d = np.sqrt(((rgb - np.array(CHROMA, np.float32)) ** 2).sum(-1))
    alpha = np.clip((d - lo) / (hi - lo), 0, 1) * (a[..., 3] / 255.0)
    spill = np.maximum(np.minimum(rgb[..., 0], rgb[..., 2]) - rgb[..., 1], 0) * (1 - alpha)
    rgb = rgb - spill[..., None] * np.array([1, 0, 1], np.float32)
    out = np.dstack([np.clip(rgb + 0.5, 0, 255), alpha * 255 + 0.5]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def clean_alpha(im, keep=96, parts=16, min_part=0.02):
    """The subject: the connected part of the alpha above keep. The largest piece always stays (with its thin
    protrusions); of the separate pieces only the big ones (at least min_part of the largest, at most parts pieces).
    The original soft alpha stays around the kept part."""
    a = np.asarray(im).copy()
    mask = Image.fromarray(((a[..., 3] >= keep) * 255).astype(np.uint8))
    deep = np.asarray(mask.filter(ImageFilter.MinFilter(9)))  # scattered dots cannot be seeds
    seeds = np.argwhere(deep > 0)
    if not len(seeds):
        return im
    kept = np.zeros(deep.shape, bool)
    sizes = []
    work = mask.copy()
    for _ in range(parts):
        free = seeds[~kept[seeds[:, 0], seeds[:, 1]] & (np.asarray(work)[seeds[:, 0], seeds[:, 1]] == 255)]
        if not len(free):
            break
        y, x = (int(v) for v in free[len(free) // 2])
        ImageDraw.floodfill(work, (x, y), 128)
        comp = np.asarray(work) == 128
        sizes.append((int(comp.sum()), comp))
        ImageDraw.floodfill(work, (x, y), 64)
    if not sizes:
        return im
    top = max(n for n, _ in sizes)
    for n, comp in sizes:
        if n >= min_part * top:
            kept |= comp
    near = np.asarray(Image.fromarray((kept * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    a[..., 3] = np.where(near, a[..., 3], 0)
    return Image.fromarray(a, "RGBA")


def key_image(im, margin=0.06):
    im = im.convert("RGBA")
    a = np.asarray(im).astype(np.float32)
    if (a[..., 3] < 250).mean() < 0.02:  # no (meaningful) transparency
        corners = np.array([a[0, 0, :3], a[0, -1, :3], a[-1, 0, :3], a[-1, -1, :3]])
        bg = np.median(corners, axis=0)
        if np.linalg.norm(bg - np.array(CHROMA, np.float32)) < 90:
            im = chroma_key(im)
        else:
            d = np.min(np.linalg.norm(a[..., None, :3] - corners[None, None], axis=-1), axis=-1)
            a[..., 3] = np.clip((d - 18.0) / 28.0, 0, 1) * 255
            im = Image.fromarray(a.astype(np.uint8), "RGBA")
    else:
        im = clean_alpha(im)
    al = np.asarray(im)[..., 3]
    ys, xs = np.nonzero(al > 24)
    if not len(ys):
        raise SystemExit("ERROR: the image is empty after removing the background")
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    side = int(max(x1 - x0, y1 - y0) * (1 + 2 * margin))
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im.crop((x0, y0, x1, y1)), ((side - (x1 - x0)) // 2, (side - (y1 - y0)) // 2))
    return out


if __name__ == "__main__":
    key_image(Image.open(sys.argv[1])).save(sys.argv[2])
    print(f"keyed: {sys.argv[2]}")
