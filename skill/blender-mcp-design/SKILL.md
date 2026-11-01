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
use `inspect_mesh` for base/evaluated geometry, boundary/non-manifold edges,
world bounds, UV coverage bounds and sampled world vertices at the current frame.
Compare base and evaluated geometry to distinguish stored topology from modifier
results. The geometry digest excludes UVs/materials; the checks do not detect
self-intersection or UV overlap.

Prefer structured tools for the supported pipeline, including when Python
execution is disabled: `create_primitive` / `create_mesh`, `configure_modifier`,
`apply_modifier` / `remove_modifier`, `generate_uv`, `duplicate_object`,
`set_parent`, `apply_transforms`, `configure_material` and `assign_material`.
Arguments for these tools are inside `params`. Use named resources to avoid
selection-dependent edits. UV generation requires explicit overwrite for an
existing map. Independent duplicates are the default; shared mesh edits require
`make_single_user=true`. Apply transforms before adding dimension-sensitive
modifiers when appropriate; modifier parameters are not rescaled automatically.
Operator-based asset tools require Object mode and a visible/selectable target;
transform baking supports static unconstrained meshes. Inspect partial results
after an error; these operations do not roll back.

Discover `list_actions` and `describe_action`, then validate a named workflow with
`plan_action`. `game_asset_prepare` defaults to inspection of `objects` and a
triangle budget. Request transforms, UV generation or triangulation explicitly;
optional save/export paths require library-write permission. It preflights all
targets, but later failures can leave edits. Its edit/export mode supports static
meshes; use rig/morph tools for animated deformation. Review report issues rather
than treating completion as engine compatibility. `run_action` takes `action`,
`inputs`, optional `request_id` and `wait` directly, without a params wrapper.
With wait=true it returns an operation record even for failure/cancellation: check
state and inspect each step's result. Reuse the planned request ID for matching
generated output paths.

Use `preview_setup` for a fresh persistent fitted camera and three asset-relative
area lights; existing scene lights remain. Choose a fresh prefix. Alternatively,
use `configure_camera` then `frame_objects` to fit named meshes for a preview;
configure the intended render aspect first and check `fully_in_frame`. Refit after
pose/aspect changes. Use Python only for functionality the structured tools do
not yet expose, such as specialized node graphs, when policy allows. For skeletons
and animation, use the structured armature/binding/clip tools described in
`blender-mcp-animation`; inspect weights with `inspect_rig` and evaluate deformation
with `sample_animation` before export.

For relative morph targets, use `create_shape_key` / `edit_shape_key` with sparse
Basis-relative offsets, absolute mesh-local positions or a same-topology source.
`inspect_shape_keys` exposes topology fingerprints, changed-vertex samples and
weight state; carry `expected_topology` into later indexed edits. Coordinate edits
preserve unlisted vertices by default. New keys start at weight zero; use
`set_shape_key_weights` for static previews after clearing active morph animation.
Basis edits and stale topology are refused. Use the animation skill for named
morph clips, combined bone/morph sampling and GLB animation delivery.

Use apply_material_preset for textured PBR surfaces, with packed color, roughness,
metallic and normal maps. For custom supported shaders use shader_node_catalog,
inspect_shader_graph and the typed node/link tools. Existing materials require
explicit graph replacement; prefer incremental edits when preserving a user graph.
Procedural shader graphs do not imply equivalent Godot materials; review or bake
those surfaces before engine delivery.

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
export a GLB with `export_glb` or `export_objects_glb` (explicit `objects` inside
params, restoring selection). Include the rig and meshes for skinned delivery.
Structured file paths are relative to the active
`library_root`. Existing project/export files require explicit `overwrite=true`;
use it when replacement is authorized by the task. `export_glb` exports selected
objects by default and handles glTF axis conversion. Avoid additional blind axis
rotations. Verify the exported artifact and, when Godot is available and in scope,
check import there. GLB export is not proof of Godot material, collision or gameplay
behavior.

The scene is shared across keys and clients. Operations execute serially; named
actions group dependent steps into one operation. Operator-local JSON recipes can
compose supported structured commands without Python. For long
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

Explicit project/export paths are protected from temporary-output cleanup. Pin
wanted generated previews with set_operation_retention before external/custom
Python use; structured image/VSE/import use pins them automatically. Inspect
retention_status and preview_retention_cleanup for scope and candidates. Old
history can return details_pruned=true; its request-ID tombstone still prevents
replay, so inspect current state rather than resubmitting with a new ID blindly.

The active
`enable_autonomous_execution` flag is advisory for the orchestrator. Python has
Blender's OS permissions, and structured tool roots do not sandbox arbitrary code.
