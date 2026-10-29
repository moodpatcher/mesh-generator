# Install

The goal: **the host stays clean.** Everything heavy runs in Docker (Blender, Pillow/numpy, TRELLIS.2, TripoSR);
the model weights live in this folder (`cache/`). On the host you need only two system things, which you install
once yourself, and one portable file set that lives inside this folder.

| what | where it lives | how |
|---|---|---|
| **Docker Desktop** (WSL2 backend) | system | you install it (admin + restart) |
| **NVIDIA driver** (`nvidia-smi` works) | system | you install it; only for the generation |
| **WSL2 memory 24 GB** | `%USERPROFILE%\.wslconfig` | `setup.ps1 -WslMemory 24` (opt-in), only for TRELLIS.2 |
| **Python 3.10+, no packages** | `.tools\python` (inside this folder) | `setup.ps1 -Python` – a portable zip, nothing is installed |
| Docker images | Docker | `setup.ps1 -Images ...`, or they build themselves on first use |
| model weights | `cache\` (inside this folder) | downloaded on the first generation |

To remove everything: delete this folder, `docker image rm kep3d-tools:1 kep3d-blender:4.5.15 kep3d-trellis2:1
kep3d-triposr:1`, and (if you set it) the `memory=` line in `.wslconfig`.

## Steps

1. **Check** (changes nothing): `.\setup.ps1`. It lists what is missing and which switch fixes it.
2. **Docker Desktop**, if missing: `winget install Docker.DockerDesktop`, restart, start it, make sure
   *Settings → General → Use the WSL 2 based engine* is on. Without an NVIDIA GPU everything but the generation
   still works (post-processing, rig, checks), and `--backend triposr` runs on the CPU, slowly.
3. **NVIDIA driver**: a current one from nvidia.com; `nvidia-smi` must work in a terminal. Docker Desktop then gives
   the containers the GPU by itself. `setup.ps1` tests this (`gpu in docker`).
4. **WSL2 memory** (TRELLIS.2 only; it needs about 16 GB, below that the container is killed with exit 137):
   `.\setup.ps1 -WslMemory 24`, then `wsl --shutdown` and restart Docker Desktop. The old `.wslconfig` is backed up
   next to it. Skip this if the machine has less than 32 GB of RAM and use `--backend triposr`.
5. **Python**: `.\setup.ps1 -Python`. It downloads `python-3.12.8-embed-amd64.zip` from python.org (about 10 MB)
   into `.tools\python`. `generate.cmd` and `setup.ps1` use it automatically. If you already have a Python 3.10+
   (not the Microsoft Store shortcut), you can skip this.
6. **Images**: `.\setup.ps1 -Images tools,blender` (small, a few minutes). The TRELLIS.2 image is about 20 GB and its
   first build can take hours (it compiles CUDA extensions): `.\setup.ps1 -Images trellis2`, or just let the first
   generation build it. After a network error, run it again: the finished layers are cached. If nldp's identical
   images are on the machine (`nldp-trellis2:2`, `nldp-triposr:1`, `nldp-blender:4.5.15`), they are retagged
   instead of rebuilt.
7. **Try it without a GPU** (about 2 minutes; needs `examples/james/`, see `CLAUDE.md`):
   `.\generate.ps1 examples\james\source.png -Hires examples\james\hires.glb -Triangles 3000`
   Every verdict should be `ok` (`skin_leak` may be `warn` with a few vertices), 3000 triangles, IoU 0.876.
8. **Weights**: the first real generation downloads a few GB into `cache/` (the only traffic besides the image
   builds); after that everything works offline.

Then: drag an image onto `generate.cmd`. `prompt.cmd` makes the prompt for the image.

## Optional: AI edits through Blender MCP (`ai.cmd`)

Only for the step after a run where an AI agent edits the model (for example puts a picture on the face):

- **A Blender window** on the host (4.x or 5.x; `winget install BlenderFoundation.Blender`). The pipeline itself needs
  no host Blender; this step needs a window. It is started with `--factory-startup` and nothing is installed into it.
- **An agent CLI**: Claude Code (`winget install Anthropic.ClaudeCode`) or Codex. Their login and billing are yours.
- **A small Docker image** with the MCP server (`kep3d-blender-mcp:2.2.1`, pinned): built on first use, or
  `.\setup.ps1 -Images blender-mcp`. The Blender add-on is taken out of that image into `.tools\mcp-for-blender\`.
- **Privacy**: prompts, pictures and what the agent sees in Blender go to the agent's provider. Telemetry of the MCP
  project is off; the only online source the agent may use is Poly Haven (free, CC0).
- Check without an agent: `.\ai.ps1 out\<name> -SelfTest`.

## Without Docker (not recommended)

`--mode local`: Blender 4.5+ on the host, `pip install pillow numpy`, and your own TRELLIS.2 / TripoSR environment.
See "Requirements, B" in `CLAUDE.md`. This is what makes the host messy; use Docker.

---

## For Claude Code (or any agent) that is asked to set this up

Installing changes the user's machine, so:

1. **Run `.\setup.ps1` first** (read-only) and show the user the table.
2. **Ask before each step that is not read-only**, saying exactly what it touches. A general "set it up" is not
   permission for the system-level or the large ones. Group the questions:
   - *inside this folder only, small:* `-Python` (10 MB download, `.tools\`), `-Images tools,blender`
     (a Docker build, about 1 GB, in Docker);
   - *large:* `-Images trellis2` (about 20 GB, hours, and about 16 GB of RAM while building), the weights download;
   - *outside this folder:* `-WslMemory` (edits `%USERPROFILE%\.wslconfig`; WSL and Docker must then be restarted,
     which kills running containers: ask again before `wsl --shutdown`).
3. **Never** install Docker Desktop or the NVIDIA driver yourself (admin rights, reboot): give the user the command
   and wait. Never put anything on the PATH, in the registry or in the user profile.
4. **Never start TRELLIS.2 while other memory-heavy work runs** and never twice at once; do not restart it after an
   out-of-memory kill without asking (see the rules in `CLAUDE.md`).
5. After the setup, **run the GPU-free test** (step 7) and report its verdicts as they are.
6. Do not tune or "fix" the license choices (no Hunyuan3D, no RMBG-2.0, no nvdiffrast).
