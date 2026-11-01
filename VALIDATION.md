# Local validation: 0.2.0

## Structured shaders and material presets

On 2026-11-01, `MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q
tests/test_shader_contracts.py tests/test_shaders.py --tb=short` passed **2 tests in
15.95 seconds**. Unit checks reject executable nodes, nonfinite values, duplicate
names, path escape and read-only edits without starting Blender. Real Blender
checks run with Python execution disabled: catalog discovery, procedural noise/ramp
graphs, invalid replacement with the original graph preserved, incremental node
and link edits, connection overwrite refusal, and all four material presets.
The textured PBR preset packs all four maps with appropriate color spaces, renders
a checker-painted sphere, survives save/reopen, and exports GLB with base-color,
metallic/roughness and normal texture references verified by JSON parsing. The
render was opened and visually inspected. Evidence: `/tmp/mb-shaders-20261101/`.
This qualifies the installed Blender 5.0.1 surface, not arbitrary imported node
groups, animated shader graphs, every supported node combination or Godot shaders.

## Latest complete regression

On 2026-11-01, after the installation-driven recovery and splash changes:

```sh
MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q --tb=short --maxfail=1
```

**85 tests passed in 543.02 seconds**, with the existing host Starlette/AnyIO
deprecation warning. This run includes the new startup/recovery contention
regressions and the earlier modeling, shader, animation, video, retention and
interruption checks. The installed service was independently exercised as below.

## Permanent installation and recovery qualification

On 2026-11-01, the standard sdist and wheel build passed with dependency checks
enabled in a private Python environment. Installed package imports were checked
outside the checkout, declared runtime constraints passed, and every installed
Python module was compared with current source. The environment reuses existing
host dependencies; no host dependency upgrades or Blender rebuild were performed.

The authenticated loopback FreeBSD service is boot-enabled and supervised.
Missing/invalid keys returned 401. Separate Codex and PQC credentials discovered
93 tools; PQC's native HTTP transport queried the shared recovered asset and found
all three adapted skills. Its configuration validates after backing up and aligning
stale sample agent model overrides with the instance's already selected models.
Installed stdio attached to the same GUI/session. Private configurations, keys,
backups, host service settings and evidence remain outside Git.

Through the installed HTTP endpoint, structured tools created a UV-mapped textured
asset and preview stage, saved a project, exported GLB, rendered a Cycles image and
captured a viewport. The rendered image was visually inspected. Service restart
and supervised frontend exit preserved Blender's PID/session. Broker restart
preserved Blender and operation history. Killing the owned test application
restored its checkpoint and asset in a new session without replaying the completed
render. Evidence is retained privately in the model library.

This installed crash test exposed a duplicate-launch race. The broker supervisor
and frontends now share a nonblocking launch lock, and a frontend waits for an
already-starting recovery PID. Ten focused broker/operation tests passed, including
deterministic contention and pending-worker coverage. The real Blender recovery
test now immediately queries during recovery rather than waiting for registration;
it passed in 31.55 seconds. A fresh installed GUI confirmed session-only splash
suppression. The earlier first launch had its welcome dialog dismissed manually.

Actual reboot/login access, longer-duration service operation, log rotation,
external HTTPS ingress and Godot integration remain unqualified. Godot is paused.

## Shader and retention regression

On 2026-11-01, FreeBSD Blender 5.0.1:

```sh
MCPBLENDER_TEST_BLENDER=1 MCPBLENDER_SHADER_EVIDENCE_DIR=/tmp/mb-shaders-20261101 python3 -m pytest -q --tb=short --maxfail=1
```

**83 tests passed in 334.08 seconds**, with the existing host Starlette/AnyIO
deprecation warning. This includes named recipes, all three built-in workflows,
typed permission/path preflight, real asset/animation delivery, cancellation and
late-failure restoration, broker restart, application interruption and checkpoint
recovery without replay, structured shader authoring/PBR delivery, and scoped
temporary-output/history retention. The earlier startup handshake failure described below
was corrected and this complete run passed. The final wheel and add-on archive
were checked for current workflow/recovery code; the setuptools build-environment
limitation below applied at the time of that run. Permanent installation was
qualified separately afterward, as described above.

## Retention controls

