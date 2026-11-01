"""Real editing fixture: textured building, external asset round trip, animation and VSE.

Uses a disposable Blender instance and an authenticated loopback API fixture, not
a real diffusion provider. Retains editable projects, renders and video evidence.
Run: python3 tools/building_workflow.py /tmp/choose-a-new-building-directory
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import json
import math
import os
import struct
import subprocess
import sys
import threading
import wave
import zlib
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcpblender.config import AssetSource, Config, write_config
from mcpblender.wire import call


def brick_texture(path):
    width = height = 128
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            stagger = 16 if (y // 16) % 2 else 0
            mortar = y % 16 < 2 or (x + stagger) % 32 < 2
            variation = (x * 17 + y * 13) % 19
            row.extend((172, 161, 141) if mortar else (133 + variation, 65 + variation // 2, 44 + variation // 3))
        rows.append(row)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


@contextmanager
def asset_api(directory):
    secret = "fixture-asset-credential-keep-out-of-journal"
    received, requests = {}, []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authorize(self):
            if self.headers.get("Authorization") != "Bearer " + secret:
                self.send_error(401)
                return False
            requests.append((self.command, self.path))
            return True

        def respond(self, body, content_type="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authorize():
                return
            if self.path == "/status/generation-1":
                self.respond(json.dumps({"state": "succeeded", "asset": "assets/awning.glb"}).encode())
            elif self.path in ("/assets/awning.glb", "/assets/brick.png"):
                path = directory / Path(self.path).name
                self.respond(path.read_bytes(), "application/octet-stream")
            else:
                self.send_error(404)

        def do_POST(self):
            if not self.authorize():
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received["generation_request"] = json.loads(body)
            self.respond(b'{"job_id":"generation-1"}')

        def do_PUT(self):
            if not self.authorize():
                return
            received["refined.glb"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.respond(json.dumps({"bytes": len(received["refined.glb"]),
                                     "sha256": hashlib.sha256(received["refined.glb"]).hexdigest()}).encode())

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", secret, received, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def finish(client, operation, timeout=300):
    if "operation_id" not in operation or "state" not in operation:
        return operation
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        operation = (await client.call_tool("wait_operation", {"operation_id": operation["operation_id"], "timeout_seconds": 1})).data
        if operation["state"] in ("succeeded", "failed", "cancelled", "interrupted"):
            assert operation["state"] == "succeeded", operation
            return operation["result"]
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(operation)


async def domain(client, command, **params):
    return await finish(client, (await client.call_tool(command, {"params": params})).data)


async def editing_workflow(client, root, frames=12):
    async def python(code):
        value = (await client.call_tool("execute_code", {"code": code})).data
        assert value["success"], value
        return value.get("result")

    await python('''
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.context.scene.render.threads_mode = "FIXED"
bpy.context.scene.render.threads = 2
bpy.context.scene.cycles.use_denoising = False
bpy.context.scene.world.use_nodes = True
bpy.context.scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.24, 0.32, 0.46, 1)
bpy.context.scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.4
''')
    capabilities = await domain(client, "get_capabilities")
    assert capabilities["ffmpeg"] and "video_sequencer" in capabilities["domains"]
    await domain(client, "create_mesh", name="API_Awning",
                 vertices=[[-.8, 0, 0], [.8, 0, 0], [-.8, -.7, -.2], [.8, -.7, -.2]], faces=[[0, 1, 3, 2]])
    await client.call_tool("select_objects", {"names": ["API_Awning"]})
    await client.call_tool("export_glb", {"path": "provider/awning.glb"})
    await python('bpy.data.objects.remove(bpy.data.objects["API_Awning"], do_unlink=True)')
    generated = await domain(client, "request_asset_api", source="fixture", resource="generate", method="POST",
                             payload={"prompt": "small entrance awning", "format": "glb"})
    assert generated["response"]["job_id"] == "generation-1"
    status = await domain(client, "request_asset_api", source="fixture", resource="status/generation-1")
    await domain(client, "download_asset", source="fixture", resource=status["response"]["asset"], path="components/awning.glb")
    texture = await domain(client, "download_asset", source="fixture", resource="assets/brick.png", path="textures/brick.png")
    assert texture["sha256"] == hashlib.sha256((root / "library/provider/brick.png").read_bytes()).hexdigest()
    imported = (await client.call_tool("import_asset", {"path": "components/awning.glb"})).data
    assert "API_Awning" in imported["objects"]
    await client.call_tool("set_object_transform", {"name": "API_Awning", "location": [0, -2.06, 2.5]})

    for name, color, roughness, metallic in [
        ("Brick", [0.55, .26, .13, 1], .8, 0), ("Roof", [.08, .12, .16, 1], .7, 0),
        ("Trim", [.86, .77, .58, 1], .5, 0), ("Glass", [.12, .38, .47, 1], .16, .55),
        ("DoorWood", [.14, .3, .28, 1], .45, 0), ("Ground", [.23, .3, .18, 1], .95, 0),
        ("Metal", [.6, .4, .12, 1], .25, .8),
    ]:
        options = {"image_path": "textures/brick.png", "texture_scale": [6, 6, 1]} if name == "Brick" else {}
        await domain(client, "configure_material", name=name, base_color=color, roughness=roughness, metallic=metallic, **options)

    async def cube(name, location, scale, material):
        await domain(client, "create_primitive", name=name, location=location, scale=scale)
        await domain(client, "assign_material", objects=[name], material=material)

    await cube("Foundation", [0, 0, .1], [2.75, 2.25, .1], "Trim")
    await cube("Walls", [0, 0, 1.55], [2.5, 2, 1.35], "Brick")
    await domain(client, "create_mesh", name="GabledRoof", vertices=[[-2.8, -2.3, 2.9], [2.8, -2.3, 2.9], [0, -2.3, 4.2],
                                                                       [-2.8, 2.3, 2.9], [2.8, 2.3, 2.9], [0, 2.3, 4.2]],
                 faces=[[0, 2, 1], [3, 4, 5], [0, 3, 5, 2], [2, 5, 4, 1], [0, 1, 4, 3]])
    await domain(client, "assign_material", objects=["GabledRoof", "API_Awning"], material="Roof")
    await cube("EntranceDoor", [0, -2.025, 1.12], [.48, .04, .92], "DoorWood")
    await cube("DoorHeader", [0, -2.08, 2.1], [.57, .06, .06], "Trim")
    await cube("DoorLeftTrim", [-.54, -2.08, 1.15], [.06, .06, .98], "Trim")
    await cube("DoorRightTrim", [.54, -2.08, 1.15], [.06, .06, .98], "Trim")
    await cube("DoorHandle", [.3, -2.095, 1.15], [.025, .035, .09], "Metal")
    for x, side in [(-1.55, "Left"), (1.55, "Right")]:
        await cube(side + "Window", [x, -2.035, 1.7], [.55, .035, .55], "Glass")
        for dx, dz, sx, sz, suffix in [(0, -.62, .65, .05, "Sill"), (0, .62, .65, .05, "Top"),
                                        (-.62, 0, .05, .62, "Left"), (.62, 0, .05, .62, "Right"),
                                        (0, 0, .025, .55, "Mullion"), (0, 0, .55, .025, "Crossbar")]:
            await cube(side + "Window" + suffix, [x + dx, -2.09, 1.7 + dz], [sx, .04, sz], "Trim")
    await cube("EntryStep", [0, -2.5, .1], [.9, .5, .1], "Trim")
    await cube("Walkway", [0, -4.2, .025], [.9, 1.3, .025], "Trim")
    await cube("Ground", [0, 0, -.05], [200, 200, .05], "Ground")
    await domain(client, "configure_light", name="Sun", type="SUN", energy=2.1, location=[-5, -8, 10], target=[0, 0, 0], color=[1, .87, .72])
    await domain(client, "configure_light", name="Fill", type="AREA", energy=400, location=[5, -3, 6], target=[0, 0, 1.5], size=6)
    await domain(client, "configure_camera", name="WalkthroughCamera", location=[9, -12, 7], target=[0, -.3, 1.6], lens=44)
    await domain(client, "set_keyframes", name="WalkthroughCamera", data_path="location",
                 keyframes=[{"frame": 1, "value": [9, -12, 7]}, {"frame": frames, "value": [7.4, -11, 6.4]}])
    await domain(client, "configure_render", engine="CYCLES", samples=12, resolution_x=384, resolution_y=256,
                 frame_start=1, frame_end=frames, fps=6)
    await python('''
for name in ("Walls", "Foundation", "EntranceDoor", "EntryStep"):
    bevel = bpy.data.objects[name].modifiers.new("Soft edges", "BEVEL")
    bevel.width = .035
    bevel.segments = 2
result = {"texture_nodes": [n.bl_idname for n in bpy.data.materials["Brick"].node_tree.nodes]}
''')
    resources = await domain(client, "inspect_resources")
    assert any(i["packed"] and i["size"] == [128, 128] for i in resources["images"])
    assert any(action["range"] == [1, frames] for action in resources["actions"])
    await client.call_tool("save_blend", {"path": "building.blend"})
    await client.call_tool("open_blend", {"path": "building.blend"})
    await client.call_tool("select_objects", {"names": ["API_Awning"]})
    await client.call_tool("export_glb", {"path": "refined-awning.glb"})
    uploaded = await domain(client, "upload_asset", source="fixture", resource="refined.glb", path="refined-awning.glb")
    assert uploaded["response"]["bytes"] > 100
    await python('''
names = [obj.name for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.name != "Ground"]
for obj in bpy.context.scene.objects:
    obj.select_set(obj.name in names)
bpy.context.view_layer.objects.active = bpy.data.objects["Walls"]
result = names
''')
    exported = (await client.call_tool("export_glb", {"path": "building.glb"})).data
    animation = await domain(client, "render_animation", directory="walkthrough-frames", frame_start=1, frame_end=frames,
                             width=384, height=256, engine="CYCLES")
    assert animation["frame_count"] == frames
    assert len({hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in animation["frames"]}) > 1
    await domain(client, "vse_add_strip", name="BuildingShot", kind="IMAGE", paths=animation["frames"], channel=1, frame_start=1)
    await domain(client, "vse_add_strip", name="Title", kind="TEXT", text="MCP Blender | Building study", duration=frames,
                 font_size=16, channel=2, location=[.5, .1])
    sound = root / "library/ambience.wav"
    with wave.open(str(sound), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", int(180 * math.sin(2 * math.pi * 220 * n / 48000))) for n in range(48000 * frames // 6)))
    await domain(client, "vse_add_strip", name="Ambience", kind="SOUND", paths=[str(sound)], channel=3)
    await domain(client, "vse_edit_strip", name="Title", text="MCP Blender | Textured building")
    timeline = await domain(client, "vse_list")
    assert {strip["type"] for strip in timeline["strips"]} == {"IMAGE", "TEXT", "SOUND"}
    await client.call_tool("save_blend", {"path": "building-edited.blend"})
    video = await domain(client, "render_video", path="walkthrough.mp4", frame_start=1, frame_end=frames, width=384, height=256)
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", video["path"]]))
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"], int(stream["nb_read_frames"])) == (384, 256, frames)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    viewed = await client.call_tool("get_image", {"artifact_id": animation["preview_artifacts"][1]["artifact_id"]})
    assert any(block.type == "image" for block in viewed.content)
    return {"resources": resources, "timeline": timeline, "animation": animation, "video": video,
            "building_export": exported, "ffprobe": probe}


async def run(root, frames=12):
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    provider = root / "library/provider"
    provider.mkdir(parents=True)
    brick_texture(provider / "brick.png")
    config = Config()
    config.bridge.socket_path = str(root / "bridge/bridge.sock")
    config.launch.log_path = str(root / "blender.log")
    config.launch.background = True
    config.launch.auto_recover = False
    config.stdio.library_root = str(root / "library")
    config.bridge.timeout_seconds = 30
    path = root / "config.json"
    evidence = {}
    with asset_api(provider) as (url, credential, received, requests):
        config.asset_sources["fixture"] = AssetSource(base_url=url, headers={"Authorization": "Bearer " + credential})
        write_config(path, config)
        transport = StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)])
        pid = None
        try:
            async with Client(transport, timeout=120) as client:
                startup = (await client.call_tool("start_blender", {"background": True})).data
                pid = startup["status"]["pid"]
                evidence = await editing_workflow(client, root, frames)
            assert received["refined.glb"][:4] == b"glTF"
            assert received["generation_request"]["format"] == "glb"
            evidence["api_requests"] = requests
            for database in (root / "bridge").glob("*.sqlite3*"):
                assert credential.encode() not in database.read_bytes(), database
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
            print(json.dumps({"root": str(root), "frames": frames, "video": evidence["video"]["path"],
                              "preview": evidence["animation"]["preview_artifacts"][1]["path"]}, indent=2))
        finally:
            if pid:
                os.kill(pid, 15)
                for _ in range(300):
                    if not call(config.bridge.socket_path, "status").get("connected"):
                        break
                    await asyncio.sleep(.1)
            call(config.bridge.socket_path, "shutdown")
    return evidence


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]).resolve()))
