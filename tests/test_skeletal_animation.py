"""Live skeletal workflows through MCP, with Python execution disabled."""
import asyncio
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

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