The complete run covers temporary-output tracking, explicit deliverable protection,
pins and structured-input protection, active/unknown operation protection, age and
size thresholds, bounded scans, modified-file refusal and final/ancestor symlink
replacement. Registration errors leave no queued scene edit. History compaction
preserves request-ID/digest tombstones; both renders and scene edits remain
deduplicated after their details are pruned. The real MCP test cleans a generated
Cycles render without replaying it, and verifies pin/unpin and library-write policy.
After adding paginated output-group reporting, the focused retention suite passed
**9 tests in 9.71 seconds**, including a fresh real MCP/Blender run.

Cleanup is limited to new registered generated outputs. It does not sweep legacy
files, delete explicit output paths, vacuum databases, rotate logs or infer external
Python/editor dependencies. Unknown filenames inside frame directories remain.
Automatic maintenance is opportunistic during submissions; age tests use controlled
local fixtures, not a seven-day soak. Physical reclamation depends on the filesystem.
The wheel (dependency-check-skipped due to the host setuptools version noted below)
and add-on archive were checked byte-for-byte for current shader/retention workers.
All three updated bundled skills passed validation; no permanent installation or
client configuration was performed.

## Built-in preview setup

On 2026-11-01, `MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q
tests/test_actions.py tests/test_action_integration.py --tb=short` passed **14 tests
in 26.75 seconds**. Separate action/server checks passed 18 tests. With Python
execution disabled, Blender created an offset painted asset, rejected a missing
target without adding objects, fitted perspective and orthographic cameras,
refused a repeated stage prefix without changing objects, and rendered a Cycles
preview. The retained `/tmp/mb-actions-resume/stage.png` was opened and visually
inspected. Existing scene objects and lights remain visible by design; this is
stage setup, not scene isolation or final-quality lighting.

## Game-asset preparation

The action checks expanded to **16 tests passed in 34.94 seconds** on 2026-11-01.
Real Blender checks verify all-target preflight before transforms, explicit UV and
triangulation edits, retained world geometry, a saved project, and an explicit-object
GLB with an embedded texture checked by parsing its JSON chunk. Selection is
restored after export. Existing UVs are refused without overwrite consent. A
separate read-only frontend runs inspection and budget checks without a recovery
checkpoint. Unit checks cover optional-step permission denial, path escape and
duplicate targets. No target-engine compatibility claim follows from these checks.

## Animation-preview workflow

The dedicated real Blender workflow test passed on 2026-11-01 in **22.64 seconds**,
with Python execution disabled. It creates a textured skinned/morph asset,
temporarily selects both slots of a combined clip, and renders five changing PNG
frames plus an H.264 video. Independent ffprobe checks confirm five frames at
12 fps. Exact inspected geometry, live clips, frame, objects, render settings and
user VSE strips are restored after success, running cancellation and a deliberately
introduced encoding-output conflict. Partial-frame artifacts remain available on
failure/cancellation. Save/reopen preserves geometry, clips, camera/timeline and
the packed texture. Parsing explicit-object GLB confirms bone and weight channels
and the embedded texture. First/last PNGs and an extracted encoded video frame were
opened and visually inspected. Evidence: `/tmp/mb-actions-resume/`.

The complete action group (`tests/test_actions.py tests/test_action_integration.py`)
passed **18 tests in 77.73 seconds**. The first full regression attempt passed 62
other tests but had five shared-fixture errors: the initial broker status handshake
timed out before Blender launched. The action group passed when rerun; the broker
startup retry was subsequently corrected and qualified in the complete run above.
That earlier attempt was not a full-suite pass.

The add-on archive contains the workflow worker without frontend dependencies.
A standard `python3 -m build --wheel --no-isolation` was blocked by the host's
setuptools 63.1.0 (the project declares >=68). An artifact sanity build using
`--skip-dependency-check` succeeded and its workflow modules were checked. This
does not qualify the declared build environment; no packages were installed or
upgraded. All three bundled skills passed the skill-creator validator.

## Named-action engine milestone

### Interruption and startup checks

Five focused checks passed in **29.90 seconds** on 2026-11-01. Startup unit checks
cover read-only status timeout/disconnect retry with one broker spawn and immediate
propagation of unrelated protocol/policy errors. Real Blender checks restart the
broker during a 64-step editing action, then verify all 64 unique objects, the same
Blender session, and request-ID deduplication. A separate action is interrupted by
killing its owned Blender process after completed edits. Its unknown outcome is
recorded; explicit application restart restores the completed checkpoint, and
resubmitting its request ID does not replay the interrupted action. These are
bounded local interruption checks, not power-failure or long-duration proof.

On 2026-11-01, the focused engine and shared-infrastructure checks passed:

