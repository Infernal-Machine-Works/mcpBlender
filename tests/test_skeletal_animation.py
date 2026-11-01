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
