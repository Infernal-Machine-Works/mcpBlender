"""Live skeletal workflows through MCP, with Python execution disabled."""
import asyncio
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.config import Config, write_config
from mcpblender.launcher import Launcher
from mcpblender.wire import call

pytestmark = [pytest.mark.blender, pytest.mark.skipif(
    os.environ.get("MCPBLENDER_TEST_BLENDER") != "1" or not shutil.which("blender"),
    reason="Set MCPBLENDER_TEST_BLENDER=1 to use an installed Blender")]


@pytest.fixture(scope="module")
def animation_live():
    with tempfile.TemporaryDirectory(prefix="mb-rig-") as directory:
        root = Path(directory)
        config = Config()
        config.bridge.socket_path = str(root / "bridge/bridge.sock")
        config.bridge.timeout_seconds = 90
        config.launch.background = True
        config.launch.auto_recover = False
        config.launch.log_path = str(root / "blender.log")
        config.stdio.library_root = str(root / "library")
        config.stdio.allow_code_execution = False
        path = root / "config.json"
        write_config(path, config)
        launcher = Launcher(config)
        try:
            launcher.start()
            yield root, path
        finally:
            if launcher.process and launcher.process.poll() is None:
                launcher.process.terminate()
                launcher.process.wait(timeout=15)
            call(config.bridge.socket_path, "shutdown", timeout=5)


def client_for(path):
    return Client(StdioTransport(command=sys.executable,
        args=["-m", "mcpblender.server", "--config", str(path)]), timeout=100)


async def tool(client, command, **params):
    return (await client.call_tool(command, {"params": params})).data


async def make_limb(client, rig, mesh, x=20):
    await tool(client, "create_armature", name=rig, location=[x, 0, 0], bones=[
        {"name": "Base", "head": [0, 0, 0], "tail": [0, 0, 1]},
        {"name": "Tip", "head": [0, 0, 1], "tail": [0, 0, 2], "parent": "Base", "connected": True}])
    vertices = [[x + dx, dy, z / 2] for z in range(5) for dx, dy in [(-.2, -.2), (.2, -.2), (.2, .2), (-.2, .2)]]
    faces = [[0, 3, 2, 1], [16, 17, 18, 19]]
    faces += [[4 * z + i, 4 * z + (i + 1) % 4, 4 * (z + 1) + (i + 1) % 4, 4 * (z + 1) + i] for z in range(4) for i in range(4)]
    await tool(client, "create_mesh", name=mesh, vertices=vertices, faces=faces)
    weights = [{"vertex": i, "weights": {"Base": 1} if v[2] < 1 else ({"Tip": 1} if v[2] > 1 else {"Base": .5, "Tip": .5})} for i, v in enumerate(vertices)]
    return await tool(client, "bind_mesh", name=mesh, armature=rig, method="EXPLICIT", weights=weights)


