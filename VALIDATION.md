# Local validation: 0.2.0

## Latest complete regression

On 2026-11-01, FreeBSD Blender 5.0.1:

```sh
MCPBLENDER_TEST_BLENDER=1 MCPBLENDER_SKELETAL_EVIDENCE_DIR=/tmp/mb-skeletal-20261101 python3 -m pytest -q
```

**39 tests passed in 221.96 seconds.** The one warning is the pre-existing host
Starlette/AnyIO deprecated alias. The wheel built without dependency downloads
and includes the skeletal worker. Updated animation/design skills passed the
skill-creator validator. No permanent deployment or consuming-agent installation
was performed.

## Skeletal tools additions

`MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q tests/test_animation_contracts.py tests/test_skeletal_animation.py`
passed **3 tests** on Blender 5.0.1. Python execution was disabled. Live checks
created a connected two-bone armature, bound a segmented mesh with blended explicit
weights, verified world endpoints/paginated inspection and preserved selection,
and exercised rigid and native automatic binding. Existing binding and invalid
vertex references were refused. Contract tests reject cyclic/invalid skeletons,
malformed weight arguments and editing through a read-only policy.

Named clip addition: the focused command passed **5 tests**. It authored and
edited bone tracks, sampled key and intermediate poses, checked the upper mesh
ring against an independently calculated 45-degree endpoint, verified a fixed
lower ring, and compared exact geometry digests before/after temporary sampling.
Switching clips reset unkeyed channels; clearing the action restored rest pose.
Duplicate names, missing bones and cross-rig managed actions were refused.

Delivery/cancellation addition: the full suite above also covered a textured,
two-bone segmented mesh with blended skinning, two named clips, five rendered
preview frames and a 192x192 H.264/AAC MP4. Independent ffprobe inspection confirmed
five video frames. Beginning and bent-middle PNGs were opened and visually
inspected. A preview assembled from every other source frame is a motion sample,
not a timing qualification for the full clip.

The editable project survived save/reopen with matching sampled geometry digests.
Independent GLB JSON/binary inspection verified exactly the two intended clip
names, a two-joint skin, normalized exported weights, UVs and an embedded texture.
Unrelated actions from other test rigs were excluded. The GLB was re-imported;
both imported clips were selected and sampled through structured tools despite
their muted NLA stashes. At frames 1, 5 and 9, their evaluated mesh world bounds
matched the corresponding source clip within 0.0001 Blender units. Export restored
the source pose and removed its temporary NLA tracks.

A separate detailed-mesh sampler was cancelled while running. Its operation
became cancelled and the prior frame, clip and exact evaluated geometry digest
were restored. Python execution remained disabled throughout these workflows.

Evidence is retained under `/tmp/mb-skeletal-20261101/`: `skeletal-animation.blend`,
`skeletal-animation.glb`, `skeletal-evidence.json`, `rig-preview/` and
`rig-preview.mp4`. These are small-rig checks: automatic weighting was qualified
on a one-bone fixture; explicit blending, rigid binding and playback on two bones.
They do not establish arbitrary character-rig or target-engine correctness.

## Structured asset additions

Asset-stage full regression command:

```sh
MCPBLENDER_TEST_BLENDER=1 MCPBLENDER_ASSET_EVIDENCE_DIR=/tmp/mb-asset-editing-20261101 python3 -m pytest -q
```

**32 tests passed in 151.53 seconds**, with the same host Starlette/AnyIO
deprecation warning. Wheel construction also passed, including the new asset
worker module. The updated design/render skills passed their schema validators.

The Python-disabled asset fixture additionally qualified perspective and
orthographic camera fits, portrait/landscape aspect ratios, multi-object fits,
view presets and a custom direction. It created a beveled textured prop through
structured tools, rendered a CPU preview, saved/reopened the project and exported
and re-imported a GLB. Independent GLB JSON inspection verified embedded image
data and UV attributes; re-imported world bounds matched the source. The cleaned
192x128 preview was opened and visually inspected. Evidence is retained in
`/tmp/mb-asset-editing-20261101/`: `asset-preview.png`, `asset-editing.blend`,
`asset-editing.glb` and `asset-evidence.json`. This qualifies static asset editing;
skeletal deformation and Godot integration remain separate work.

`MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q tests/test_asset_contracts.py tests/test_asset_editing.py`
passed **3 tests** on the installed Blender 5.0.1. A disposable stdio client had
Python execution disabled. It verified base/evaluated cube geometry and world
bounds, bevel baking, all five structured modifier types, removal, missing/type
errors, selection preservation and boundary detection on an open triangle.
Contract tests reject arbitrary modifier properties and excessive subdivision;
read-only policy tests check discovery and direct submission denial.

