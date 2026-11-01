"""Bounded GUI smoke test. Starts and stops only its own fresh Blender instance.

Run from the checkout: python3 tools/gui_smoke.py /tmp/mcpblender-gui-evidence
Requires authorization to open a GUI on the current display. Evidence is retained.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import Client
from mcpblender.config import Config
from mcpblender.server import build_server
from mcpblender.wire import call


async def run(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.environ["BLENDER_USER_CONFIG"] = str(root / "blender-config")
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    config = Config()
    config.launch.auto_recover = False
    config.bridge.socket_path = str(root / "bridge/bridge.sock")
    config.launch.log_path = str(root / "blender.log")
    config.stdio.library_root = str(root / "library")
    server, state = build_server(config)
    evidence = {"root": str(root)}
    try:
        async with Client(server, timeout=120) as client:
            evidence["startup"] = (await client.call_tool("start_blender", {"background": False})).data
            assert evidence["startup"]["started"]
            code = '''
obj = bpy.data.objects["Cube"]
obj.name = "MCP_GUI_TestPart"
mod = obj.modifiers.new("Beveled edges", "BEVEL")
mod.width = 0.15
mod.segments = 3
obj.data.materials.clear()
mat = bpy.data.materials.new("Blue")
mat.diffuse_color = (0.08, 0.35, 0.65, 1)
obj.data.materials.append(mat)
for window in bpy.context.window_manager.windows:
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.shading.color_type = "MATERIAL"
result = {"name": obj.name}
'''
            creation = (await client.call_tool("execute_code", {"code": code})).data
            assert creation["success"], creation
            evidence["object"] = (await client.call_tool("get_object_info", {"name": "MCP_GUI_TestPart"})).data
            await client.call_tool("save_blend", {"path": "gui-scene.blend"})
            await client.call_tool("open_blend", {"path": "gui-scene.blend"})
            await asyncio.sleep(1)
            image = (await client.call_tool("capture_viewport", {"width": 640, "height": 480})).data
            assert Path(image["path"]).read_bytes().startswith(b"\x89PNG")
            evidence["viewport"] = image
            viewed = await client.call_tool("get_image", {"artifact_id": image["artifact_id"]})
            assert any(block.type == "image" for block in viewed.content)
            evidence["image_delivered"] = True
            evidence["status_after_reopen"] = (await client.call_tool("bridge_status")).data
    finally:
        process = state.launcher.process
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        call(config.bridge.socket_path, "shutdown", timeout=5)
        evidence["owned_process_stopped"] = process is not None and process.poll() is not None
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]).resolve()))