```sh
MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q tests/test_actions.py tests/test_action_integration.py tests/test_server.py tests/test_operations.py tests/test_storage.py tests/test_wire.py --tb=short
```

**30 tests passed in 21.95 seconds**, with the existing host Starlette/AnyIO
deprecation warning. Tests cover strict recipe inputs, forbidden commands and
templates, permission filtering and preflight, library-path confinement, stable
generated paths, storage estimates and configuration-relative recipe files.
Real Blender tests use Python execution disabled and verify successful serial
editing/inspection, explicit partial failure with retained edits, frontend reconnect
and request-ID deduplication, and running cancellation between completed steps.
Owned test Blender and broker processes are stopped by fixture cleanup.

This is a focused check of the current changes. The complete regression below
predates the engine. Built-in workflow recipes, specialized server-side shims,
action-specific broker-restart/crash tests and representative named-action asset
delivery remain pending. Actions provide partial reports, not transactional rollback
or step-by-step recovery. No permanent installation was performed.

## Latest complete regression (before the action engine)

On 2026-11-01, FreeBSD Blender 5.0.1:

```sh
MCPBLENDER_TEST_BLENDER=1 MCPBLENDER_MORPH_EVIDENCE_DIR=/tmp/mb-morph-20261101 python3 -m pytest -q
```

**49 tests passed in 247.29 seconds**, with the existing host Starlette/AnyIO
deprecation warning. The wheel built without dependency downloads and contains
the morph contracts and worker. The add-on bundle includes the morph worker
without frontend dependencies. Updated animation/design skills passed their
validators. Blender was not rebuilt; no permanent deployment or client/skill
installation was performed.

## Shape-key additions

`MCPBLENDER_TEST_BLENDER=1 python3 -m pytest -q tests/test_morph_contracts.py tests/test_shape_keys.py`
passed **4 tests** on the installed Blender 5.0.1 with Python execution disabled.
The tests checked sparse Basis-relative/absolute coordinate edits, half-weight
evaluated positions, non-accumulating offsets, reset/delete, named-key pagination,
source topology refusal, shared-data isolation and save/reopen. Invalid indices,
stale fingerprints, nonfinite input and read-only editing were refused. New key
weights are explicitly zeroed because this Blender build initializes new keys at one.

Named morph animation and delivery checks subsequently covered:

- Scalar keyframe insertion/update and intermediate fractional weights, checked
  against independently calculated mesh bounds; switching resets unkeyed values.
- Temporary sampling of another morph clip, with exact restoration of geometry,
  frame, weights and action. Running combined sampling was cancelled on a detailed
  mesh; both morph and skeletal states were restored with the original digest.
- A shared action with separate OBJECT/KEY slots. Editing bone keys preserved the
  morph slot and editing morph keys preserved bone channels. Joining an unrelated
  skeletal action and deleting an animated shape were refused.
- A textured segmented limb with combined skeletal/morph motion. Five consecutive
  frames were rendered and encoded at 192x192, 12 FPS. Independent ffprobe verified
  H.264 and five frames; the first and fully flared/bent PNGs were opened and viewed.
  The video covers the first five frames of the nine-frame source clip.
- Save/reopen with matching sampled geometry digests. GLB inspection verified
  named combined and morph-only clips, rotation/weight channels, a named POSITION
  morph target, skin attributes, UVs and embedded texture. Both clips were
  re-imported and sampled at frames 1, 3, 5 and 9: weights matched within 0.00001
  and evaluated world bounds within 0.0001 Blender units. Export restored the live
  state and removed its temporary tracks.
- Morph-only export with no rig, an inactive managed action and static weight
  0.25. The file retained that default weight and its animation; source state
  remained unchanged, and re-imported sampled weights matched the source.

Evidence is retained under `/tmp/mb-morph-20261101/`: `morph-animation.blend`,
`morph-animation.glb`, `morph-solo.glb`, `morph-evidence.json`, `morph-preview/` and
`morph-preview.mp4`. All these workflows ran with Python execution disabled.
Qualification covers small relative Basis-based shapes, including one morph on
a two-bone skinned mesh. It does not establish general facial-rig, corrective
driver, absolute-shape-key or target-engine correctness.

## Skeletal-stage complete regression

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

Absolute shape-key animation, physics bakes, constraints/drivers/active NLA blending, other Blender/platform versions,
long-duration workloads, external HTTPS deployment, Godot behavior and real
external generation services are not qualified by this suite.
