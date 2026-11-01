# mcpBlender

Python distribution name: **`mcpBlender-tspspi`**. The import package and CLI
command remain `mcpblender`. Building a distribution does not publish it.

MCP control of an installed, running Blender for our internal agent ecosystem.
Works through a private Unix domain socket with both stdio and authenticated
Streamable HTTP clients. Blender does **not** need rebuilding. The Blender side
uses only `bpy` and Python's standard library; MCP dependencies run outside Blender.

The main workflows are animation production, game/simulation asset modeling, and
model visualization. Structured tools cover meshes, textured materials, lighting,
cameras, keyframes, frame sequences, video editing, project save/open, GLB export
and external asset APIs. Arbitrary Python exposes the rest of installed bpy. Tools are designed for our ecosystem rather
than exact compatibility with another Blender MCP implementation.

## Installation

Requires Python 3.11+, Blender 4.5+ and a Unix system supporting same-user socket
peer checks (FreeBSD/macOS/BSD `getpeereid`, or Linux `SO_PEERCRED`). Live tests
currently cover the installed FreeBSD Blender 5.0.1; other versions are unqualified.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[remote,test]'
.venv/bin/mcpblender --config ./mcpblender.conf --init-config
```

For development with dependencies already available, every CLI example also works
as `python3 -m mcpblender.server ...` from this checkout. No installation into
Blender's Python environment is needed.

## Blender startup and attachment

The MCP server can start Blender through `start_blender`. With
`launch.auto_start=true` (the default), the first operation needing Blender also
starts it lazily when its key has startup permission. Set this false for explicit
startup only. Operation-history and storage queries never require Blender startup. Default startup creates a fresh GUI
instance with factory settings, auto-execution disabled, and our bridge bootstrapped.
It does not modify Blender preferences or install files into Blender. The GUI has
an **MCP** sidebar panel with bridge start/stop controls.

For a CLI launch before connecting an agent:

```sh
mcpblender --config ./mcpblender.conf --start-blender
```

For a headless session, add `--background` or set `launch.background` in JSON.
Background mode stays alive to accept commands, supports camera rendering, and
cannot capture a GUI viewport. Startup reports readiness only after a round trip
through the socket and confirmation of the launched Blender PID. A live socket is
reused; a busy bridge does not trigger another launch. A launch file lock coordinates
multiple server processes using the same socket.

To attach to an existing GUI without restarting it, use Blender's Python console:

```python
import sys
sys.path.insert(0, "/absolute/path/to/mcpBlender")
from mcpblender.blender import bridge
bridge.start("/absolute/private/directory/bridge.sock")
```

Use the same `bridge.socket_path` in server configuration. `bridge.stop()` detaches.
For optional persistent add-on installation, generate a ZIP and install it through
Blender's **Preferences → Add-ons → Install from Disk**:

```sh
mcpblender --addon-zip /tmp/mcpblender-addon.zip
```

Enable **mcpBlender Unix Bridge**. It uses the default socket unless the Blender
process has `MCPBLENDER_BRIDGE_SOCKET` set. An add-on installation is unnecessary
when starting Blender through the MCP server. Do not enable the installed add-on
and separately register the checkout's UI module in the same session.

## Configuration

See [examples/mcpblender.conf](examples/mcpblender.conf). JSON is the configuration
source. Paths beginning with `~` are expanded. Relative file tool paths are relative
to the active policy's `library_root`.

The default bridge socket is `~/.local/state/mcpblender/bridge.sock`. Its immediate
parent must belong to the current user with mode `0700`; the socket is `0600`.
All socket peers verify UID. A separate stdlib-only broker owns the socket and
durable SQLite operation journal; the Blender main-thread worker claims one
operation at a time. This keeps status/progress queries responsive during native
Blender work. The bridge has no network listener or remote API key.
Existing live sockets and non-socket files are preserved; owned stale sockets may
be replaced. This bridge controls one shared Blender session: different remote
keys do not get separate scenes or OS accounts.

`launch.executable`, `launch.background`, `launch.factory_startup`,
`launch.startup_timeout_seconds` and `launch.log_path` configure startup. Blender
continues running after ordinary client/server disconnects. Use `shutdown_blender`
for graceful shutdown. It refuses a dirty project unless a save path or explicit
discard is supplied, and disables automatic restart for that intentional shutdown.
`launch.shutdown_on_exit=true` opts a launching frontend into checkpoint-and-quit
on exit; this defaults false to support multiple independent connections.

`bridge.timeout_seconds` bounds the server's wait for a response. A timeout or
disconnect does **not** cancel queued or running Blender code, undo edits, or stop
a render. Inspect the session and log before retrying a mutation.

## Authentication and transports

The remote authentication formats and Argon2id KDF fields match mcpFreeCAD:

- `Authorization: Bearer <key>`
- `X-API-Key: <key>`
- `?mcp=<key>`
- `?api_key=<key>`

Precedence follows that order. Generate or rotate a named key with:

```sh
mcpblender --config ./mcpblender.conf --genkey design-agent
```

The key is printed once. Only its salted Argon2id hash is stored in the private
configuration file. Rotation preserves an existing key's policy. New keys inherit
the policy model defaults; edit their `library_root` and flags before remote use.
The configuration is loaded at startup; restart the MCP server after key rotation
or policy changes. This leaves Blender running.

```sh
mcpblender --config ./mcpblender.conf --transport stdio
mcpblender --config ./mcpblender.conf --transport remotehttp
```

`--remote` is an alias for selecting remote HTTP. Remote mode refuses to start
without an API key. `/status` is a public server liveness check and does not prove
Blender connectivity; use authenticated `bridge_status` for that.

Example internal client configurations (substitute real paths and issued keys):

```json
{
  "mcpServers": {
    "blender-local": {
      "command": "/absolute/path/to/.venv/bin/mcpblender",
      "args": ["--config", "/absolute/path/to/mcpblender.conf", "--transport", "stdio"]
    },
    "blender-remote": {
      "url": "https://mcp.example.com/blender/mcp/?mcp=ISSUED_KEY"
    }
  }
}
```

Stdio uses the `stdio` policy block, needs no remote API key, and keeps stdout
reserved for MCP. Both transports use the same Blender Unix socket.

HTTP can listen on loopback TCP or a Unix socket (`remote_server.uds` takes
precedence over host/port). For HTTPS behind Apache, see
[examples/apache.conf](examples/apache.conf). The URL key is intentionally
supported. Application access logging is disabled to avoid recording query keys;
configure the proxy access log with `%U` rather than `%r` or `%q`. Do not turn on
request URL debug logging. `remote_server.url_prefix` is the externally visible
base URL including the proxy path, used to construct artifact links.

## Policies, images and jobs

Each API key, and the stdio block, has these flags:

| Flag | Controls |
| --- | --- |
| `allow_code_execution` | Arbitrary Python execution |
| `allow_scene_edit` | Python, selection, transforms, frame changes, project open, asset import |
| `allow_library_write` | Project saves and GLB exports |
| `allow_snapshots` | Viewport images, camera renders and image retrieval |
| `allow_start_blender` | Explicit/lazy process startup and bridge restart |
| `allow_shutdown_blender` | Graceful application shutdown |
| `allow_external_assets` | Configured asset/API sources |
| `enable_autonomous_execution` | Advisory policy surfaced for orchestrators |

Disabled operations are hidden from tool discovery and denied on direct calls.
The autonomy flag is advisory, as in FreeCAD; the server adds no approval workflow.
Structured file tools resolve paths and reject traversal and symlink escapes from
`library_root`. Arbitrary Python has Blender's full OS permissions, so these roots
and per-key flags are not a Python sandbox. API keys share the same Blender scene.
Blender may also resolve linked dependencies of imported assets or projects;
the structured path check constrains the entry file, not Blender's internal reads.

`capture_viewport` returns an artifact identifier; `get_image` returns that image
as MCP image content. `render_still` returns a job identifier immediately; poll
`get_job`, then call `get_image` with the completed result's `artifact_id`.
Job state tracks durable broker operations; frame sequences and video encoding
report completed-frame progress. A native still render may have no finer progress. A camera
render occupies Blender's main thread and can leave the GUI busy until completion.

Remote artifacts have an authenticated `download_url` and, when an external URL
prefix is configured, a `download_url_with_token` valid for ten minutes. Artifact
ownership is checked for API-key downloads and jobs. Token links are bearer
capabilities that can be shared intentionally. Operation and artifact registries are private SQLite databases beside the socket
and survive MCP frontend restarts. The broker admits up to 128 queued/running
operations. History and output files are retained; there is no automatic journal
or artifact pruning.
Snapshot permission permits generated image files even when general library writes
are disabled. There is no automated file cleanup.

`execute_code` has `bpy` available and returns `success`, `stdout`, `stderr`, a
JSON-compatible `result`, and a traceback. Assign the output value to `result`:

```python
bpy.ops.mesh.primitive_cube_add(size=1)
obj = bpy.context.object
obj.name = "Prop_Block"
result = {"name": obj.name, "dimensions": list(obj.dimensions)}
```

Scripts can call `report_progress({...})` and `check_cancelled()` for cooperative
progress/cancellation. Each call has a fresh Python namespace. Scene data persists. Python exceptions
are reported with `success=false`; changes before the exception remain. Captured
Python stdout/stderr are capped at 128,000 characters each. Native Blender output
goes to the Blender log. The bridge bounds JSON messages to 8 MiB.

## Structured asset editing

Asset tools accept a typed `params` object, support `wait=false` and `request_id`,
and work when `allow_code_execution=false`. Editing requires `allow_scene_edit`;
`inspect_mesh` remains available to read-only clients.

- `inspect_mesh`: base or evaluated viewport geometry at the current frame, world
  bounds, triangle counts, boundary/non-manifold edges, degenerate faces, UV bounds
  and zero-area UV faces. Vertex samples are bounded (0–256); a geometry digest
  helps compare poses. The digest covers world vertices and polygon connectivity,
  not materials or UVs. This is not a self-intersection or UV-overlap test.
- `configure_modifier`: create/update a named `BEVEL`, `SUBSURF`, `DECIMATE`,
  `SOLIDIFY` or `TRIANGULATE` modifier with typed settings. Other stack entries
  remain intact. Settings include defaults; an update replaces the supported
  settings for that modifier. Angles are radians, distances are Blender units.
- `apply_modifier` / `remove_modifier`: explicitly bake or discard a stack entry.
  Baking shared mesh data requires `make_single_user=true`; shape-key meshes are
  refused. Applying a later stack entry follows Blender's native stack behavior.
- `generate_uv`: SMART island projection or CUBE projection of all faces into a
  named map (`uv_map`, default `UVMap`). Existing coordinates require
  `overwrite=true`; shared meshes require `make_single_user=true`. SMART accepts
  `angle_limit_degrees` and `island_margin`; CUBE accepts `cube_size`. The target
  UV map becomes active. Inspect UV bounds and zero-area faces with `inspect_mesh`.
- `duplicate_object`: choose `new_name`, `linked_data` (default false) and an
  optional world-space `offset`. The copy is linked to the current collection.
  Parent, constraints, materials and animation action references are retained;
  duplicating geometry does not create independent copies of those resources.
- `set_parent`: set a named `parent` or omit it to unparent. `keep_world=true`
  preserves placement using the parent inverse. Cycles and singular parents are
  refused; clearing a parent with world shear is refused rather than losing it.
- `apply_transforms`: bake `location` (default false), `rotation` and `scale`
  (default true) into mesh geometry. Child world placement is preserved. Animated,
  constrained, shape-key or linked/overridden targets are refused; descendants
  must have static transforms and ordinary object parenting. Shared meshes need
  `make_single_user=true`. Modifier parameters are not rescaled automatically.
- `frame_objects`: fit an existing camera (`name`) to evaluated bounds of named
  meshes (`objects`). Choose `view` (`FRONT`, `BACK`, `LEFT`, `RIGHT`, `TOP`,
  `BOTTOM`, `ISOMETRIC`) or a world `direction` from the asset toward the camera;
  `margin` defaults to 1.15. Fits perspective and orthographic cameras and reports
  normalized `projection_bounds` and `fully_in_frame`. Use `configure_camera`
  with `projection="ORTHO"` for an orthographic view. The camera must be static,
  unparented and have independent data. Fit uses current render aspect and frame;
  refit after changing them. Clipping distances may expand to accommodate the asset.

Asset mesh tools require Object mode; operator-based edits also require a visible,
selectable target in the current view layer. They restore object selection and
active object. Linked/overridden mesh editing is refused. Operations do not provide
rollback; inspect the scene after a failure.

Example `configure_modifier` arguments:

```json
{"params":{"name":"Prop","modifier":"SoftEdges","settings":{"type":"BEVEL","width":0.05,"segments":3}}}
```

## Structured skeletal animation

`create_armature`, `bind_mesh` and `inspect_rig` take typed `params` and work with
Python execution disabled. Creation/binding require scene-edit permission;
inspection is available to read-only clients.

Create bones from armature-local `head`/`tail` positions, optional `parent`,
`connected` and `roll_degrees`. The armature object's `location` places the whole
skeleton. Bone names must be unique, connected heads must match parent tails,
and cyclic parent relationships are rejected before submission. Creation
preserves active object and selection and never replaces an existing object.

`bind_mesh` names the mesh in `name` and the skeleton in `armature`:

- `AUTO` (default) uses Blender's native bone-heat weighting.
- `RIGID` assigns all vertices to the named `bone` with weight one.
- `EXPLICIT` accepts `weights`, e.g. `[{"vertex":0,"weights":{"Base":0.5,"Tip":0.5}}]`.
  Each vertex index appears once; weights normalize by default.

Bind an unparented, unbound mesh before animating the skeleton, with bones in an
unconstrained rest pose. Existing armature modifiers and conflicting bone-named
groups are refused. Shared mesh data requires `make_single_user=true`.
`require_complete=true` rejects incomplete coverage. Failed binding removes its
new groups/modifiers and restores placement; an isolated mesh copy may remain.
Native automatic weighting can occupy Blender's main thread; operation/storage
status remains available through the broker.

`inspect_rig` reports rest and evaluated pose matrices/endpoints, bone hierarchy,
and skin coverage, weight sums and sampled influences. Bone results are paginated
with `bone_offset`/`bone_limit`. At most 32 bound scene meshes are inspected
automatically; supply `meshes` for a specific subset. Counts identify truncation.

`create_animation_clip` names the rig in `name` and the new action in `clip`.
Supply `tracks` with a bone, property (`location`, `rotation_degrees` or `scale`)
and vector keyframes. Rotations use XYZ Euler **degrees** in bone-local rest
coordinates; translation uses the same local axes. Clips persist through saving
even when inactive. `activate` defaults true; existing action names are refused.

```json
{"params":{"name":"Arm","clip":"Wave","tracks":[{"bone":"Tip","property":"rotation_degrees","keyframes":[{"frame":1,"value":[0,0,0]},{"frame":12,"value":[45,0,0]}]}]}}
```

Use `set_pose_keyframes` to insert/replace specified keys in a managed clip. Other
tracks/frames remain, and only touched keys receive the chosen interpolation.
`set_active_clip` switches clips, resetting unkeyed bone channels to rest to avoid
leaking the previous pose. Omit `clip` to clear the action and reset pose.
These clip tools support ordinary skeletal actions without constraints, drivers
or NLA blending. Authoring rotations requires XYZ bones; imported compatible
skeletal actions can be selected using their `slot` identifier when ambiguous.

`inspect_animation` lists paginated actions/slots and reports channel paths, key
counts and ranges for a chosen or active action. The action list is global;
`managed_target` identifies clips authored for a specific rig. A chosen action
must resolve to skeletal channels on that rig.

`sample_animation` evaluates up to 64 `frames`, optionally in a chosen `clip`,
returning bone world endpoints and evaluated geometry for up to eight bound
`meshes`. Samples restore the previous frame, subframe, action, slot and original
pose channels, including on cooperative cancellation. Sampling is available to
read-only keys but needs a local rig for temporary evaluation and can mark Blender
dirty. Use sampled vertices/bounds as deformation evidence; inserted keys alone
do not prove skinning is correct. `inspect_rig` gives the current pose and weights.

## Blender to Godot

Use Blender for modeling, materials, UVs, rigging and animation; export selected
assets with `export_glb`. The initial export uses Blender's glTF exporter, GLB,
standard glTF +Y up, and uncompressed meshes. Animation export is configurable.
The exporter does not forcibly apply destructive transforms or modifiers.

Before export, verify object dimensions, scene unit scale, origins, local axes,
selection, normals, materials and desired animation clips. Keep the editable
`.blend` project alongside the GLB. Godot import, collision setup, shader fidelity
and game behavior require checks in the Godot project; GLB creation alone does
not prove that integration.

The installed FreeBSD Blender 5.0.1 glTF add-on crashes in its Draco library-name
probe even when compression is disabled. Our structured import/export calls use a
scoped in-memory workaround only when that FreeBSD probe raises `TypeError`.
It disables Draco availability for the operation and restores the original probe
afterward. Fixed Blender versions are left alone. Installed Blender files are
unchanged, and Draco-compressed assets are not supported by this workaround.

## Long operations and recovery

`submit_operation(command, params, request_id)` returns immediately for any
structured operation, including Python, imports/exports, renders and API transfers.
New domain tools use a typed `params` object and accept `wait` and `request_id`;
long renders and API transfers default to asynchronous execution.
`execute_code(..., wait=false)` is also supported.

Poll `get_operation` or bounded `wait_operation`, or recover IDs using
`list_operations`. `get_job` remains a render polling alias. A wait timeout does
not fail/cancel the operation. Reusing a request ID with identical command arguments
returns the same operation; changed arguments are rejected. Deduplication is scoped
to the API key (or the shared `stdio` identity).

Multiple frontends/connections share one broker. Blender operations execute
serially, and an animation sequence holds its turn across frames. Put edits into
one Python operation when they must form an uninterrupted batch. There is no
rollback. Queued work can be cancelled; running Python must call `check_cancelled`,
and frame sequences stop between frames. Native blocking calls and VSE video
encoding are not forcibly interrupted.

The frontend/worker reconnect after broker restart; `restart_bridge` exercises
this while preserving Blender. Claimed work is not claimed twice, and completion
acknowledgements can be retried. If Blender exits, unfinished work becomes
`interrupted` with `outcome_unknown=true`; edits are never automatically replayed.

For server-launched instances, `launch.auto_recover=true` supervises Blender
independently of frontend connections. It allows `launch.max_recovery_attempts`
restarts until an explicit launch resets the budget. A live busy process is not a
crash. Intentional shutdown disables recovery. Manually attached instances are
not taken over by the supervisor.

Recovery copies follow successful edits, throttled by
`launch.checkpoint_interval_seconds` (default 30). Dirty scenes are checkpointed
before renders; `checkpoint` forces a copy. Files live beside the socket in
`recovery/`; `launch.checkpoint_copies` defaults to three. Rotation removes only
older service-generated checkpoints. Recovery opens the latest completed copy
with auto-execution disabled. `start_blender(recover=false)` requests fresh startup.
Status reports checkpoint/recovery paths and errors, application session and broker
epoch. Edits after the last checkpoint and in-flight external effects may be lost
or uncertain.

## Storage feedback

`storage_status` reports current free bytes, configured reserve, usable space and
warnings for the active library. Operation status includes fresh storage data even
while Blender is busy; frame/download progress also carries it. Renders, downloads,
saves and checkpoints check capacity before admission and execution. Frame renders
and streaming downloads recheck as output grows.

`storage.reserve_bytes` defaults to 256 MiB; `storage.warn_below_bytes` to 2 GiB.
Estimates identify conservative pixel bounds, configured download limits,
heuristics, or unknown sizes. They cannot predict arbitrary Python output or quotas;
other processes may consume space after a check. Failures retain completed files;
the server never deletes user output to make room.

## External asset and generation APIs

Configure named `asset_sources` in private JSON:

```json
{
  "asset_sources": {
    "generator": {
      "base_url": "https://assets.example.com/api/",
      "headers": {"Authorization": "Bearer PROVIDER_CREDENTIAL"},
      "timeout_seconds": 120,
      "max_bytes": 268435456
    }
  }
}
```

`request_asset_api` submits JSON requests or polls provider jobs. `download_asset`
streams a result into the library, optionally verifies SHA-256 and publishes it
atomically. Import models with `import_asset`; use images in `configure_material`.
`upload_asset` sends refined assets with PUT/POST. Credentials, signed URLs and
request bodies stay outside the journal. Resources and redirects must remain on
the configured origin.

These are generic HTTP/JSON/file adapters. Specific diffusion services may require
multipart or other provider-specific adapters. No real/paid provider is configured
or qualified. Network jobs continue independently of Blender; a broker exit makes
unfinished transfers uncertain. Remote POST/upload effects are not replayed.

## Representative editing fixture

```sh
python3 tools/building_workflow.py /tmp/CHOOSE_A_NEW_BUILDING_DIRECTORY
```

This fixture retains an editable exterior building with image-textured brick, roof,
windows/glazing, a door, trim, lights and an animated camera. It round-trips an awning
through an authenticated loopback API, packs the texture, saves/reopens the project,
exports GLB, renders 12 frames, assembles image/text/sound strips in Blender's VSE,
and exports H.264/AAC MP4. `ffprobe` independently checks video dimensions, frame
count and audio. The API fixture does not exercise an actual diffusion model.

## Skills and verification

Repository skills:

- [blender-mcp-design](skill/blender-mcp-design/SKILL.md): modeling, inspection and asset preparation.
- [blender-mcp-render](skill/blender-mcp-render/SKILL.md): materials, lighting, camera previews and image review.
- [blender-mcp-animation](skill/blender-mcp-animation/SKILL.md): animation, frame sequences and video delivery.

Copy those skill directories into the consuming agent's skill directory when
deploying. They are kept in the repository rather than installed globally by this
project. Tool descriptions remain usable by other MCP agents such as PQC.

```sh
python3 -m pytest -q
MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q
```

The opt-in suite starts disposable background Blender in a private `/tmp` directory,
uses real stdio and HTTP clients, and terminates only its own test processes.
For a separately authorized GUI smoke test, run
`python3 tools/gui_smoke.py /tmp/CHOOSE_A_NEW_EVIDENCE_DIRECTORY` on the current
display. It retains a test project, viewport image, log and JSON evidence, then
stops only its own Blender process.
See [VALIDATION.md](VALIDATION.md) for the checks performed on this host and their limits.
