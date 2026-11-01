---
name: blender-mcp-animation
description: Create, inspect, render and revise Blender animations through mcpBlender, including game/simulation asset animation and video assembly. Use for animation timelines, keyframes, camera motion and frame/video delivery; use blender-mcp-design for static modeling.
---

Inspect the application session, scene, frame range, FPS, actions and resources
with `bridge_status`, `get_scene_info` and `inspect_resources`. Confirm the intended
duration, motion and export destination from the task. Use GUI mode for viewport
feedback; background mode supports camera renders. Discover renderer and video
capabilities with `get_capabilities` rather than assuming GPU/codec availability.

Use `set_keyframes` for object location, Euler rotation (radians) and scale, and
`configure_render` for timing/FPS. Named domain tools take their data in `params`.
Use `execute_code` for bones, constraints, drivers, shape keys, animation layers,
geometry nodes or other installed bpy functionality. Return JSON inspection data
in `result`; check script success and traceback. Operator context and animation API
details depend on the running Blender version.

Inspect evaluated poses at key and intermediate frames using `set_frame` and
independent Python readback. Check transforms, framing and intended endpoints;
successful key insertion does not prove the motion is right. Save a `.blend`
project and force a recovery `checkpoint` at useful milestones.

Render a small preview frame sequence before committing to expensive output.
`render_animation` returns immediately by default and reports per-frame progress;
poll `get_operation`/`wait_operation`, then view its `preview_artifacts` with
`get_image`. Inspect beginning, middle and end frames. Use a stable request ID for
retry/reconnect recovery; do not replay an uncertain interrupted edit.

Check `storage_status` before long sequences and watch live storage fields while
rendering. Completed PNGs remain when a sequence stops. Queued work cancels
immediately, frame sequences cancel between frames, and Python can cooperate via
`check_cancelled()`. Native render/video calls are not forcibly interrupted.

Use `vse_add_strip`, `vse_edit_strip`, `vse_remove_strip` and `vse_list` to assemble
image/movie, sound and text strips when a video is requested. `render_video`
exports a tracked H.264/AAC MP4 from the sequencer. Verify actual encoded dimensions,
FPS/duration, frame count and audio with independent media inspection when available.
For game/simulation assets, preserve editable animation and export GLB with the
intended actions; verify playback and material/axis/scale behavior in the target
engine when in scope. Video export alone does not validate engine animation.
