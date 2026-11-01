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
For skeletal animation, prefer `create_armature`, `bind_mesh`,
`create_animation_clip`, `set_pose_keyframes` and `set_active_clip`. These work
with Python execution disabled. Define bones with armature-local head/tail
coordinates and optional parents/connected joints. Bind meshes before posing:
AUTO computes native weights, RIGID needs one bone, and EXPLICIT accepts
per-vertex influences. Inspect coverage and weight sums with `inspect_rig`.
Existing bindings/group conflicts are refused; shared mesh edits need explicit
isolation. Unweighted vertices are an error by default.

Clips name the armature in `name` and the action in `clip`; tracks specify a
`bone`, `property` and vector `keyframes`. Bone `rotation_degrees` uses XYZ Euler
degrees relative to rest-bone axes, unlike object `set_keyframes` rotations in
radians. Clips persist while inactive. Duplicate clip names are refused; use
`set_pose_keyframes` to edit keys. Clip switching resets unkeyed channels to rest.
Ordinary single-action rigs and muted glTF NLA stashes are supported; constraints,
drivers and active NLA blending need another workflow. Imported ambiguous actions
require an explicit slot from `inspect_animation`.

Use `execute_code`, when allowed, for constraints, drivers, shape keys, animation
layers or geometry nodes not yet exposed by structured tools. Return inspection
data in `result` and check success/traceback. API details depend on Blender version.

Inspect evaluated poses at key and intermediate frames using `set_frame` and
`inspect_rig`/`inspect_mesh`, or use `sample_animation` to compare several frames
and clips in one operation. Sampling returns world bone endpoints and evaluated
mesh samples/bounds/digests, then restores frame, action, slot and pose even on
cooperative cancellation. It temporarily evaluates a local rig and can mark the
project dirty. Check transforms, framing and intended endpoints;
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
Select both the armature and meshes for GLB delivery. Managed clips are exported
for their own rig; unrelated actions are not broadcast automatically. Preserve
the `.blend`, inspect the GLB skin/clip contents and re-import when useful.
