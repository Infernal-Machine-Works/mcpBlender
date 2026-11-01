---
name: blender-mcp-render
description: Set up and review Blender materials, lighting, cameras and still renders through mcpBlender. Use for visual look development and rendered image delivery; use blender-mcp-design for dedicated modeling and game asset export.
---

Start with `bridge_status` and `get_scene_info` to identify the session, scene,
camera and current policy. If a new Blender session is needed and startup is in
scope, use `start_blender`. GUI mode supports viewport images; background mode
supports camera renders. Preserve an existing project's objects and settings unless
the task calls for changing them.

Prefer `inspect_resources`, `configure_material`, `assign_material`,
`configure_light`, `configure_camera` and `configure_render` for supported setup.
These take a typed `params` object and work with Python execution disabled.
For reusable surfaces, prefer `apply_material_preset` with PBR, TEXTURED_PBR,
GLASS or EMISSION. PBR accepts base_color_texture, roughness_texture,
metallic_texture and normal_texture; data maps use Non-Color, base color sRGB,
and packing defaults true. Roughness/metallic maps use their red channel. Assign
the material with assign_material. Existing materials require replace=true.

For custom supported graphs, discover socket identifiers/indexes and property
choices with shader_node_catalog, then inspect_shader_graph. configure_shader_graph
creates/replaces a graph; configure_shader_node, connect_shader_nodes,
disconnect_shader_input and remove_shader_node support incremental edits. Input
defaults cannot override a linked input until it is disconnected. Existing links
need replace_existing=true to replace. Graph edits preflight on a temporary copy;
rare application failures can still leave partial edits. Script nodes, arbitrary
RNA, linked materials and animated shader graphs are excluded. Procedural shaders
and glass need target-engine review/baking; the textured PBR export is qualified.
For a standard persistent preview stage, discover and plan `preview_setup`, then
run it with `inputs` containing mesh `objects` and a fresh `prefix`. It fits an
active camera and creates key/fill/rim area lights around evaluated bounds, leaving
existing lights and render settings intact. Check the step result's camera fit
and view the render. `run_action` arguments are direct action/inputs/wait/request_id;
wait=true returns a full operation record whose state must be checked even when
the MCP call succeeds. Failed/cancelled actions can have partial edits/files.
Set the render aspect before `frame_objects`, which fits an existing camera
(`name`) to named meshes (`objects`) using a view preset or world direction.
Check `fully_in_frame`, then judge the rendered image. Fit covers the current
frame only; refit after aspect or pose changes. Orthographic setup uses
`configure_camera` with `projection="ORTHO"`.

Use `execute_code`, when permitted, for unsupported nodes/groups, color management
or settings not yet exposed by structured tools. `bpy` is available; assign structured output
to `result`, and check `success` and traceback. Namespace variables do not persist
across calls; scene data does. Native Blender output may be in the Blender log.

Begin with a small preview and suitable sample count, then inspect the actual
image before committing time to a larger render. `capture_viewport` produces a
GUI viewport artifact. `render_still` submits a camera-render job; poll `get_job`
until it succeeds or fails, then call `get_image` with the completed result's
`artifact_id`. Render dimensions are limited to 16–4096 pixels per axis. The
structured render tool restores temporary resolution, output path, engine, format
and active camera after completion; intentional lasting look changes belong in
the scene setup.

For motion previews, `animation_preview` renders PNGs and optional H.264 through a
temporary VSE scene. It can choose temporary skeletal/morph clips and restores live
animation/frame/render state. View the returned step result's preview_artifacts;
inspect partial_output after failure/cancellation. The camera fit covers one frame,
so allow enough room for the entire motion. Native video encoding is blocking.

Judge framing, silhouette, lighting, surface readability, clipping and the user's
requested style from the retrieved image. Adjust and render again when needed.
Successful file creation or a completed job alone does not prove visual quality.
Use the intended production renderer for final fidelity; a workbench preview does
not validate material shading. Do not infer GPU support from installed Blender;
inspect capabilities or use a bounded CPU test when the hardware is unknown.

Rendering can occupy Blender's main thread and make the GUI busy. The separate
broker keeps `get_operation`, `get_job` and storage reports available. Operation
and artifact identifiers survive frontend restart. Frame sequences report completed
frames; native still renders may not have finer progress. A timeout does not stop
the render. Use a stable request ID and recover the existing operation before
resubmitting. Remote token URLs expire after ten minutes.

Check `storage_status` before long/high-resolution output and watch operation
storage fields during rendering. Estimates identify their assumptions; they do
not guarantee space if other processes consume it. `checkpoint` preserves an
editing milestone before expensive work. Preserve wanted images and the editable
project for delivery. Save paths
are relative to the active `library_root`; arbitrary Python has Blender's full OS
permissions. Report which images were actually viewed and which checks remain.

Automatically generated render paths are temporary: default retention is seven
days with a 2 GiB target and one-hour minimum age. Use explicit output paths for
deliverables or set_operation_retention(operation_id, pinned=true). Structured
use as a material/VSE input pins the output group; pin before external/custom
Python use because those dependencies cannot be inferred. Preview cleanup with
preview_retention_cleanup before applying a plan. details_pruned=true means old
history expired; recover current state instead of replaying its mutation.
