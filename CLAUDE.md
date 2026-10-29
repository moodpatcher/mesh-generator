# Image → 3D kit – instructions for Claude Code (and anyone)

This folder turns **one image into a finished, textured, PS1/PS2-style low-poly 3D model** (GLB) without nldp. The
model works in Unity, Godot, Blender and anything else that reads glTF.

- Everything runs on this machine and is free for commercial use.
- It is the "Image → 3D" workshop of nldp (0.6.48), taken out on its own.
- Without nldp there is no skinning and no walk cycle (see "Animation").

```
python scripts/pipeline.py IMAGE OUTPUT_FOLDER --kind character --triangles 3000 --texture 512
```

In the output folder:

| file | what it is |
|---|---|
| `reference_keyed.png` | the image without its background, cropped to the subject – the generator works from this |
| `hires.glb` | the high-res mesh (TRELLIS.2: about 1 million triangles) – **keep it**, it makes the result exactly repeatable |
| `model.glb` | **the finished model**: low-poly, one texture atlas, real meters, soles at Y = 0, Y up, front +Z |
| `check/sheet-full.png` | the reference + the model from the front, three-quarter, side and back |
| `check/sheet-closeup-*.png` | close-ups (character: hips 50, shoulders 72, head 90 – percent of the height) |
| `check/*.png` | the single views of the sheets |
| `report.json` | every setting, every measurement, and the ok / warn / fail verdicts |

## Rules (always follow them)

1. **Only backends that run locally:** TRELLIS.2 (the default) or TripoSR. The image never leaves the machine.
   - The only network traffic is the first run downloading the model weights and building the Docker images.
   - After that, everything works offline.
2. **NEVER use or suggest Hunyuan3D.** Its license is not valid in the EU.
   - Nothing else with a non-commercial license either, e.g. TRELLIS.2's RMBG-2.0 background remover (CC BY-NC)
     or nvdiffrast (NVIDIA, non-commercial).
   - The runners avoid these on purpose; do not "fix" that back.
3. **Do not generate images** with a paid API (OpenAI etc.) without the user's explicit permission. If there is no
   good reference image, ask for one, or give the prompt (below) so the user can generate it.
4. TRELLIS.2 takes **about 16 GB of system memory** and an NVIDIA GPU for minutes.
   - Never run two at once.
   - Do not start it while other memory-heavy work is running.
   - If it was killed for lack of memory, do not restart it without asking.
5. **At most one retry**, and only for a clear fault: a cut-off subject, two objects, a wrong view, or a broken mesh.
   The fix is almost always a better reference image, not tuning the code to one model.
6. To retry with other settings (budget, texture, PS1), **do not generate again**: use
   `--hires <previous>/hires.glb`. It is free, takes about half a minute, and is bit-identical.

## Requirements

### A) With Docker (recommended, tested)

- **Windows 10/11 + Docker Desktop** (WSL2 backend), or **Linux + Docker** + the NVIDIA Container Toolkit.
- **An NVIDIA GPU** with a current driver (`nvidia-smi` must work).
  - TRELLIS.2 runs its smaller mode (512) automatically under 24 GB of VRAM. Tested on an RTX 3060 with 12 GB.
  - TripoSR needs about 6 GB of VRAM; without a GPU it runs on the CPU, slowly.
- **Memory (WSL2):** put this in `%USERPROFILE%\.wslconfig`:
  ```
  [wsl2]
  memory=24GB
  ```
  Then run `wsl --shutdown` and restart Docker Desktop. With less memory the TRELLIS.2 container gets killed
  (exit 137).
- **Disk:**
  - the TRELLIS.2 image is about 20 GB, and its first build can take hours (it compiles CUDA extensions);
  - the weights are a few GB more (they go into `cache/`, once);
  - the TripoSR image is smaller, the Blender image is about 1 GB.
- **Python 3.10+ on the host**, no packages: `pipeline.py` uses only the standard library.

The images build themselves on first use (`docker/*.Dockerfile`). After a network error, just run again: the
finished layers stay cached. If the machine already has nldp's identical images (`nldp-trellis2:2`,
`nldp-triposr:1`, `nldp-blender:4.5.15`), the pipeline retags them instead of rebuilding.

### B) Without Docker (`--mode local`)

- **Blender 4.5 or newer** (tested: 4.5.15 in Docker, 5.2 on Windows).
  - `pipeline.py` finds `blender` on the PATH, under `C:\Program Files\Blender Foundation`, or from the `BLENDER`
    environment variable.
  - Or give it yourself: `--blender "C:\…\blender.exe"`.