def test_skeleton_creation_and_skin_binding(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            assert "execute_code" not in {t.name for t in await client.list_tools()}
            selected = (await client.call_tool("get_scene_info")).data["active_object"]
            binding = await make_limb(client, "BindingRig", "BindingLimb")
            assert binding["unweighted_vertices"] == 0
            assert binding["unnormalized_vertices"] == 0
            assert binding["max_influences"] == 2
            assert (await client.call_tool("get_scene_info")).data["active_object"] == selected
            rig = await tool(client, "inspect_rig", name="BindingRig", bone_limit=1)
            assert rig["total_bones"] == 2 and rig["next_bone_offset"] == 1
            second = await tool(client, "inspect_rig", name="BindingRig", bone_offset=1)
            assert second["bones"][0]["parent"] == "Base" and second["bones"][0]["connected"]
            assert second["bones"][0]["pose_tail_world"] == pytest.approx([20, 0, 2])
            with pytest.raises(ToolError, match="already has"):
                await tool(client, "bind_mesh", name="BindingLimb", armature="BindingRig", method="RIGID", bone="Tip")
            await tool(client, "create_primitive", name="RigidPart", location=[20, 0, 2], scale=[.2, .2, .2])
            rigid = await tool(client, "bind_mesh", name="RigidPart", armature="BindingRig", method="RIGID", bone="Tip")
            assert rigid["unweighted_vertices"] == 0 and rigid["max_influences"] == 1
            assert all(s["weights"] == {"Tip": 1} for s in rigid["vertex_samples"])
            await tool(client, "create_armature", name="AutoRig", location=[25, 0, 0], bones=[{"name": "Root", "head": [0, 0, 0], "tail": [0, 0, 2]}])
            await tool(client, "create_primitive", name="AutoPart", location=[25, 0, 1], scale=[.4, .4, 1])
            auto = await tool(client, "bind_mesh", name="AutoPart", armature="AutoRig")
            assert auto["unweighted_vertices"] == 0 and auto["armature_modifiers"]
            await tool(client, "create_primitive", name="InvalidPart", location=[25, 0, 1])
            with pytest.raises(ToolError, match="unknown vertex"):
                await tool(client, "bind_mesh", name="InvalidPart", armature="AutoRig", method="EXPLICIT", weights=[{"vertex": 999, "weights": {"Root": 1}}])
            info = (await client.call_tool("get_object_info", {"name": "InvalidPart"})).data
            assert info["parent"] is None and not info["modifiers"]

    asyncio.run(run())


def test_named_clips_keyframes_and_independent_deformation(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            await make_limb(client, "ClipRig", "ClipLimb", x=30)
            base = await tool(client, "inspect_mesh", name="ClipLimb")
            clip = await tool(client, "create_animation_clip", name="ClipRig", clip="Bend", tracks=[{
                "bone": "Tip", "property": "rotation_degrees", "keyframes": [
                    {"frame": 1, "value": [0, 0, 0]}, {"frame": 5, "value": [45, 0, 0]}, {"frame": 9, "value": [90, 0, 0]}]}])
            assert clip["detail"]["total_tracks"] == 3 and clip["detail"]["frame_range"] == [1, 9]
            await tool(client, "create_animation_clip", name="ClipRig", clip="Shift", activate=False, tracks=[{
                "bone": "Base", "property": "location", "keyframes": [{"frame": 1, "value": [.4, 0, 0]}, {"frame": 9, "value": [.4, 0, 0]}]}])
            await client.call_tool("set_frame", {"frame": 5})
            posed_before = await tool(client, "inspect_mesh", name="ClipLimb")
            sampled = await tool(client, "sample_animation", name="ClipRig", clip="Bend", frames=[1, 3, 5, 9], meshes=["ClipLimb"], sample_limit=20)
            assert sampled["restored_frame"] == 5 and sampled["restored_clip"] == "Bend"
            assert (await tool(client, "inspect_mesh", name="ClipLimb"))["geometry_sha256"] == posed_before["geometry_sha256"]
            samples = sampled["samples"]
            assert len({s["meshes"][0]["geometry_sha256"] for s in samples}) == 4
            assert samples[0]["meshes"][0]["geometry_sha256"] == base["geometry_sha256"]
            assert samples[0]["meshes"][0]["vertex_samples"][16]["world"] != samples[-1]["meshes"][0]["vertex_samples"][16]["world"]
            tip = next(b for b in samples[2]["bones"] if b["name"] == "Tip")
            assert tip["head_world"] == pytest.approx([30, 0, 1], abs=1e-5)
            assert tip["tail_world"][0] == pytest.approx(30, abs=1e-5)
            assert abs(tip["tail_world"][1]) == pytest.approx(math.sin(math.pi / 4), abs=1e-5)
            assert tip["tail_world"][2] == pytest.approx(1 + math.cos(math.pi / 4), abs=1e-5)
            top = [v["world"] for v in samples[2]["meshes"][0]["vertex_samples"][16:20]]
            assert [sum(v[i] for v in top) / 4 for i in range(3)] == pytest.approx(tip["tail_world"], abs=1e-5)
            assert samples[0]["meshes"][0]["vertex_samples"][:4] == samples[-1]["meshes"][0]["vertex_samples"][:4]
            await tool(client, "set_active_clip", name="ClipRig", clip="Shift")
            shifted = await tool(client, "inspect_rig", name="ClipRig", meshes=[])
            assert shifted["bones"][0]["pose_head_world"][0] == pytest.approx(30.4, abs=1e-5)
            await tool(client, "set_active_clip", name="ClipRig", clip="Bend")
            unshifted = await tool(client, "inspect_rig", name="ClipRig", meshes=[])
            assert unshifted["bones"][0]["pose_head_world"][0] == pytest.approx(30, abs=1e-5)
            edited = await tool(client, "set_pose_keyframes", name="ClipRig", clip="Bend", tracks=[{
                "bone": "Tip", "property": "rotation_degrees", "keyframes": [{"frame": 5, "value": [30, 0, 0]}]}])
            assert all(t["keys"] == 3 for t in edited["detail"]["tracks"])
            sample = await tool(client, "sample_animation", name="ClipRig", frames=[5], bones=["Tip"])
            assert sample["samples"][0]["bones"][0]["tail_world"][2] == pytest.approx(1 + math.cos(math.pi / 6), abs=1e-5)
            with pytest.raises(ToolError, match="already exists"):
                await tool(client, "create_animation_clip", name="ClipRig", clip="Bend", tracks=[{
                    "bone": "Tip", "keyframes": [{"frame": 1, "value": [0, 0, 0]}]}])
            await tool(client, "create_armature", name="OtherClipRig", bones=[{"name": "Root", "head": [0, 0, 0], "tail": [0, 0, 1]}])
            with pytest.raises(ToolError, match="different armature"):
                await tool(client, "set_active_clip", name="OtherClipRig", clip="Bend")
            with pytest.raises(ToolError, match="Bone not found"):
                await tool(client, "create_animation_clip", name="ClipRig", clip="InvalidClip", tracks=[{
                    "bone": "Missing", "keyframes": [{"frame": 1, "value": [0, 0, 0]}]}])
            assert "InvalidClip" not in {a["name"] for a in (await tool(client, "inspect_animation", name="ClipRig"))["clips"]}
            await tool(client, "set_active_clip", name="ClipRig")
            assert (await tool(client, "inspect_mesh", name="ClipLimb"))["geometry_sha256"] == base["geometry_sha256"]
            await tool(client, "set_active_clip", name="ClipRig", clip="Bend")

    asyncio.run(run())


def test_textured_animation_render_and_glb_round_trip(animation_live):
    root, path = animation_live
    library = root / "library"
    library.mkdir(exist_ok=True)
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    pixels = b"".join(b"\x00" + b"".join(bytes((240, 155, 40) if (x // 4 + y // 4) % 2 else (35, 110, 170)) for x in range(16)) for y in range(16))
    (library / "rig-checker.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 16, 16, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))

    async def run():
        async with client_for(path) as client:
            await make_limb(client, "DeliveryRig", "DeliveryLimb", x=40)
            await tool(client, "generate_uv", name="DeliveryLimb")
            await tool(client, "configure_material", name="RigTexture", image_path="rig-checker.png", roughness=.55)
            await tool(client, "assign_material", objects=["DeliveryLimb"], material="RigTexture")
            for clip, bone, end in (("BendPreview", "Tip", [60, 0, 0]), ("ReachPreview", "Base", [0, 0, 25])):
                await tool(client, "create_animation_clip", name="DeliveryRig", clip=clip, activate=False, tracks=[{
                    "bone": bone, "property": "rotation_degrees", "keyframes": [
                        {"frame": 1, "value": [0, 0, 0]}, {"frame": 5, "value": end}, {"frame": 9, "value": [0, 0, 0]}]}])
            await tool(client, "set_active_clip", name="DeliveryRig", clip="BendPreview")
            await client.call_tool("set_frame", {"frame": 1})
            source = await tool(client, "sample_animation", name="DeliveryRig", clip="BendPreview", frames=[1, 5, 9], meshes=["DeliveryLimb"], sample_limit=20)
            source_reach = await tool(client, "sample_animation", name="DeliveryRig", clip="ReachPreview", frames=[1, 5, 9], meshes=["DeliveryLimb"], sample_limit=20)
            await tool(client, "configure_render", resolution_x=192, resolution_y=192, fps=12, samples=4, frame_start=1, frame_end=9)
            await tool(client, "configure_camera", name="RigCamera")
            fit = await tool(client, "frame_objects", name="RigCamera", objects=["DeliveryLimb"], direction=[1, -3, .7], margin=1.5)
            assert fit["fully_in_frame"]
            await tool(client, "configure_light", name="RigKey", type="AREA", location=[43, -4, 6], target=[40, 0, 1], energy=700, size=5)
            queued = await tool(client, "render_animation", directory="rig-preview", frame_start=1, frame_end=9, frame_step=2, width=192, height=192, engine="CYCLES", camera="RigCamera")
            rendered = (await client.call_tool("wait_operation", {"operation_id": queued["operation_id"], "timeout_seconds": 90})).data
            assert rendered["state"] == "succeeded", rendered
            assert rendered["storage"]["available_after_reserve_bytes"] > 0
            frame_paths = sorted((library / "rig-preview").glob("*.png"))
            assert len(frame_paths) == 5 and frame_paths[0].read_bytes() != frame_paths[2].read_bytes()
            await tool(client, "vse_add_strip", name="RigPreviewSequence", kind="IMAGE", paths=[str(p.relative_to(library)) for p in frame_paths], duration=5)
            video = await tool(client, "render_video", path="rig-preview.mp4", frame_start=1, frame_end=5, width=192, height=192)
            encoded = (await client.call_tool("wait_operation", {"operation_id": video["operation_id"], "timeout_seconds": 90})).data
            assert encoded["state"] == "succeeded", encoded
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(library / "rig-preview.mp4")]))
            stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
            assert stream["codec_name"] == "h264" and int(stream["nb_read_frames"]) == 5
            assert (stream["width"], stream["height"]) == (192, 192)
            await client.call_tool("save_blend", {"path": "skeletal-animation.blend"})
            await client.call_tool("open_blend", {"path": "skeletal-animation.blend"})
            reopened = await tool(client, "sample_animation", name="DeliveryRig", clip="BendPreview", frames=[1, 5, 9], meshes=["DeliveryLimb"], sample_limit=20)
            assert [s["meshes"][0]["geometry_sha256"] for s in reopened["samples"]] == [s["meshes"][0]["geometry_sha256"] for s in source["samples"]]
            await client.call_tool("select_objects", {"names": ["DeliveryRig", "DeliveryLimb"]})
            before_export = await tool(client, "inspect_mesh", name="DeliveryLimb")
            await client.call_tool("export_glb", {"path": "skeletal-animation.glb"})
            assert (await tool(client, "inspect_mesh", name="DeliveryLimb"))["geometry_sha256"] == before_export["geometry_sha256"]
            # Temporary export tracks must be gone: ordinary clip tools reject NLA.
            await tool(client, "set_active_clip", name="DeliveryRig", clip="BendPreview")
            data = (library / "skeletal-animation.glb").read_bytes()
            size = struct.unpack_from("<I", data, 12)[0]
            gltf = json.loads(data[20:20 + size])
            binary = data[28 + size:]
            assert {a["name"] for a in gltf["animations"]} == {"BendPreview", "ReachPreview"}
            assert len(gltf["skins"]) == 1 and len(gltf["skins"][0]["joints"]) == 2
            assert gltf["images"][0]["bufferView"] >= 0
            attributes = gltf["meshes"][0]["primitives"][0]["attributes"]
            assert {"TEXCOORD_0", "JOINTS_0", "WEIGHTS_0"} <= attributes.keys()
            accessor = gltf["accessors"][attributes["WEIGHTS_0"]]
            view = gltf["bufferViews"][accessor["bufferView"]]
            assert accessor["componentType"] == 5126 and accessor["type"] == "VEC4"
            for i in range(accessor["count"]):
                offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0) + i * view.get("byteStride", 16)
                assert sum(struct.unpack_from("<4f", binary, offset)) == pytest.approx(1, abs=1e-5)
            # Re-import and inspect actual playback, not just animation headers.
            before_actions = {a["name"] for a in (await tool(client, "inspect_animation", name="DeliveryRig"))["clips"]}
            imported = (await client.call_tool("import_asset", {"path": "skeletal-animation.glb"})).data
            objects = (await client.call_tool("get_scene_info")).data["objects"]
            imported_rig = next(o["name"] for o in objects if o["name"] in imported["objects"] and o["type"] == "ARMATURE")
            imported_mesh = next(o["name"] for o in objects if o["name"] in imported["objects"] and o["type"] == "MESH")
            # Native glTF import stashes inactive clips in muted NLA tracks;
            # structured clip selection supports these without NLA blending.
            imported_info = await tool(client, "inspect_rig", name=imported_rig)
            assert imported_info["meshes"][0]["unweighted_vertices"] == 0
            actions = (await tool(client, "inspect_animation", name=imported_rig))["clips"]
            new_actions = [a["name"] for a in actions if a["name"] not in before_actions]
            assert len(new_actions) == 2
            imported_samples = {}
            for clip, expected in (("BendPreview", source), ("ReachPreview", source_reach)):
                imported_clip = next(name for name in new_actions if clip in name)
                await tool(client, "set_active_clip", name=imported_rig, clip=imported_clip)
                sampled = await tool(client, "sample_animation", name=imported_rig, frames=[1, 5, 9], meshes=[imported_mesh])
                for actual, original in zip(sampled["samples"], expected["samples"]):
                    for key in ("min", "max"):
                        assert actual["meshes"][0]["world_bounds"][key] == pytest.approx(original["meshes"][0]["world_bounds"][key], abs=1e-4)
                assert sampled["samples"][0]["meshes"][0]["geometry_sha256"] != sampled["samples"][1]["meshes"][0]["geometry_sha256"]
                imported_samples[clip] = sampled
            evidence = {"source_samples": source, "source_reach_samples": source_reach, "imported_rig": imported_info,
                        "imported_samples": imported_samples, "exported_clips": [a["name"] for a in gltf["animations"]],
                        "imported_clips": new_actions, "camera_fit": fit, "ffprobe": probe}
            (library / "skeletal-evidence.json").write_text(json.dumps(evidence, indent=2))
            destination = os.environ.get("MCPBLENDER_SKELETAL_EVIDENCE_DIR")
            if destination:
                shutil.copytree(library, destination, dirs_exist_ok=True)

    asyncio.run(run())


def test_cancelled_sampling_restores_frame_and_pose(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            await make_limb(client, "CancelRig", "CancelLimb", x=60)
            await tool(client, "configure_modifier", name="CancelLimb", modifier="DetailedSurface", settings={"type": "SUBSURF", "levels": 4})
            await tool(client, "create_animation_clip", name="CancelRig", clip="CancelBend", tracks=[{
                "bone": "Tip", "keyframes": [{"frame": 1, "value": [0, 0, 0]}, {"frame": 64, "value": [90, 0, 0]}]}])
            await client.call_tool("set_frame", {"frame": 5})
            before = await tool(client, "inspect_mesh", name="CancelLimb")
            queued = (await client.call_tool("sample_animation", {"params": {"name": "CancelRig", "frames": list(range(1, 65)), "meshes": ["CancelLimb"], "sample_limit": 0}, "wait": False})).data
            for _ in range(500):
                operation = (await client.call_tool("get_operation", {"operation_id": queued["operation_id"]})).data
                if operation.get("progress", {}).get("phase") == "sampling animation":
                    break
                assert operation["state"] not in {"succeeded", "failed", "cancelled"}, operation
                await asyncio.sleep(.01)
            else:
                pytest.fail("Sampler did not report its first evaluated frame")
            await client.call_tool("cancel_operation", {"operation_id": queued["operation_id"]})
            cancelled = (await client.call_tool("wait_operation", {"operation_id": queued["operation_id"], "timeout_seconds": 30})).data
            assert cancelled["state"] == "cancelled", cancelled
            scene = (await client.call_tool("get_scene_info")).data
            assert scene["frame"] == 5
            assert (await tool(client, "inspect_rig", name="CancelRig", meshes=[]))["active_clip"] == "CancelBend"
            assert (await tool(client, "inspect_mesh", name="CancelLimb"))["geometry_sha256"] == before["geometry_sha256"]

    asyncio.run(run())
