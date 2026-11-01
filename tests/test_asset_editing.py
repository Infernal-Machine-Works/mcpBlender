"""Real structured asset editing through MCP with arbitrary Python disabled."""
import asyncio
import os
from pathlib import Path
import shutil
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
def assets_live():
    with tempfile.TemporaryDirectory(prefix="mb-asset-") as directory:
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


def test_evaluated_mesh_and_modifier_lifecycle(assets_live):
    _, path = assets_live

    async def run():
        async with client_for(path) as client:
            assert "execute_code" not in {t.name for t in await client.list_tools()}
            with pytest.raises(ToolError):
                await client.call_tool("execute_code", {"code": "raise Exception('unreachable')"})
            await tool(client, "create_primitive", name="BeveledAsset", location=[3, 0, 1])
            base = await tool(client, "inspect_mesh", name="BeveledAsset", evaluated=False)
            assert (base["vertices"], base["polygons"], base["triangles"]) == (8, 6, 12)
            assert base["world_bounds"] == {"min": [2, -1, 0], "max": [4, 1, 2]}
            assert base["topology"]["non_manifold_edges"] == 0
            assert base["uv_layers"][0]["zero_area_faces"] == 0
            selected = (await client.call_tool("get_object_info", {"name": "Cube"})).data["selected"]
            await tool(client, "configure_modifier", name="BeveledAsset", modifier="SoftEdges",
                       settings={"type": "BEVEL", "width": .2, "segments": 3})
            evaluated = await tool(client, "inspect_mesh", name="BeveledAsset", sample_limit=3)
            assert evaluated["vertices"] > base["vertices"]
            assert len(evaluated["vertex_samples"]) == 3
            assert evaluated["geometry_sha256"] != base["geometry_sha256"]
            unchanged = await tool(client, "inspect_mesh", name="BeveledAsset", evaluated=False)
            assert unchanged["geometry_sha256"] == base["geometry_sha256"]
            with pytest.raises(ToolError, match="type must match"):
                await tool(client, "configure_modifier", name="BeveledAsset", modifier="SoftEdges",
                           settings={"type": "SUBSURF"})
            applied = await tool(client, "apply_modifier", name="BeveledAsset", modifier="SoftEdges")
            assert applied["vertices"] == evaluated["vertices"] and not applied["modifiers"]
            assert (await client.call_tool("get_object_info", {"name": "Cube"})).data["selected"] == selected
            for kind in ("SUBSURF", "DECIMATE", "SOLIDIFY", "TRIANGULATE"):
                await tool(client, "configure_modifier", name="BeveledAsset", modifier="Temporary",
                           settings={"type": kind})
                assert (await tool(client, "inspect_mesh", name="BeveledAsset"))["vertices"] > 0
                await tool(client, "remove_modifier", name="BeveledAsset", modifier="Temporary")
            with pytest.raises(ToolError, match="not found"):
                await tool(client, "apply_modifier", name="BeveledAsset", modifier="Temporary")
            await tool(client, "create_mesh", name="OpenTriangle", vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
            triangle = await tool(client, "inspect_mesh", name="OpenTriangle")
            assert triangle["topology"]["boundary_edges"] == 3
            assert not triangle["uv_layers"]

    asyncio.run(run())
