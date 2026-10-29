"""Image -> a finished, textured PS1/PS2-style low-poly 3D model (GLB), with one command. Needs only the Python
standard library (3.10+); everything else runs in Docker, or with the given local programs.

    python pipeline.py IMAGE OUTPUT_FOLDER [options]

Steps (each result stays in the output folder):
  1. key    - background removed, cropped to the subject           -> reference_keyed.png
  2. hires  - TRELLIS.2 or TripoSR (GPU)                            -> hires.glb   (--hires: an existing mesh, skipped)
  3. post   - Blender: clean-up, size, low-poly, UV, bake, orientation, (PS1)   -> model.glb
  4. check  - Blender: measurements + 4 views + close-ups + sheets  -> check/*.png, report.json

Mode (--mode):
  docker - everything in containers (the default when Docker is running). The images are built on first use
           (docker/*.Dockerfile); the TRELLIS.2 one can take hours and many GB.
  local  - without Docker: --blender (blender.exe / blender), this Python for the key step (Pillow + numpy),
           --trellis-python + --trellis-repo or --triposr-python + --triposr-repo for the generation.
Mixed works too: e.g. --mode local (Blender installed) with the generation in Docker: --gen-mode docker.

Repeatability: TRELLIS.2 is not bit-exact on the GPU (two runs: cm-level differences on the finished model in
places). The exact same result comes from the saved hires.glb: --hires <folder>/hires.glb + the same settings."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SCRIPTS = KIT / "scripts"
DOCKER_DIR = KIT / "docker"
IMAGES = {  # tag, Dockerfile, existing images with the same content (e.g. nldp's on this machine) - can be retagged
    "trellis2": ("kep3d-trellis2:1", "trellis2.Dockerfile", ["nldp-trellis2:2"]),
    "triposr": ("kep3d-triposr:1", "triposr.Dockerfile", ["nldp-triposr:1"]),
    "blender": ("kep3d-blender:4.5.15", "blender.Dockerfile", ["nldp-blender:4.5.15"]),
    "tools": ("kep3d-tools:1", "tools.Dockerfile", []),
}
HEIGHT = {"object": 1.0, "character": 1.75, "creature": 1.6}
TRIANGLES = {"object": 500, "character": 1000, "creature": 1000}
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Fail(RuntimeError):
    pass


def log(step, text):
    print(f"[{time.strftime('%H:%M:%S')}] {step}: {text}", flush=True)


# --- environment -------------------------------------------------------------------------------------------------------


def docker_exe():
    d = shutil.which("docker")
    if d:
        return d
    p = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    return str(p) if p.exists() else None


def docker_running(d):
    try:
        return subprocess.run([d, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=60,
                              creationflags=FLAGS).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def gpus():
    """[{name, vram_gb, cap}] from nvidia-smi ([] without NVIDIA)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=30, creationflags=FLAGS)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for ln in r.stdout.splitlines():
        p = [x.strip() for x in ln.split(",")]
        if len(p) >= 3:
            try:
                out.append({"name": p[0], "vram_gb": round(float(p[1]) / 1024, 1), "cap": p[2]})
            except ValueError:
                pass
    return out


def find_blender(custom):
    if custom:
        return custom
    if os.environ.get("BLENDER"):
        return os.environ["BLENDER"]
    found = shutil.which("blender")
    if found:
        return found
    for base in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Blender Foundation"):
        if base.is_dir():
            exes = sorted(base.glob("**/blender.exe"), key=str, reverse=True)
            if exes:
                return str(exes[0])
    return None


def image_ready(d, tag):
    return subprocess.run([d, "image", "inspect", tag], capture_output=True, creationflags=FLAGS).returncode == 0


def ensure_image(d, key, build_args=()):
    tag, dockerfile, aliases = IMAGES[key]
    if image_ready(d, tag):
        return tag
    for a in aliases:
        if image_ready(d, a):
            log("docker", f"{a} -> {tag} (same Dockerfile, tagged instead of rebuilding)")
            subprocess.run([d, "tag", a, tag], check=True, creationflags=FLAGS)
            return tag
    log("docker", f"building {tag} from docker/{dockerfile} (first run only; TRELLIS.2: may take hours, ~20 GB)")
    args = [x for kv in build_args for x in ("--build-arg", kv)]
    r = subprocess.run([d, "build", "--progress=plain", *args, "-t", tag, "-f", str(DOCKER_DIR / dockerfile),
                        str(DOCKER_DIR)], creationflags=FLAGS)
    if r.returncode != 0:
        raise Fail(f"docker build of {tag} failed (exit {r.returncode}) - a network error: just run again, the "
                   "finished layers are cached")
    return tag