- **For the background removal:** a Python with Pillow and numpy (`pip install pillow numpy`). By default this is
  the Python that runs `pipeline.py`; `--python` picks another one.
- **For the generation:** your own TRELLIS.2 or TripoSR environment:
  `--trellis-python …/python --trellis-repo …/TRELLIS.2`, or `--triposr-python … --triposr-repo …`.
  - TRELLIS.2 is tested on Linux (per its README). Natively on Windows it was **not tried**.
  - On Windows without Docker, the way is WSL2 (Ubuntu 22.04): install it there, and run `pipeline.py` there too
    (Blender can be the Linux one).
  - The install steps are exactly the lines of `docker/trellis2.Dockerfile`; follow those:
    - CUDA 12.4, torch 2.6.0 + torchvision 0.21.0 (cu124);
    - clone the TRELLIS.2 repo, replace Eigen, and run `setup.sh --basic --flash-attn --cumesh --o-voxel --flexgemm`;
    - then `pip install huggingface_hub tqdm trimesh "timm>=1.0.20"`.
  - For TripoSR, follow the lines of `docker/triposr.Dockerfile`.
  - This Docker-free generation was **not tested** in this kit. The Docker-free Blender and background removal are
    tested.
- **Mixed:** e.g. Blender installed locally, the generation in Docker: `--mode local --gen-mode docker`.

## Quick test without a GPU (2 minutes)

`examples/` is **not in the git repository** (the high-res meshes are 80–110 MB each). It only exists in a local
copy of the kit. Without it, run the same test with any `hires.glb` from an earlier run and the matching
`reference_keyed.png`.

`examples/james/` has a ready high-res mesh from an earlier TRELLIS.2 run. With it, only the post-processing and the
checks run:

```
python scripts/pipeline.py examples/james/source.png out/james --kind character --triangles 3000 --texture 512 --hires examples/james/hires.glb
```

**Expected result** (the full `report.json` and the pictures are in `examples/james/expected/`):

- every verdict `ok`;
- 3000 triangles, outline match 0.876, 215.8 px/m, 0 open edges, height 1.757 m;
- with Docker (Blender 4.5.15), `model_md5` is `8440c47ad10a6e06f02ec1343ee3d8c1`, the same as there.

With another Blender version (e.g. 5.2) the GLB's md5 differs, but the measurements are the same. With the same
Blender and the same `hires.glb`, every run is bit-identical.

If this works, the kit works. Compare `out/james/check/sheet-*.png` with `examples/james/expected/check/`.

## Demos

The sheets of the best runs are in `docs/images/` (shown in `README.md`). The full runs are in `examples/`
(local copy only). These are real runs on an RTX 3060 (12 GB VRAM, so TRELLIS.2 ran its 512 mode), Docker, Blender 4.5.15.
Each has `source.png` and `result/` with:
- `model.glb`, `hires.glb` and `check/sheet-*.png`;
- `report.json`, whose `command` is the exact command.

To get exactly the same `model.glb`, run that command with `--hires examples/<name>/result/hires.glb` added. Without
it, a new generation gives a similar result.

