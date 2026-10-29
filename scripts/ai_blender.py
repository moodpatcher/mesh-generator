"""Runs INSIDE a Blender window (blender --factory-startup --python ai_blender.py -- model.glb addon.py port):
imports the finished model, loads the add-on of mcp-for-blender (MIT) straight from its file - nothing is installed into
your Blender - switches off every online service but Poly Haven (free, CC0), and starts the add-on's socket server on
localhost:<port>, so an AI agent can work on the model through the MCP server (which runs in Docker).
Telemetry is off on both sides (no add-on preference is ever set, the server has DISABLE_TELEMETRY=true)."""

import importlib.util
import sys

import bpy

# Everything but Poly Haven needs an API key or credits, or has a licence that is not free for commercial use
# (Sketchfab: per model; Poly Pizza: per model; the generators: paid; Hunyuan3D is never used, see CLAUDE.md).
OFF = ("hyper3d", "hunyuan3d", "sketchfab", "tripo", "meshy", "polypizza")


def main():
    glb, addon, port = sys.argv[sys.argv.index("--") + 1:][:3]
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.import_scene.gltf(filepath=glb)
    try:  # the importer can leave the armature in edit mode
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError:
        pass

    spec = importlib.util.spec_from_file_location("blender_mcp_addon", addon)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.register()

    sc = bpy.context.scene
    sc.blendermcp_port = int(port)
    sc.blendermcp_auto_start_server = False
    for name in OFF:
        if hasattr(sc, f"blendermcp_use_{name}"):
            setattr(sc, f"blendermcp_use_{name}", False)
    sc.blendermcp_use_polyhaven = True

    def start():
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                if a.type == "VIEW_3D":
                    a.spaces[0].shading.type = "MATERIAL"  # the texture shows
                    with bpy.context.temp_override(window=w, area=a, region=a.regions[-1]):
                        bpy.ops.view3d.view_all()
        bpy.ops.blendermcp.start_server()
        print(f"[kep3d] MCP add-on listening on localhost:{port}", flush=True)
        return None

    bpy.app.timers.register(start, first_interval=1.5)


main()
