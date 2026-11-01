"""Managed render cleanup through MCP without starting a second application."""
import asyncio
from pathlib import Path

from mcpblender.config import load_config, write_config, Retention
from test_asset_editing import assets_live, client_for, tool, pytestmark


def test_render_cleanup_pin_and_stable_request_id(assets_live):
    root, path = assets_live
    config = load_config(path)
    config.retention = Retention(enabled=False, min_age_seconds=0, max_age_seconds=0, history_max_age_seconds=0)
    config_path = root / "retention.json"
    write_config(config_path, config)

    async def run():
        async with client_for(config_path) as client:
            # Explicit render paths are deliverables, generated paths are temporary.
            queued = (await client.call_tool("submit_operation", {"command": "render_still", "params": {"width": 32, "height": 32, "engine": "CYCLES"}, "request_id": "retention-render"})).data
            rendered = (await client.call_tool("wait_operation", {"operation_id": queued["operation_id"], "timeout_seconds": 60})).data
            assert rendered["state"] == "succeeded", rendered
            image = Path(rendered["result"]["path"])
            assert image.is_file()
            status = (await client.call_tool("retention_status", {"limit": 1})).data
            assert status["output_groups"][0]["operation_id"] == queued["operation_id"]
            assert not status["automatic_enabled_for_policy"] and status["next_offset"] is None
            assert not (await client.call_tool("retention_status", {"offset": 1, "limit": 1})).data["output_groups"]
            await client.call_tool("set_operation_retention", {"operation_id": queued["operation_id"], "pinned": True})
            assert not (await client.call_tool("preview_retention_cleanup")).data["files"]
            await client.call_tool("set_operation_retention", {"operation_id": queued["operation_id"], "pinned": False})
            preview = (await client.call_tool("preview_retention_cleanup")).data
            assert [f["path"] for f in preview["files"]] == [str(image)]
            cleaned = (await client.call_tool("apply_retention_cleanup", {"plan_id": preview["plan_id"]})).data
            assert cleaned["deleted_logical_bytes"] > 0 and not image.exists()
            replay = (await client.call_tool("submit_operation", {"command": "render_still", "params": {"width": 32, "height": 32, "engine": "CYCLES"}, "request_id": "retention-render"})).data
            assert replay["operation_id"] == queued["operation_id"] and replay["details_pruned"]
            assert not image.exists()
            assert (await client.call_tool("retention_status")).data["registered_output_groups"] == 0
            created = (await client.call_tool("create_primitive", {"params": {"name": "RetentionKeptMesh"}, "request_id": "kept-mutation"})).data
            history = (await client.call_tool("preview_retention_cleanup")).data
            await client.call_tool("apply_retention_cleanup", {"plan_id": history["plan_id"]})
            recovered = (await client.call_tool("create_primitive", {"params": {"name": "RetentionKeptMesh"}, "request_id": "kept-mutation"})).data
            assert recovered["operation_id"] == created["operation_id"] and recovered["details_pruned"]
            assert sum(o["name"] == "RetentionKeptMesh" for o in (await client.call_tool("get_scene_info")).data["objects"]) == 1
    asyncio.run(run())