| demo | command (short) | gen. time | result | what it shows |
|---|---|---|---|---|
| `leon-trellis2-ps2` | character, 3000, 512, trellis2 | 261 s | all ok: 2998 tris, IoU 0.87, 207 px/m, closed | good shape and face. The blue uniform came out almost black (the generator's color: mean 27 in the high-res mesh vs 46 in the reference; the bake keeps it, 28), and the "R.P.D." letters are unreadable |
| `harry-trellis2-ps1` | character, 1000, 128, `--ps1`, trellis2 | 203 s | all ok: 1000 tris, IoU 0.828, 49 px/m, closed | a real PS1 look: blocky dithered texels, the jacket and the build are right; the green trousers became gray-blue |
| `trousers-trellis2-object` | object, 800, 256, height 1.05, trellis2 | 299 s | all ok: 800 tris, best-view IoU 0.857 (front 0.65), 115 px/m | an object from a three-quarter reference: belt, buckle and thigh pouch kept; the denim blue became dark gray. `generation-report.json` is the generation run, `report.json` the re-check with `--hires` (identical `model_md5`) |
| `lara-triposr-object` | object, 800, 256, height 1.7, triposr | 23 s | **silhouette warn**: IoU 0.707, 6 open edges | **a bad example on purpose**: a whole character from a three-quarter reference, with TripoSR – the body leans and twists, and the face is a smear. A character needs a front A-pose reference and TRELLIS.2 |
| `james` | character, 3000, 512, `--hires` | – | all ok: 3000 tris, IoU 0.876, 216 px/m | the GPU-free test; `expected/` is the reference result |

## Options

| option | value | when |
|---|---|---|
| `--kind` | `object` / `character` / `creature` | characters / creatures get a closed mesh (see "The steps"); the default height differs |
| `--triangles` | PS1 prop 300–800 (500), PS1 character ~1000, PS2 2000–5000 (3000) | default: object 500, character 1000 |
| `--texture` | 64 / 128 / 256 / 512 | PS1: 64–256, PS2: 256–512; default 128 |
| `--height` | meters | the real height (bottle 0.3, chair 0.9, door 2.1); 0 = object 1.0, character 1.75, creature 1.6 |
| `--ps1` | – | 15-bit color with a 4×4 dither on the atlas (the PS1 look) |
| `--remesh` | – | always carve a closed solid before the decimation (for holey, open meshes; automatic for characters) |
| `--backend` | `trellis2` / `triposr` | TRELLIS.2: far better (face, hair, clothes). TripoSR: light, coarse shape, distorted face – only for simple props or a weak machine |
| `--vram` | `auto` / `low` | TRELLIS.2: auto picks low under 24 GB; try low on a memory error |
| `--seed` | 42 | the TRELLIS.2 random seed (even the same seed is not bit-exact on the GPU) |
| `--hires` | GLB | an existing high-res mesh: the generation is skipped |
| `--close` | e.g. `0.5,0.72,0.9` | heights of the close-ups (fractions); the default for characters (hips, shoulders, head) |
| `--mode` / `--gen-mode` | `auto` / `docker` / `local` | see "Requirements" |
| `--cache` | folder | where the weights are kept (default `cache/`) |

**Always tell the user the exact command**, together with the `settings` part of `report.json`, so they get the
same result if they repeat it. An exact repeat needs the `hires.glb`; a new generation only gives a similar result.

## A good reference image

Most of the result depends on the reference image. If the user's image is not like this, tell them what is wrong
with it.

- **One** figure or object, **entirely** visible with a small margin; nothing cut off, centered.
- **A plain single-color background:** a transparent PNG, magenta (#FF00FF), or any single color. No scene, floor,
  cast shadow, text or watermark.
- Almost orthographic, no distortion; soft, even light; true colors.
- **Object:** a three-quarter view from slightly above (about 30° to the side, 20° from above).
- **Character: from the front, in an A-pose.**
  - Both arms straight, about 45° down and out.
  - Background visible between the hands and the hips, between the arms and the torso, and between the legs.
  - Nothing in the hands, nothing covering the body.
  - Arms touching the torso, or a long coat or skirt, grow together on the model; nldp could only partly handle
    this when rigging.
- Transparent PNGs from the web sometimes have the checkerboard baked in. The key step cleans that up, but look at
  `reference_keyed.png`.
- For a figure in dark clothes the runner puts a gray background behind the image by itself; on black, TRELLIS.2
  would build a box.

**Prompts, if the user generates the image themselves.** These are the ones nldp uses; replace `[…]` with the
description.

- **Character:**
  > Reference image for turning ONE character into a 3D model: [description: outfit, build, colors, era]. A single
  > figure, full body from the top of the head to the soles, entirely visible with a small margin, centered.
  > Straight front view, almost orthographic. MANDATORY A-pose: both arms straight, angled about 45 degrees down and
  > out from the shoulders, hands open, a clear gap between each arm and the torso and between the hands and the
  > hips, nothing in the hands; legs straight, feet a little apart with a gap between the legs. Neutral soft diffuse
  > light, no cast shadows, true colors. Plain single-color background (pure magenta #FF00FF if transparency is not
  > available), no text. PS1 / PS2 era game character style – clear silhouette, simple readable forms,
  > hand-painted texture look.
- **Object:**
  > Reference image for turning ONE object into a 3D model: [description]. A single object, entirely visible with a
  > small margin, centered. Three-quarter view from slightly above (about 30° to the side, 20° from above), almost
  > orthographic. Neutral soft diffuse light, no cast shadows, no strong highlights. Plain single-color background
  > (magenta #FF00FF if no transparency), no text. PS1 / PS2 era game prop style – simple forms, hand-painted
  > texture look.

## When it is worth it, and when not

- **Worth it** for organic shapes:
  - monsters, creatures, animals, characters;
  - tree stumps, rocks, ruins, rubble;
  - complex props: a carved statue, a skull, an engine block, a sack, clothing.
- **Not worth it** for simple architecture and hard-edged shapes: walls, floors, doors, crates, tables, shelves,
  pipes, stairs. Built from primitives (by hand in Blender or with a script) they are cleaner, lighter, and keep
  their straight edges. A model made from an image makes these lumpy.

## The steps (what `pipeline.py` does)

1. **key** (`scripts/key.py`):
   - removes the background: the alpha of a transparent image, a chroma key for magenta, otherwise by the corner
     colors;
   - cleans up stray alpha;
   - crops to the subject and pads to a square.
2. **hires** (`scripts/trellis2_runner.py` / `triposr_runner.py`, on the GPU):
   - makes the high-res colored mesh;
   - TRELLIS.2 runs the official `Trellis2ImageTo3DPipeline`, with the background remover and nvdiffrast replaced
     (licenses), and the DINOv3 encoder taken from timm's public copy (no account, no token);
   - the mesh gets a texture from the color volume.
3. **post** (`scripts/blender_post.py`, Blender, headless):
   - **Clean-up:** duplicate vertices, tiny floating pieces, normals.
   - **Size:** scales to the given height, puts the soles on the ground, centers horizontally.
   - **Low-poly:** Collapse decimation (QEM) to the budget. If it stalls, or for a character whose mesh is open or
     double-layered, it first **carves a solid** around the mesh (voxel grid flooded from outside, Voxel Remesh)
     and decimates that. The result is a closed, one-piece mesh.
   - **UV and bake:** Smart UV Project, then a Cycles bake (Emit, selected-to-active): the color of the high-res
     mesh goes onto the atlas, without shadows; the material is matte.
   - **Orientation:** of the four side views, the one matching the reference outline best becomes the front.
   - **PS1 (if asked):** reduces the atlas to 15-bit color.
   - **Export:** GLB.
4. **check** (`scripts/blender_check.py`, Blender):
   - measurements, and the outline compared with the reference;
   - renders of 4 views, full figure and close-ups, plus the sheets.

## Checking – ALWAYS do all of this before saying "done"

### 1. `report.json` → `verdicts`

| verdict | ok when | if not |
|---|---|---|
| `triangles` | 85–100% of the budget | above: the decimation stalled → `--remesh`; far below: the mesh was tiny |
| `height` | the requested height ±1% | a scaling bug – report it |
| `on_ground` | \|min Z\| of the soles ≤ 2 mm | a bug – report it |
| `centered` | horizontal center ≤ 2% of the height | an asymmetric shape (e.g. one arm stretched out) can give warn – check the pictures |
| `silhouette` | outline match (IoU) ≥ 0.80; 0.70–0.80: warn; < 0.70: fail. Character / creature: from the front. Object: the best viewing angle (`check.best_view`, yaw every 15°, pitch 0 or 20°), because an object reference is a three-quarter view | a bad reference (see below) or a wrong orientation |
| `closed_mesh` | character / creature: 0 open or non-manifold edges | `--remesh` |
| `texture` | the requested atlas size | a bug – report it |

### 2. Measured, expected values

**Earlier runs in nldp** (character, 1.75 m, 512 texture, TRELLIS.2):

| model | triangles | IoU | note |
|---|---|---|---|
| James (examples/james) | 3000 | 0.876–0.889 | a good A-pose reference |
| Leon | 2998 | 0.875 | |
| Harry A / B | 3000 | 0.82 / 0.82 | |
| Harry A, TripoSR, 1000 | 1000 | 0.92 | high IoU, but a coarse shape and a distorted face – IoU is not everything |
| Leon 1000, bad reference | 1000 | **0.71** | the arms touched the body – this is where "bad" starts |

**Demo results of this kit:** see the table under "Demos" above.

- **Texel density:** for a 1.75 m figure, roughly 0.35–0.45 × the texture size in px/m. Measured: 512 →
  190–225 px/m, 128 → 44 px/m. Much less means a poorly used atlas.
- **Time:**
  - post-processing about 25–70 s (at 3000 triangles), checking about 50 s;
  - TRELLIS.2 took 203–299 s on an RTX 3060 (512 mode), TripoSR 23 s;
  - the first run is much longer: it also builds the image and downloads the weights.

### 3. Look at the pictures

Read `check/sheet-full.png` and, for a character, all `check/sheet-closeup-*.png`. Look for:

- **Shape:** is it the reference's shape? No box instead of the figure, no cut-off part, no arms grown into the
  torso, no missing legs?
- **Front:** does the front view show the reference's view? (`post.front_view` tells which one was picked.)
- **Surface:** no holes, no black wedges, no floating pieces?
- **Texture:** are the face and the clothes recognizable? No smeared dark spots, no streaky transitions?
  - Known weakness: on thin parts (the back of the hand, the wrist) the bake sometimes gives a dark spot, because
    the low-poly surface is far from the high-res one there.
  - Then try a larger budget, a larger texture, or a better reference (hands further from the body). Do not solve
    this by tuning the code.
  - The colors come from the generator. TRELLIS.2 (at least in its 512 mode) often makes colors darker and less
    saturated than the reference: Leon's blue uniform turned almost black, and the denim became dark gray. It
    sometimes also shifts a hue: James's blond hair turned reddish.
  - That is not a bake error. To check, compare the mean color of `hires.glb` with that of `model.glb`: the bake
    keeps it within a few levels.
  - Tell the user. Fixing it means recoloring the atlas (e.g. a levels / saturation edit on the texture in
    Blender), not changing the pipeline.
  - Small lettering and logos (like "R.P.D.") do not survive at PS1/PS2 budgets.
- **The back** is the generator's own guess (the image only showed the front). That is normal, but check that it is
  believable.

**Only say it is done** when every verdict is `ok` (or you checked and explained the reason for a `warn`) and the
pictures show no fault. Give the user:
- the path of `model.glb`;
- the command;
- the main measurements;
- and what you saw in the pictures, faults included.

### Faults and what to do

| symptom | cause | what to do |
|---|---|---|
| a box / block instead of the figure | unclean background (checkerboard), or a dark figure on black | look at `reference_keyed.png`; use a cleaner image |
| IoU < 0.7, limbs grown together | the reference is not an A-pose, or an arm touches the body | a new A-pose reference |
| good `silhouette`, but the wrong front | the outline cannot tell the front from the back | rotate the GLB by 180° (in Blender), or report it |
| exit 137 / "killed" | not enough memory | `.wslconfig` 24 GB; nothing else running; `--vram low` |
| "out of GPU memory" | not enough VRAM | `--vram low`, or `--backend triposr` |
| "no NVIDIA GPU" | no driver, or Docker does not see the GPU | `nvidia-smi`; Docker Desktop → WSL2 + GPU support |
| the Docker build stops on a network error | GitHub / PyPI overloaded | run again – the finished layers are kept |
| the image is empty after key | the whole image looked like background | another background color, or a transparent PNG |

## Blender MCP (optional)

If a Blender MCP server is connected to Claude Code (e.g. the `blender-mcp` add-on in the user's open Blender), the
check can also be done live:

- import `model.glb` (`bpy.ops.import_scene.gltf`) and look at it in a viewport screenshot;
- look at the material and the atlas, and check the size (Z is up in Blender; 1 unit = 1 m).

The pipeline itself does not need it: `pipeline.py` runs a headless Blender. Through MCP, do **not** edit the mesh by
hand or tune the code to a single model. The fix is a better reference, or other settings with `--hires`.

## Animation

`model.glb` is static, a single mesh.

- To animate it, add an armature in Blender and bind it with automatic weights (Armature → With Automatic
  Weights), then animate.
- The A-pose reference is what makes this work: the arms stand away from the body.
- nldp's own skinning and walk generator are not part of this kit.

## Licenses

- **TRELLIS.2:** MIT (code and weights, Microsoft); CuMesh and FlexGEMM are MIT too.
- **The DINOv3 image encoder:** DINOv3 License. Commercial use is allowed, but "Built with DINOv3" must be shown.
- **TripoSR:** MIT (code and weights).
- **Blender:** GPL. It runs as a separate program; the output (the model) is yours.
- **Left out:** RMBG-2.0 (CC BY-NC), nvdiffrast and nvdiffrec (NVIDIA, non-commercial), and Hunyuan3D (EU).
- **The reference image:** its rights belong to the user or its original author. A model of a known game character
  cannot be used commercially in your own project just because the licenses of the tools are fine. The demo
  characters here are only tests.

## Files

```
CLAUDE.md                 this guide
README.md                 a short summary for people
scripts/pipeline.py       the whole run (standard library only)
scripts/key.py            background removal (Pillow + numpy)
scripts/trellis2_runner.py, triposr_runner.py   the generation, in the container / your own environment
scripts/blender_post.py   post-processing (Blender)
scripts/blender_check.py  measurements + renders + sheets (Blender)
docker/*.Dockerfile       trellis2, triposr, blender (4.5.15), tools (Pillow + numpy)
docs/images/              sheets of the best demo runs (README)
examples/james/           source + a ready hires.glb + the expected result (expected/) – local copy only
examples/*/               demo runs: source.png + result/ (model.glb, hires.glb, check/, report.json) – local only
cache/                    the model weights (created on the first generation)
```
