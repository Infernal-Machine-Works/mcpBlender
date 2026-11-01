---
name: blender-mcp-render
description: Set up and review Blender materials, lighting, cameras and still renders through mcpBlender. Use for visual look development and rendered image delivery; use blender-mcp-design for dedicated modeling and game asset export.
---

Start with `bridge_status` and `get_scene_info` to identify the session, scene,
camera and current policy. If a new Blender session is needed and startup is in
scope, use `start_blender`. GUI mode supports viewport images; background mode
supports camera renders. Preserve an existing project's objects and settings unless
the task calls for changing them.

Use `execute_code` to inspect or edit material nodes, lighting, camera framing,
color management and renderer settings. `bpy` is available, and structured output
is assigned to the JSON-compatible `result` variable. Check the returned `success`
and traceback. Namespace variables do not persist across calls; scene data does.
Native Blender output may be in the Blender log rather than captured Python stdout.

Begin with a small preview and suitable sample count, then inspect the actual
image before committing time to a larger render. `capture_viewport` produces a
GUI viewport artifact. `render_still` submits a camera-render job; poll `get_job`
until it succeeds or fails, then call `get_image` with the completed result's
`artifact_id`. Render dimensions are limited to 16–4096 pixels per axis. The
structured render tool restores temporary resolution, output path, engine, format
and active camera after completion; intentional lasting look changes belong in
the scene setup.

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