def stream(cmd, step, timeout, env=None, cwd=None):
    """The running command's output: the runners' JSON lines ({step, progress, text} / {error} / {done}) readably."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=env, cwd=cwd, creationflags=FLAGS)
    tail, last, t0 = [], {}, time.monotonic()
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("{"):
            try:
                msg = json.loads(line)
                last.update(msg)
                if "step" in msg:
                    pr = f" {msg['progress']:.0%}" if isinstance(msg.get("progress"), (int, float)) else ""
                    log(step, f"{msg['step']}{pr} {msg.get('text', '')}")
                continue
            except ValueError:
                pass
        tail = (tail + [line])[-40:]
        if time.monotonic() - t0 > timeout:
            proc.kill()
            raise Fail(f"{step}: timed out after {timeout} s")
    return proc.wait(), tail, last


# --- steps -----------------------------------------------------------------------------------------------------------


def step_key(a, out, d):
    src = out / ("input" + Path(a.image).suffix.lower())
    if Path(a.image).resolve() != src.resolve():
        shutil.copy(a.image, src)
    if d:
        tag = ensure_image(d, "tools")
        cmd = [d, "run", "--rm", "-v", f"{out}:/work", "-v", f"{SCRIPTS}:/s:ro", tag, "python", "/s/key.py",
               f"/work/{src.name}", "/work/reference_keyed.png"]
    else:
        cmd = [a.python, str(SCRIPTS / "key.py"), str(src), str(out / "reference_keyed.png")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=FLAGS)
    if r.returncode != 0:
        raise Fail("key failed (local mode needs Pillow + numpy: pip install pillow numpy):\n" + (r.stderr or r.stdout)[-1500:])
    log("key", "reference_keyed.png")


def step_hires(a, out, d):
    dst = out / "hires.glb"
    if a.hires:
        if Path(a.hires).resolve() != dst.resolve():
            shutil.copy(a.hires, dst)
        log("hires", f"reused {a.hires} (no generation)")
        return {"reused": str(a.hires)}
    g = gpus()
    task = {"image": "/work/reference_keyed.png", "out": "/work/hires.glb", "seed": a.seed}
    if a.backend == "trellis2":
        small = bool(g) and max(x["vram_gb"] for x in g) < 24
        low = a.vram == "low" or (a.vram == "auto" and small)
        task.update(weights="microsoft/TRELLIS.2-4B", pipeline_type="512" if low else "1024_cascade", low_vram=low,
                    decimation_target=200000, texture_size=2048)
        if not g and not a.trellis_python:
            raise Fail("TRELLIS.2 needs an NVIDIA GPU (nvidia-smi found none) - try --backend triposr")
        runner, timeout = "trellis2_runner.py", 3600
    else:
        task.update(weights="stabilityai/TripoSR", resolution=256, decimation_target=100000)
        runner, timeout = "triposr_runner.py", 1800
    local_py = a.trellis_python if a.backend == "trellis2" else a.triposr_python
    if a.gen_mode == "local" or local_py:
        if not local_py:
            raise Fail(f"local mode: --{a.backend}-python is required (the Python of your {a.backend} environment)")
        repo = a.trellis_repo if a.backend == "trellis2" else a.triposr_repo
        task.update(image=str(out / "reference_keyed.png"), out=str(dst))
        (out / "gen_task.json").write_text(json.dumps(task), encoding="utf-8")
        env = {**os.environ, "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}
        if repo:
            env["PYTHONPATH"] = str(repo)
        cmd, cwd = [local_py, str(SCRIPTS / runner), str(out / "gen_task.json")], repo or None
    else:
        if not d:
            raise Fail("Docker is not running - start Docker Desktop, or use --gen-mode local")
        caps = sorted({x["cap"] for x in g})
        build = [f"CUDA_ARCHS={';'.join(caps)}"] if caps else []
        tag = ensure_image(d, a.backend, build + ["MAX_JOBS=4"])
        (out / "gen_task.json").write_text(json.dumps(task), encoding="utf-8")
        cache = Path(a.cache).resolve()
        cache.mkdir(parents=True, exist_ok=True)
        gpu = ["--gpus", "all"] if g else []
        cmd = [d, "run", "--rm", *gpu, "-v", f"{out}:/work", "-v", f"{cache}:/cache", "-v", f"{SCRIPTS}:/s:ro", tag,
               "python", f"/s/{runner}", "/work/gen_task.json"]
        cwd = env = None
    t0 = time.monotonic()
    code, tail, last = stream(cmd, a.backend, timeout, env, cwd)
    if last.get("error"):
        hint = " - try --vram low, or --backend triposr" if last.get("oom") else ""
        raise Fail(f"{a.backend}: {last['error']}{hint}")
    if code == 137:
        raise Fail(f"{a.backend}: killed - out of memory (WSL2: raise memory in %USERPROFILE%\\.wslconfig, e.g. 24GB)")
    if code != 0 or not dst.exists():
        raise Fail(f"{a.backend} failed (exit {code}):\n" + "\n".join(tail[-15:]))
    log("hires", f"hires.glb in {time.monotonic() - t0:.0f} s")
    return {"generated": a.backend, "seed": a.seed, "pipeline_type": task.get("pipeline_type"),
            "seconds": round(time.monotonic() - t0)}


def blender(a, out, d, script, task, timeout=1800):
    """One Blender task (headless), in Docker or with the local Blender. The task's paths are inside the output
    folder, given relative to it."""
    name = Path(script).stem
    if a.blender_mode == "docker":
        tag = ensure_image(d, "blender")
        inside = {k: (f"/work/{v}" if isinstance(v, str) and k in ("glb", "out", "reference") else v)
                  for k, v in task.items()}
        (out / f"{name}.json").write_text(json.dumps(inside), encoding="utf-8")
        cmd = [d, "run", "--rm", "-v", f"{out}:/work", "-v", f"{SCRIPTS}:/s:ro", tag, "-b", "--factory-startup",
               "--python", f"/s/{script}", "--", f"/work/{name}.json"]
    else:
        full = {k: (str(out / v) if isinstance(v, str) and k in ("glb", "out", "reference") else v)
                for k, v in task.items()}
        (out / f"{name}.json").write_text(json.dumps(full), encoding="utf-8")
        cmd = [a.blender_exe, "-b", "--factory-startup", "--python", str(SCRIPTS / script), "--",
               str(out / f"{name}.json")]
    res_file = out / f"{name}.json.out.json"
    res_file.unlink(missing_ok=True)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=FLAGS)
    if not res_file.exists():
        tail = (r.stderr or r.stdout or b"").decode("utf-8", "replace")[-1500:]
        raise Fail(f"Blender ({script}) failed (exit {r.returncode}):\n{tail}")
    res = json.loads(res_file.read_text(encoding="utf-8"))
    if not res.get("ok"):
        raise Fail(f"Blender ({script}): {res.get('error')}")
    return res


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def verdicts(a, height, post, chk):
    """The expected result, point by point: ok | warn | fail (see "Checking" in CLAUDE.md)."""
    v = {}
    t = chk["triangles"]
    v["triangles"] = "ok" if 0.85 * a.triangles <= t <= a.triangles else ("warn" if t <= 1.05 * a.triangles else "fail")
    v["height"] = "ok" if abs(chk["height_m"] - height) <= 0.01 * height else "fail"
    v["on_ground"] = "ok" if abs(chk["min_z_m"]) <= 0.002 else "fail"
    v["centered"] = "ok" if chk["center_offset_m"] <= 0.02 * height else "warn"
    s = chk.get("silhouette_iou")
    if a.kind == "object" and chk.get("best_view"):  # an object reference is a three-quarter view from above
        s = chk["best_view"]["iou"]
    if s is not None:
        v["silhouette"] = "ok" if s >= 0.80 else ("warn" if s >= 0.70 else "fail")
    if a.kind != "object":
        v["closed_mesh"] = "ok" if chk["open_or_nonmanifold_edges"] == 0 else "warn"
    v["texture"] = "ok" if chk["textures"] and chk["textures"][0][0] == a.texture else "fail"
    return v


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", help="the source image (PNG/JPG; transparent, magenta or plain single-color background)")
    p.add_argument("out", help="output folder (created)")
    p.add_argument("--kind", choices=("object", "character", "creature"), default="object")
    p.add_argument("--triangles", type=int, help="budget: PS1 prop 300-800, PS1 character ~1000, PS2 2000-5000")
    p.add_argument("--texture", type=int, choices=(64, 128, 256, 512), default=128, help="atlas size (px)")
    p.add_argument("--height", type=float, default=0.0, help="real height in m (0: object 1.0, character 1.75, "
                                                              "creature 1.6)")
    p.add_argument("--ps1", action="store_true", help="15-bit color with 4x4 dither on the atlas")
    p.add_argument("--remesh", action="store_true", help="always carve a closed solid before decimation")
    p.add_argument("--backend", choices=("trellis2", "triposr"), default="trellis2")
    p.add_argument("--vram", choices=("auto", "low"), default="auto", help="TRELLIS.2: auto = low under 24 GB")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--hires", help="reuse a saved high-res GLB (skips generation; exactly repeatable)")
    p.add_argument("--mode", choices=("auto", "docker", "local"), default="auto")
    p.add_argument("--gen-mode", choices=("docker", "local"), help="override --mode for the generation step")
    p.add_argument("--blender", default="", help="local Blender executable (local mode)")
    p.add_argument("--python", default=sys.executable, help="local Python with Pillow + numpy for the key step")
    p.add_argument("--trellis-python", default="", help="Python of a local TRELLIS.2 environment")
    p.add_argument("--trellis-repo", default="", help="the local TRELLIS.2 repo folder (PYTHONPATH)")
    p.add_argument("--triposr-python", default="", help="Python of a local TripoSR environment")
    p.add_argument("--triposr-repo", default="", help="the local TripoSR repo folder (PYTHONPATH)")
    p.add_argument("--cache", default=str(KIT / "cache"), help="model weights cache (Docker mode, downloaded once)")
    p.add_argument("--close", default="", help="close-up heights as fractions, e.g. 0.5,0.72,0.9 "
                                               "(default: character 0.5,0.72,0.9; object none)")
    p.add_argument("--skip-check", action="store_true")
    a = p.parse_args()

    a.triangles = a.triangles or TRIANGLES[a.kind]
    height = a.height if a.height > 0 else HEIGHT[a.kind]
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    d = docker_exe()
    d = d if d and docker_running(d) else None
    mode = a.mode if a.mode != "auto" else ("docker" if d else "local")
    if mode == "docker" and not d:
        raise SystemExit("ERROR: Docker is not running - start Docker Desktop, or use --mode local")
    a.gen_mode = a.gen_mode or mode
    a.blender_exe = find_blender(a.blender) if mode == "local" else None
    a.blender_mode = "docker" if mode == "docker" or not a.blender_exe else "local"
    if a.blender_mode == "docker" and not d:
        raise SystemExit("ERROR: no Blender found (--blender PATH, or the BLENDER environment variable), and Docker "
                         "is not running")
    close = [float(x) for x in a.close.split(",") if x.strip()] if a.close else (
        [0.5, 0.72, 0.9] if a.kind != "object" else [])
    settings = {k: getattr(a, k) for k in ("kind", "triangles", "texture", "ps1", "remesh", "backend", "vram",
                                           "seed", "hires")}
    settings.update(height_m=height, mode=mode, gen_mode=a.gen_mode, blender_mode=a.blender_mode,
                    image=str(Path(a.image).resolve()))
    if a.hires:  # the mesh was made earlier: --backend / --vram / --seed had no effect
        for k in ("backend", "vram", "seed"):
            settings[k] = "(not used: --hires)"
    report = {"command": "python scripts/pipeline.py " + " ".join(
        f'"{x}"' if " " in x else x for x in sys.argv[1:]), "settings": settings, "gpus": gpus()}
    t0 = time.monotonic()
    try:
        step_key(a, out, d if mode == "docker" else None)
        report["hires"] = step_hires(a, out, d)
        report["hires"]["md5"] = md5(out / "hires.glb")
        log("post", f"Blender ({a.blender_mode}): {a.triangles} triangles, {a.texture}px atlas, {height} m")
        post = blender(a, out, d, "blender_post.py",
                       {"op": "image3d", "glb": "hires.glb", "out": "model.glb", "triangles": a.triangles,
                        "texture": a.texture, "height": height, "remesh": a.remesh, "manifold": a.kind != "object",
                        "reference": "reference_keyed.png", "ps1": a.ps1})
        post.pop("ok", None)
        post.pop("out", None)
        report["post"] = post
        report["model_md5"] = md5(out / "model.glb")
        log("post", f"model.glb: {post['triangles']} triangles, carved={post['remeshed']}, front={post['front_view']}")
        if not a.skip_check:
            log("check", "measuring + rendering")
            shutil.rmtree(out / "check", ignore_errors=True)
            chk = blender(a, out, d, "blender_check.py",
                          {"glb": "model.glb", "reference": "reference_keyed.png", "out": "check", "close": close,
                           "scan_views": a.kind == "object"})
            chk.pop("ok", None)
            chk["images"] = [str(out / "check" / Path(x).name) for x in chk["images"]]
            chk["sheets"] = [str(out / "check" / Path(x).name) for x in chk["sheets"]]
            report["check"] = chk
            report["verdicts"] = verdicts(a, height, post, chk)
    except Fail as e:
        report["error"] = str(e)
        (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        raise SystemExit(f"ERROR: {e}")
    report["seconds"] = round(time.monotonic() - t0)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    v = report.get("verdicts", {})
    log("done", f"{out / 'model.glb'} - " + ", ".join(f"{k}: {x}" for k, x in v.items()))
    if report.get("check"):
        c = report["check"]
        best = f" (best view {c['best_view']['iou']})" if c.get("best_view") else ""
        log("done", f"{c['triangles']} tris, IoU {c.get('silhouette_iou')}{best}, {c['texel_density_px_per_m']} px/m, "
                    f"open edges {c['open_or_nonmanifold_edges']}, height {c['height_m']} m")


if __name__ == "__main__":
    main()
