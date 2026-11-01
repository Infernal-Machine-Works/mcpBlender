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
            assert (await client.call_tool("get_object_info", {"name": "ShapePanel"})).data["shape_keys"] == 2
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


def test_named_morph_clips_and_restorable_sampling(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            await tool(client, "create_primitive", name="MorphCube", location=[100, 0, 1])
            base = await tool(client, "inspect_mesh", name="MorphCube")
            await tool(client, "create_shape_key", name="MorphCube", key="Wide", vertices=[
                {"vertex": v["index"], "value": [.5 if v["world"][0] > 100 else -.5, 0, 0]} for v in base["vertex_samples"]])
            await tool(client, "create_shape_key", name="MorphCube", key="Lift", vertices=[
                {"vertex": v["index"], "value": [0, 0, .5]} for v in base["vertex_samples"] if v["world"][2] > 1])
            created = await tool(client, "create_morph_clip", name="MorphCube", clip="Pulse", tracks=[{
                "key": "Wide", "keyframes": [{"frame": 1, "value": 0}, {"frame": 5, "value": 1}, {"frame": 9, "value": 0}]}])
            assert created["detail"]["total_tracks"] == 1
            assert created["detail"]["tracks"][0]["keys"] == 3
            await tool(client, "create_morph_clip", name="MorphCube", clip="LiftOnly", activate=False, tracks=[{
                "key": "Lift", "keyframes": [{"frame": 1, "value": 1}, {"frame": 9, "value": 1}]}])
            await client.call_tool("set_frame", {"frame": 3})
            before = await tool(client, "inspect_mesh", name="MorphCube")
            sample = await tool(client, "sample_morph_animation", name="MorphCube", frames=[1, 3, 5], sample_limit=8)
            for frame, weight, actual in zip((1, 3, 5), (0, .5, 1), sample["samples"]):
                assert actual["values"]["Wide"] == pytest.approx(weight)
                assert actual["mesh"]["world_bounds"]["min"][0] == pytest.approx(99 - .5 * weight)
                assert actual["mesh"]["world_bounds"]["max"][0] == pytest.approx(101 + .5 * weight)
            temporary = await tool(client, "sample_morph_animation", name="MorphCube", clip="LiftOnly", frames=[1, 5])
            assert temporary["restored_clip"] == "Pulse" and temporary["restored_frame"] == 3
            assert (await tool(client, "inspect_mesh", name="MorphCube"))["geometry_sha256"] == before["geometry_sha256"]
            await tool(client, "set_active_morph_clip", name="MorphCube", clip="LiftOnly")
            lifted = await tool(client, "inspect_shape_keys", name="MorphCube")
            assert {b["name"]: b["value"] for b in lifted["keys"] if b["name"] != "Basis"} == {"Wide": 0, "Lift": 1}
            with pytest.raises(ToolError, match="Clear the active"):
                await tool(client, "set_shape_key_weights", name="MorphCube", values={"Wide": .5})
            edited = await tool(client, "set_morph_keyframes", name="MorphCube", clip="Pulse", tracks=[{
                "key": "Wide", "keyframes": [{"frame": 5, "value": .8}]}])
            assert edited["detail"]["tracks"][0]["keys"] == 3
            assert (await tool(client, "inspect_morph_animation", name="MorphCube"))["active_clip"] == "LiftOnly"
            with pytest.raises(ToolError, match="already exists"):
                await tool(client, "create_morph_clip", name="MorphCube", clip="Pulse", tracks=[{"key": "Wide", "keyframes": [{"frame": 1, "value": 0}]}])
            await tool(client, "set_active_morph_clip", name="MorphCube")
            assert (await tool(client, "inspect_mesh", name="MorphCube"))["geometry_sha256"] == base["geometry_sha256"]
            with pytest.raises(ToolError, match="animation references"):
                await tool(client, "remove_shape_key", name="MorphCube", key="Wide")
            await tool(client, "create_primitive", name="OtherMorphCube")
            await tool(client, "create_shape_key", name="OtherMorphCube", key="Wide")
            with pytest.raises(ToolError, match="different mesh"):
                await tool(client, "set_active_morph_clip", name="OtherMorphCube", clip="Pulse")

    asyncio.run(run())


async def make_flexible_limb(client, rig="FlexRig", mesh="FlexLimb", clip="Flex", x=110):
    await make_limb(client, rig, mesh, x=x)
    base = await tool(client, "inspect_mesh", name=mesh, sample_limit=20)
    await tool(client, "create_shape_key", name=mesh, key="Flare", vertices=[
        {"vertex": v["index"], "value": [.3 if v["world"][0] > x else -.3, 0, 0]}
        for v in base["vertex_samples"] if v["world"][2] > 1])
    await tool(client, "create_animation_clip", name=rig, clip=clip, tracks=[{
        "bone": "Tip", "property": "rotation_degrees", "keyframes": [
            {"frame": 1, "value": [0, 0, 0]}, {"frame": 5, "value": [60, 0, 0]}, {"frame": 9, "value": [0, 0, 0]}]}])
    return await tool(client, "create_morph_clip", name=mesh, clip=clip, join_existing=True, tracks=[{
        "key": "Flare", "keyframes": [{"frame": 1, "value": 0}, {"frame": 5, "value": 1}, {"frame": 9, "value": 0}]}])


def test_combined_skeletal_and_morph_action_slots(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            created = await make_flexible_limb(client)
            clip = next(c for c in created["clips"] if c["name"] == "Flex")
            assert {s["target_type"] for s in clip["slots"]} == {"OBJECT", "KEY"}
            await client.call_tool("set_frame", {"frame": 3})
            before = await tool(client, "inspect_mesh", name="FlexLimb", sample_limit=20)
            sampled = await tool(client, "sample_morph_animation", name="FlexLimb", clip="Flex", armature="FlexRig", skeletal_clip="Flex", frames=[1, 3, 5], sample_limit=20)
            full = sampled["samples"][-1]
            assert full["values"]["Flare"] == 1
            assert full["mesh"]["world_bounds"]["min"][0] == pytest.approx(109.5, abs=1e-5)
            assert full["mesh"]["world_bounds"]["max"][0] == pytest.approx(110.5, abs=1e-5)
            top = [p["world"] for p in full["mesh"]["vertex_samples"][16:20]]
            assert sum(p[2] for p in top) / 4 == pytest.approx(1.5, abs=1e-5)
            assert (await tool(client, "inspect_mesh", name="FlexLimb", sample_limit=20))["geometry_sha256"] == before["geometry_sha256"]
            await tool(client, "set_pose_keyframes", name="FlexRig", clip="Flex", tracks=[{
                "bone": "Tip", "property": "rotation_degrees", "keyframes": [{"frame": 5, "value": [45, 0, 0]}]}])
            morph = await tool(client, "inspect_morph_animation", name="FlexLimb")
            assert morph["detail"]["tracks"][0]["keys"] == 3
            await tool(client, "set_morph_keyframes", name="FlexLimb", clip="Flex", tracks=[{
                "key": "Flare", "keyframes": [{"frame": 5, "value": .8}]}])
            assert (await tool(client, "inspect_animation", name="FlexRig"))["detail"]["total_tracks"] == 3
            await tool(client, "create_primitive", name="UnboundMorph")
            await tool(client, "create_shape_key", name="UnboundMorph", key="Flare")
            with pytest.raises(ToolError, match="skeletal owner"):
                await tool(client, "create_morph_clip", name="UnboundMorph", clip="Flex", join_existing=True, tracks=[{
                    "key": "Flare", "keyframes": [{"frame": 1, "value": 0}]}])

    asyncio.run(run())


def test_textured_morph_skin_render_and_glb_round_trip(animation_live):
    root, path = animation_live
    library = root / "library"
    library.mkdir(exist_ok=True)
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    pixels = b"".join(b"\x00" + b"".join(bytes((240, 150, 35) if (x // 4 + y // 4) % 2 else (30, 115, 175)) for x in range(16)) for y in range(16))
    (library / "morph-checker.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 16, 16, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))

    async def run():
        async with client_for(path) as client:
            rig, mesh, combined, morph_only = "MorphDeliveryRig", "MorphDeliveryLimb", "FlexDelivery", "FlareOnly"
            await make_flexible_limb(client, rig, mesh, combined, x=120)
            await tool(client, "create_morph_clip", name=mesh, clip=morph_only, activate=False, tracks=[{
                "key": "Flare", "keyframes": [{"frame": 1, "value": 0}, {"frame": 5, "value": .6}, {"frame": 9, "value": 0}]}])
            await tool(client, "generate_uv", name=mesh)
            await tool(client, "configure_material", name="MorphTexture", image_path="morph-checker.png", roughness=.6)
            await tool(client, "assign_material", objects=[mesh], material="MorphTexture")
            source = {}
            source[combined] = await tool(client, "sample_morph_animation", name=mesh, clip=combined, armature=rig, skeletal_clip=combined, frames=[1, 3, 5, 9], sample_limit=20)
            await tool(client, "set_active_clip", name=rig)
            source[morph_only] = await tool(client, "sample_morph_animation", name=mesh, clip=morph_only, armature=rig, frames=[1, 3, 5, 9], sample_limit=20)
            await tool(client, "set_active_clip", name=rig, clip=combined)
            await tool(client, "set_active_morph_clip", name=mesh, clip=combined)
            await client.call_tool("set_frame", {"frame": 5})
            await tool(client, "configure_render", resolution_x=192, resolution_y=192, fps=12, samples=4, frame_start=1, frame_end=9)
            await tool(client, "configure_camera", name="MorphCamera")
            fit = await tool(client, "frame_objects", name="MorphCamera", objects=[mesh], direction=[1, -3, .7], margin=1.6)
            assert fit["fully_in_frame"]
            await tool(client, "configure_light", name="MorphKey", type="AREA", location=[123, -4, 6], target=[120, 0, 1], energy=700, size=5)
            queued = await tool(client, "render_animation", directory="morph-preview", frame_start=1, frame_end=5, width=192, height=192, engine="CYCLES", camera="MorphCamera")
            rendered = (await client.call_tool("wait_operation", {"operation_id": queued["operation_id"], "timeout_seconds": 90})).data
            assert rendered["state"] == "succeeded", rendered
            assert rendered["storage"]["available_after_reserve_bytes"] > 0
            frames = sorted((library / "morph-preview").glob("*.png"))
            assert len(frames) == 5 and frames[0].read_bytes() != frames[-1].read_bytes()
            await tool(client, "vse_add_strip", name="MorphPreviewSequence", kind="IMAGE", paths=[str(p.relative_to(library)) for p in frames], duration=5)
            video = await tool(client, "render_video", path="morph-preview.mp4", frame_start=1, frame_end=5, width=192, height=192)
            encoded = (await client.call_tool("wait_operation", {"operation_id": video["operation_id"], "timeout_seconds": 90})).data
            assert encoded["state"] == "succeeded", encoded
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(library / "morph-preview.mp4")]))
            stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
            assert stream["codec_name"] == "h264" and int(stream["nb_read_frames"]) == 5
            assert stream["r_frame_rate"] == "12/1"
            await client.call_tool("save_blend", {"path": "morph-animation.blend"})
            await client.call_tool("open_blend", {"path": "morph-animation.blend"})
            reopened = await tool(client, "sample_morph_animation", name=mesh, clip=combined, armature=rig, skeletal_clip=combined, frames=[1, 3, 5, 9])
            assert [s["mesh"]["geometry_sha256"] for s in reopened["samples"]] == [s["mesh"]["geometry_sha256"] for s in source[combined]["samples"]]
            before = await tool(client, "inspect_mesh", name=mesh)
            await client.call_tool("select_objects", {"names": [rig, mesh]})
            await client.call_tool("export_glb", {"path": "morph-animation.glb"})
            assert (await tool(client, "inspect_mesh", name=mesh))["geometry_sha256"] == before["geometry_sha256"]
            await tool(client, "set_active_morph_clip", name=mesh, clip=combined)
            data = (library / "morph-animation.glb").read_bytes()
            size = struct.unpack_from("<I", data, 12)[0]
            gltf = json.loads(data[20:20 + size])
            assert {a["name"] for a in gltf["animations"]} == {combined, morph_only}
            action = next(a for a in gltf["animations"] if a["name"] == combined)
            assert {"weights", "rotation"} <= {c["target"]["path"] for c in action["channels"]}
            primitive = gltf["meshes"][0]["primitives"][0]
            assert len(primitive["targets"]) == 1 and "POSITION" in primitive["targets"][0]
            assert gltf["meshes"][0]["extras"]["targetNames"] == ["Flare"]
            assert {"JOINTS_0", "WEIGHTS_0", "TEXCOORD_0"} <= primitive["attributes"].keys()
            assert gltf["images"][0]["bufferView"] >= 0
            before_actions = {a["name"] for a in (await tool(client, "inspect_morph_animation", name=mesh))["clips"]}
            destination = os.environ.get("MCPBLENDER_MORPH_EVIDENCE_DIR")
            if destination:
                shutil.copytree(library, destination, dirs_exist_ok=True)
            imported = (await client.call_tool("import_asset", {"path": "morph-animation.glb"})).data
            scene = (await client.call_tool("get_scene_info")).data["objects"]
            imported_rig = next(o["name"] for o in scene if o["name"] in imported["objects"] and o["type"] == "ARMATURE")
            imported_shapes = [await tool(client, "inspect_shape_keys", name=o["name"]) for o in scene if o["name"] in imported["objects"] and o["type"] == "MESH"]
            candidates = [o["name"] for o in imported_shapes if o["total_keys"] > 1]
            assert len(candidates) == 1, imported_shapes
            imported_mesh = candidates[0]
            inspection = await tool(client, "inspect_morph_animation", name=imported_mesh)
            new_actions = [a["name"] for a in inspection["clips"] if a["name"] not in before_actions]
            imported_samples = {}
            for clip in (combined, morph_only):
                imported_clip = next(a for a in new_actions if clip in a)
                if clip == combined:
                    await tool(client, "set_active_clip", name=imported_rig, clip=imported_clip)
                else:
                    await tool(client, "set_active_clip", name=imported_rig)
                await tool(client, "set_active_morph_clip", name=imported_mesh, clip=imported_clip)
                sampled = await tool(client, "sample_morph_animation", name=imported_mesh, armature=imported_rig, frames=[1, 3, 5, 9])
                for actual, original in zip(sampled["samples"], source[clip]["samples"]):
                    assert actual["values"]["Flare"] == pytest.approx(original["values"]["Flare"], abs=1e-5)
                    for key in ("min", "max"):
                        assert actual["mesh"]["world_bounds"][key] == pytest.approx(original["mesh"]["world_bounds"][key], abs=1e-4)
                imported_samples[clip] = sampled
            evidence = {"source": source, "imported": imported_samples, "exported_clips": [a["name"] for a in gltf["animations"]], "camera_fit": fit, "ffprobe": probe}
            (library / "morph-evidence.json").write_text(json.dumps(evidence, indent=2))
            destination = os.environ.get("MCPBLENDER_MORPH_EVIDENCE_DIR")
            if destination:
                shutil.copytree(library, destination, dirs_exist_ok=True)

    asyncio.run(run())


def test_cancelled_morph_sampling_restores_weights_and_skeleton(animation_live):
    _, path = animation_live

    async def run():
        async with client_for(path) as client:
            rig, mesh, clip = "MorphCancelRig", "MorphCancelLimb", "MorphCancel"
            await make_flexible_limb(client, rig, mesh, clip, x=140)
            await tool(client, "configure_modifier", name=mesh, modifier="DetailedMorph", settings={"type": "SUBSURF", "levels": 4})
            await client.call_tool("set_frame", {"frame": 3})
            before = await tool(client, "inspect_mesh", name=mesh)
            values = {b["name"]: b["value"] for b in (await tool(client, "inspect_shape_keys", name=mesh))["keys"]}
            queued = (await client.call_tool("sample_morph_animation", {"params": {"name": mesh, "clip": clip, "armature": rig, "skeletal_clip": clip, "frames": list(range(1, 65)), "sample_limit": 0}, "wait": False})).data
            for _ in range(500):
                op = (await client.call_tool("get_operation", {"operation_id": queued["operation_id"]})).data
                if op["state"] == "running" and op.get("progress"):
                    break
                assert op["state"] not in {"succeeded", "failed", "cancelled"}, op
                await asyncio.sleep(.01)
            else:
                pytest.fail("Sampler did not start")
            await client.call_tool("cancel_operation", {"operation_id": queued["operation_id"]})
            cancelled = (await client.call_tool("wait_operation", {"operation_id": queued["operation_id"], "timeout_seconds": 30})).data
            assert cancelled["state"] == "cancelled", cancelled
            assert (await client.call_tool("get_scene_info")).data["frame"] == 3
            assert (await tool(client, "inspect_mesh", name=mesh))["geometry_sha256"] == before["geometry_sha256"]
            assert {b["name"]: b["value"] for b in (await tool(client, "inspect_shape_keys", name=mesh))["keys"]} == values
            assert (await tool(client, "inspect_rig", name=rig, meshes=[]))["active_clip"] == clip
            assert (await tool(client, "inspect_morph_animation", name=mesh))["active_clip"] == clip

    asyncio.run(run())


def test_morph_only_export_with_inactive_action_and_static_weight(animation_live):
    root, path = animation_live

    async def run():
        async with client_for(path) as client:
            name, clip = "SoloMorph", "SoloPulse"
            await tool(client, "create_primitive", name=name, location=[150, 0, 0])
            await tool(client, "create_shape_key", name=name, key="Lift", vertices=[{"vertex": 0, "value": [0, 0, 1]}])
            await tool(client, "set_shape_key_weights", name=name, values={"Lift": .25})
            await tool(client, "create_morph_clip", name=name, clip=clip, activate=False, tracks=[{
                "key": "Lift", "keyframes": [{"frame": 1, "value": 0}, {"frame": 5, "value": 1}]}])
            assert (await tool(client, "inspect_morph_animation", name=name))["active_clip"] is None
            before = await tool(client, "inspect_mesh", name=name)
            source = await tool(client, "sample_morph_animation", name=name, clip=clip, frames=[1, 3, 5])
            await client.call_tool("select_objects", {"names": [name]})
            await client.call_tool("export_glb", {"path": "morph-solo.glb"})
            assert (await tool(client, "inspect_mesh", name=name))["geometry_sha256"] == before["geometry_sha256"]
            after = await tool(client, "inspect_shape_keys", name=name, key="Lift")
            assert after["keys"][0]["value"] == .25 and after["active_clip"] is None
            data = (root / "library/morph-solo.glb").read_bytes()
            gltf = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
            assert {a["name"] for a in gltf["animations"]} == {clip}
            assert gltf["meshes"][0]["weights"] == [.25]
            imported = (await client.call_tool("import_asset", {"path": "morph-solo.glb"})).data
            imported_name = next(n for n in imported["objects"] if n.startswith(name))
            sampled = await tool(client, "sample_morph_animation", name=imported_name, frames=[1, 3, 5])
            for actual, expected in zip(sampled["samples"], source["samples"]):
                assert actual["values"] == pytest.approx(expected["values"])
                assert actual["mesh"]["world_bounds"]["min"] == pytest.approx(expected["mesh"]["world_bounds"]["min"])
            destination = os.environ.get("MCPBLENDER_MORPH_EVIDENCE_DIR")
            if destination:
                shutil.copy2(root / "library/morph-solo.glb", Path(destination) / "morph-solo.glb")

    asyncio.run(run())
