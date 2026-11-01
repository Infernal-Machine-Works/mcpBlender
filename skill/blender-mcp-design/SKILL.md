---
name: blender-mcp-design
description: Create, inspect and edit Blender models and game assets through mcpBlender, including preparation and GLB export for Godot. Use for live Blender modeling and asset validation; use blender-mcp-render for dedicated lighting and image work.
---

Use the available mcpBlender tools. Tool namespaces depend on the client's server
name. `bridge_status` reports the connected Blender session and current policy.
Use `start_blender` to choose GUI mode when viewport feedback is required. Lazy
startup may also occur on the first Blender operation, according to configuration
and key policy. Do not infer a dead process from a busy or timed-out operation.

Inspect the scene before editing: check objects, names, dimensions, unit scale,
selection, materials and the current project path with `get_scene_info` and
`get_object_info`. These tools expose local transforms and object dimensions;
use `execute_code` for evaluated geometry, manifold checks, world-space measurements,
UVs, material nodes, rigs and animation details when the request needs them.

`execute_code` runs Python on Blender's main thread. `bpy` is preloaded; assign a
JSON-compatible value to `result`. Each call has a fresh namespace but shares the
live scene. Prefer direct data API edits where operators would depend on mode,
area or active selection. Use explicit context overrides for operators that need
them. Report `success=false` scripts as failures even when the MCP tool call itself
completed. Edits before an exception are not rolled back.

Use clear object names and preserve existing user assets. After editing, inspect
the changed geometry independently and request a viewport image with
`capture_viewport`, then **view it** with `get_image`. Headless Blender has no
viewport: use a camera render and distinguish visual evidence from geometry checks.
Keep inspection proportional to the requested asset: dimensions and origin matter
for game props; topology, normals, UVs and deformation matter where the asset needs
them. Object counts alone do not validate a model.

For Godot delivery, verify intended units, dimensions, origins, axes, selection,
material assignments and animation clips. Save the editable `.blend` project and
export a GLB with `export_glb`. Structured file paths are relative to the active
`library_root`. Existing project/export files require explicit `overwrite=true`;
use it when replacement is authorized by the task. `export_glb` exports selected
objects by default and handles glTF axis conversion. Avoid additional blind axis
rotations. Verify the exported artifact and, when Godot is available and in scope,
check import there. GLB export is not proof of Godot material, collision or gameplay
behavior.

The scene is shared across keys and clients. Operations execute serially; group
dependent edits in one Python operation when they must not interleave. For long
work, use `submit_operation` or `execute_code(wait=false)` with a caller-chosen
`request_id`, then poll `get_operation`. Reusing the same ID and arguments recovers
the same operation across connections and frontend restarts. Changed arguments
need a new ID. `interrupted`/`outcome_unknown` requires inspection; it is not a cue
to replay a mutation.

Use `checkpoint` at meaningful editing milestones. Automatic recovery restores a
completed checkpoint, which can predate the latest edits. `bridge_status` reports
session identity and checkpoint/recovery errors. Check `storage_status` before
large imports/exports; operation storage fields report current free bytes and
the estimate/reserve used. Arbitrary Python output size is not predictable.

The active
`enable_autonomous_execution` flag is advisory for the orchestrator. Python has
Blender's OS permissions, and structured tool roots do not sandbox arbitrary code.
