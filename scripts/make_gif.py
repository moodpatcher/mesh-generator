"""Step 5b (Pillow): the frames of blender_anim.py -> one looping GIF, the views side by side.
  python make_gif.py FRAMES_DIR OUT.gif [fps]
Frames are <view>-<index>.png. One shared palette for all frames (no flicker), no dithering."""

import os
import sys

from PIL import Image


def main():
    src, out = sys.argv[1], sys.argv[2]
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
    files = sorted(f for f in os.listdir(src) if f.endswith(".png"))
    views = sorted({f.rsplit("-", 1)[0] for f in files})
    count = min(len([f for f in files if f.startswith(v + "-")]) for v in views)
    frames = []
    for i in range(count):
        parts = [Image.open(os.path.join(src, f"{v}-{i:04d}.png")).convert("RGB") for v in views]
        row = Image.new("RGB", (sum(p.width for p in parts), max(p.height for p in parts)))
        x = 0
        for p in parts:
            row.paste(p, (x, 0))
            x += p.width
        frames.append(row)
    sample = frames[::max(1, len(frames) // 6)]
    montage = Image.new("RGB", (sample[0].width, sample[0].height * len(sample)))
    for k, f in enumerate(sample):
        montage.paste(f, (0, k * f.height))
    pal = montage.quantize(colors=200)
    q = [f.quantize(palette=pal, dither=0) for f in frames]
    q[0].save(out, save_all=True, append_images=q[1:], duration=max(20, round(1000 / fps)), loop=0, disposal=1)
    print(f"{out}: {count} frames, {len(views)} views, {os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()
