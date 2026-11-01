"""Real shape-key edits and combined morph/skin delivery with Python disabled."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

from fastmcp.exceptions import ToolError
import pytest

from test_skeletal_animation import animation_live, client_for, tool, make_limb, pytestmark


def test_shape_coordinates_weights_and_topology_guards(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            assert "execute_code" not in {t.name for t in await client.list_tools()}
            await tool(client, "create_mesh", name="ShapePanel", vertices=[[80, 0, 0], [82, 0, 0], [82, 0, 1], [80, 0, 1]], faces=[[0, 1, 2, 3]])
            base = await tool(client, "inspect_mesh", name="ShapePanel")
            topology = (await tool(client, "inspect_shape_keys", name="ShapePanel"))["topology_sha256"]
            created = await tool(client, "create_shape_key", name="ShapePanel", key="Lift", expected_topology=topology, vertices=[{"vertex": 2, "value": [0, 0, 1]}])
            assert created["total_keys"] == 2 and created["managed_topology_matches"]
            assert created["keys"][0]["moved_vertices"] == 1
            assert created["keys"][0]["max_offset"] == 1
            assert created["keys"][0]["value"] == 0
            assert (await tool(client, "inspect_mesh", name="ShapePanel"))["geometry_sha256"] == base["geometry_sha256"]
            await tool(client, "set_shape_key_weights", name="ShapePanel", values={"Lift": .5})
            half = await tool(client, "inspect_mesh", name="ShapePanel")
            assert half["vertex_samples"][2]["world"] == pytest.approx([82, 0, 1.5])
            await tool(client, "edit_shape_key", name="ShapePanel", key="Lift", vertices=[{"vertex": 2, "value": [0, 0, 2]}])
            edited = await tool(client, "inspect_mesh", name="ShapePanel")
            assert edited["vertex_samples"][2]["world"] == pytest.approx([82, 0, 2])
            await tool(client, "edit_shape_key", name="ShapePanel", key="Lift", vertices=[{"vertex": 2, "value": [0, 0, 2]}])
            assert (await tool(client, "inspect_mesh", name="ShapePanel"))["geometry_sha256"] == edited["geometry_sha256"]
            for extra, message in (({"expected_topology": "0" * 64, "vertices": [{"vertex": 2, "value": [0, 0, 1]}]}, "topology"),
                                   ({"vertices": [{"vertex": 999, "value": [0, 0, 1]}]}, "outside")):
                with pytest.raises(ToolError, match=message):
                    await tool(client, "edit_shape_key", name="ShapePanel", key="Lift", **extra)
            await tool(client, "edit_shape_key", name="ShapePanel", key="Lift", reset_unspecified=True)
            assert (await tool(client, "inspect_mesh", name="ShapePanel"))["geometry_sha256"] == base["geometry_sha256"]
            await tool(client, "edit_shape_key", name="ShapePanel", key="Lift", mode="POSITION", vertices=[{"vertex": 2, "value": [82, 0, 3]}])
            assert (await tool(client, "inspect_mesh", name="ShapePanel"))["vertex_samples"][2]["world"] == pytest.approx([82, 0, 2])
            with pytest.raises(ToolError, match="Basis"):
                await tool(client, "edit_shape_key", name="ShapePanel", key="Basis", reset_unspecified=True)
            with pytest.raises(ToolError, match="already exists"):
                await tool(client, "create_shape_key", name="ShapePanel", key="Lift")
            with pytest.raises(ToolError, match="outside"):
                await tool(client, "create_shape_key", name="ShapePanel", key="Invalid", vertices=[{"vertex": 999, "value": [0, 0, 0]}])
            assert (await tool(client, "inspect_shape_keys", name="ShapePanel"))["total_keys"] == 2
            await tool(client, "remove_shape_key", name="ShapePanel", key="Lift")
            assert (await tool(client, "inspect_shape_keys", name="ShapePanel"))["total_keys"] == 1

    asyncio.run(run())


def test_shape_source_topology_and_shared_data_isolation(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            vertices = [[90, 0, 0], [92, 0, 0], [92, 0, 1], [90, 0, 1]]
            moved = [[x, y + (.5 if z else 0), z] for x, y, z in vertices]
            for name, coords, faces in (("ShapeTarget", vertices, [[0, 1, 2, 3]]),
                                         ("ShapeSource", moved, [[0, 1, 2, 3]]),
                                         ("WrongTopology", moved, [[0, 2, 1, 3]])):
                await tool(client, "create_mesh", name=name, vertices=coords, faces=faces)
            with pytest.raises(ToolError, match="topology"):
                await tool(client, "create_shape_key", name="ShapeTarget", key="Wrong", source_object="WrongTopology")
            assert not (await tool(client, "inspect_shape_keys", name="ShapeTarget"))["keys"]
            await tool(client, "create_shape_key", name="ShapeTarget", key="Lean", source_object="ShapeSource")
            await tool(client, "duplicate_object", name="ShapeTarget", new_name="SharedShape", linked_data=True)
            with pytest.raises(ToolError, match="shared"):
                await tool(client, "set_shape_key_weights", name="SharedShape", values={"Lean": 1})
            await tool(client, "set_shape_key_weights", name="SharedShape", values={"Lean": 1}, make_single_user=True)
            own = await tool(client, "inspect_mesh", name="SharedShape")
            assert own["vertex_samples"][2]["world"] == pytest.approx(moved[2])
            assert (await tool(client, "inspect_mesh", name="ShapeTarget"))["vertex_samples"][2]["world"] == pytest.approx(vertices[2])
            await tool(client, "edit_shape_key", name="SharedShape", key="Lean", vertices=[{"vertex": 2, "value": [0, 1, 0]}])
            assert (await tool(client, "inspect_mesh", name="SharedShape"))["vertex_samples"][2]["world"][1] == pytest.approx(1)
            await client.call_tool("save_blend", {"path": "shape-keys.blend"})
            await client.call_tool("open_blend", {"path": "shape-keys.blend"})
            assert (await tool(client, "inspect_shape_keys", name="SharedShape", key="Lean"))["keys"][0]["value"] == 1
            assert (await tool(client, "inspect_shape_keys", name="ShapeTarget", limit=1))["next_offset"] == 1

    asyncio.run(run())