UV projection addition: the same focused command passed **4 tests**. SMART and
CUBE projection produced finite, nonzero-area UVs without changing geometry;
overwrite protection and two-map persistence through save/reopen were verified.

Hierarchy/transform addition: the focused command passed **5 tests**. Live checks
covered world placement under a rotated, nonuniformly scaled parent; cycle
refusal; child placement during parent transform baking; unparenting; independent
and shared geometry copies; shared-data refusal/isolation when baking a modifier;
and refusal to bake animated object transforms. Python execution remained disabled.

2026-11-01, FreeBSD, installed Blender 5.0.1, Python 3.11, FastMCP 4.0.3.
Blender was not rebuilt and its installed files/preferences were not changed.
Permanent deployment and consuming-agent configuration remain deferred.

## Initial 0.2.0 regression baseline

```sh
MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q
```

**26 tests passed in 149.35 seconds.** One warning comes from the host's
Starlette/AnyIO deprecated test alias. Live tests used disposable processes and
private temporary directories, with actual stdio and HTTP MCP clients.

Covered behavior:

- Same-UID socket verification, private parent/socket modes, live-socket preservation,
  stale-socket recovery and refusal to overwrite other files.
- FreeCAD-compatible Argon2id hashes and all four remote key formats, tool discovery
  and direct-call policy enforcement, path traversal/symlink refusal, artifact/job
  ownership, token expiry and credential-free HTTP logs.
- Persistent operation/artifact recovery across separate MCP frontend processes;
  concurrent submissions of one request ID execute the mutation once, while changed
  arguments with the same ID are rejected.
- A client disconnect during a long Python edit leaves it running and recoverable.
  Queued cancellation prevents execution; running cancellation is cooperative.
- Broker restart during active Python work preserves its result without repetition.
- Application exit records unfinished work as interrupted with unknown outcome.
  Automatic recovery launches a replacement Blender from the completed checkpoint;
  an independent object readback verifies the saved pose and absence of replay.
- Opt-in frontend-exit shutdown preserves a checkpoint and stops its owned application.
- Graceful shutdown disables automatic recovery. A later editing request exercises
  lazy application startup. Bridge restart preserves Blender's process/session.
- Capacity checks reject low-space work before admission and recheck queued work
  before execution. Storage status reflects changing free bytes. Pixel estimates
  scale with resolution and frame count.
- Actual object/material/modifier edits, selection/transforms, save/open, overwrite
  refusal, GLB export/re-import, CPU rendering and MCP image delivery.

## Representative editing evidence

Retained under `/tmp/mb-building-20261101-final/`:

- `evidence.json`: resource/timeline inspection, API requests and independent ffprobe data.
- `library/building.blend`: editable image-textured exterior building and camera animation.
- `library/building-edited.blend`: sequencer timeline with image, title and sound strips.
- `library/building.glb`: exported model with packed image texture.
- `library/walkthrough-frames/`: 12 PNG frames and a manifest, with differing frame hashes.
- `library/walkthrough.mp4`: 384x256, 12-frame H.264 video with an AAC audio stream.

The fixture used structured mesh, material, light, camera and keyframe tools;
packed/reopened the texture; and rendered a changing camera view. A rendered frame
was opened and visually inspected. An earlier equivalent video frame was also
extracted and inspected to verify the VSE title overlay. CPU previews deliberately
use low samples; they are functional evidence, not final-quality artwork.

An authenticated loopback API fixture handled generation submission/status, GLB
and texture downloads, refinement export and upload. The received upload was
checked independently and provider credentials were absent from SQLite/WAL files.
This qualifies the generic adapter against a local fixture, not a diffusion vendor.

## GUI, packaging and skills

`python3 tools/gui_smoke.py /tmp/mb-gui-validation-v02` passed on the current display.
It launched a fresh GUI Blender through MCP, edited an object, saved/reopened the
project and retrieved a 640x480 viewport screenshot through protocol 2. That image
was opened and visually inspected. Session identity survived project reload; the
owned test process and broker were stopped. The retained directory contains the
project, screenshot, JSON evidence and Blender log. Host LLVM shader warnings did
not prevent rendering.

The pure-Python 0.2.0 wheel built without dependency downloads. All three skills
passed the skill-creator validator. The generated add-on bundle contains the worker
and stdlib-only broker dependencies, without FastMCP or Pydantic.

## Limits

Native Blender rendering still occupies its main thread; the broker stays usable.
Native blocking operations are not forcibly cancellable, and arbitrary Python
cannot be assigned a reliable storage estimate. Recovery preserves the completed
checkpoint, not edits after it, and does not imply rollback or power-failure proof.

Shape-key animation, physics bakes, constraints/drivers/active NLA blending, other Blender/platform versions,
long-duration workloads, external HTTPS deployment, Godot behavior and real
external generation services are not qualified by this suite.
