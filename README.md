![A character made by the kit](docs/images/header.png)

# mesh-generator

One image → a textured, rigged, low-poly PS1/PS2-style 3D model (GLB). Runs on your own PC.

## Requirements

- An NVIDIA graphics card
- Docker Desktop

## Install

Open this folder in Claude Code and say: **"install this"**. It follows [install.md](install.md) and asks before it
changes anything.

Manually: install Docker Desktop and the NVIDIA driver, then run `.\setup.ps1 -Python` and `.\setup.ps1` (it checks
everything and tells you what is missing; the Docker images build themselves on first use).

## Use

- **Drag your image onto `generate.cmd`.** It asks for the style and the maximum number of triangles, then writes
  the model to `out\<image name>\` (`model_rigged.glb`, `model.glb`, check pictures, `report.json`).
- **No image yet?** Run `prompt.cmd`: it generates the prompt for an image model.

More in [CLAUDE.md](CLAUDE.md). TRELLIS.2 and TripoSR are MIT, Blender is GPL (it runs as a separate program);
Hunyuan3D and other non-commercial parts are left out on purpose.
