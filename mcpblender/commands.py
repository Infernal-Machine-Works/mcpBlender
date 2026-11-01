"""Validated structured command contracts and per-command capabilities."""
from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from .config import Model

Vec3 = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
Color = Annotated[list[Annotated[FiniteFloat, Field(ge=0, le=1)]], Field(min_length=4, max_length=4)]
Name = Annotated[str, Field(min_length=1, max_length=255)]


class Empty(Model):
    pass


class Primitive(Model):
    name: Name
    kind: Literal["CUBE", "PLANE", "UV_SPHERE", "CYLINDER", "CONE"] = "CUBE"
    location: Vec3 = [0, 0, 0]
    scale: Vec3 = [1, 1, 1]


class Mesh(Model):
    name: Name
    vertices: list[Vec3] = Field(min_length=3, max_length=1_000_000)
    faces: list[Annotated[list[int], Field(min_length=3)]] = Field(min_length=1, max_length=1_000_000)

    @model_validator(mode="after")
    def valid_indices(self):
        if any(index < 0 or index >= len(self.vertices) for face in self.faces for index in face):
            raise ValueError("Mesh face index is outside the vertex array")
        return self


class Material(Model):
    name: Name
    base_color: Color | None = None
    roughness: FiniteFloat | None = Field(default=None, ge=0, le=1)
    metallic: FiniteFloat | None = Field(default=None, ge=0, le=1)
    image_path: str | None = None
    pack_image: bool = True
    texture_scale: Vec3 = [1, 1, 1]


class AssignMaterial(Model):
    objects: list[Name] = Field(min_length=1)
    material: Name
    replace: bool = True


class Light(Model):
    name: Name
    type: Literal["POINT", "SUN", "SPOT", "AREA"] = "AREA"
    energy: FiniteFloat = Field(default=1000, ge=0)
    color: Vec3 = [1, 1, 1]
    location: Vec3 | None = None
    rotation_euler: Vec3 | None = None
    target: Vec3 | None = None
    size: FiniteFloat = Field(default=5, gt=0)


class Camera(Model):
    name: Name
    location: Vec3 | None = None
    rotation_euler: Vec3 | None = None
    target: Vec3 | None = None
    lens: FiniteFloat = Field(default=50, gt=0)
    projection: Literal["PERSP", "ORTHO"] | None = None
    ortho_scale: FiniteFloat | None = Field(default=None, gt=0)
    active: bool = True


class Keyframe(Model):
    frame: int
    value: Vec3


class Keyframes(Model):
    name: Name
    data_path: Literal["location", "rotation_euler", "scale"]
    keyframes: list[Keyframe] = Field(min_length=1, max_length=100_000)
    interpolation: Literal["LINEAR", "BEZIER", "CONSTANT"] = "LINEAR"


class RenderSettings(Model):
    engine: Literal["CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"] | None = None
    resolution_x: int | None = Field(default=None, ge=16, le=4096)
    resolution_y: int | None = Field(default=None, ge=16, le=4096)
    fps: int | None = Field(default=None, ge=1, le=240)
    frame_start: int | None = None
    frame_end: int | None = None
    samples: int | None = Field(default=None, ge=1, le=65536)


class Animation(Model):
    directory: str | None = None
    frame_start: int
    frame_end: int
    frame_step: int = Field(default=1, ge=1)
    width: int = Field(default=640, ge=16, le=4096)
    height: int = Field(default=360, ge=16, le=4096)
    engine: Literal["CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"] | None = None
    camera: str | None = None

    @model_validator(mode="after")
    def bounded_range(self):
        if self.frame_end < self.frame_start or (self.frame_end - self.frame_start) // self.frame_step >= 100_000:
            raise ValueError("Animation range must contain 1 to 100000 frames")
        return self


class Video(Model):
    path: str | None = None
    frame_start: int
    frame_end: int
    width: int = Field(default=640, ge=16, le=4096)
    height: int = Field(default=360, ge=16, le=4096)
    overwrite: bool = False

    @model_validator(mode="after")
    def valid_range(self):
        if self.frame_end < self.frame_start:
            raise ValueError("frame_end precedes frame_start")
        if self.width % 2 or self.height % 2:
            raise ValueError("H.264 video dimensions must be even")
        return self


class Strip(Model):
    name: Name
    kind: Literal["IMAGE", "MOVIE", "SOUND", "TEXT"]
    paths: list[str] = []
    channel: int = Field(default=1, ge=1, le=128)
    frame_start: int = 1
    duration: int | None = Field(default=None, ge=1)
    text: str = ""
    font_size: FiniteFloat = Field(default=36, gt=0)
    color: Color = [1, 1, 1, 1]
    location: Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)] = [0.5, 0.12]

    @model_validator(mode="after")
    def media_required(self):
        if self.kind != "TEXT" and not self.paths:
            raise ValueError("Media strips need at least one file path")
        return self


class EditStrip(Model):
    name: Name
    frame_final_start: int | None = None
    frame_final_end: int | None = None
    channel: int | None = Field(default=None, ge=1, le=128)
    mute: bool | None = None
    blend_alpha: FiniteFloat | None = Field(default=None, ge=0, le=1)
    text: str | None = None


class Named(Model):
    name: Name


class MeshInspection(Named):
    evaluated: bool = True
    sample_limit: int = Field(default=16, ge=0, le=256)


class BevelSettings(Model):
    type: Literal["BEVEL"] = "BEVEL"
    width: FiniteFloat = Field(default=0.1, ge=0)
    segments: int = Field(default=2, ge=1, le=16)
    limit_method: Literal["NONE", "ANGLE", "WEIGHT"] = "ANGLE"
    angle_limit: FiniteFloat = Field(default=0.523599, ge=0, le=3.141593)


class SubdivisionSettings(Model):
    type: Literal["SUBSURF"] = "SUBSURF"
    subdivision_type: Literal["CATMULL_CLARK", "SIMPLE"] = "CATMULL_CLARK"
    levels: int = Field(default=1, ge=0, le=4)
    render_levels: int = Field(default=2, ge=0, le=4)


class DecimateSettings(Model):
    type: Literal["DECIMATE"] = "DECIMATE"
    ratio: FiniteFloat = Field(default=0.5, gt=0, le=1)
    use_collapse_triangulate: bool = False


class SolidifySettings(Model):
    type: Literal["SOLIDIFY"] = "SOLIDIFY"
    thickness: FiniteFloat = 0.01
    offset: FiniteFloat = Field(default=-1, ge=-1, le=1)
    use_even_offset: bool = True


class TriangulateSettings(Model):
    type: Literal["TRIANGULATE"] = "TRIANGULATE"
    quad_method: Literal["BEAUTY", "FIXED", "FIXED_ALTERNATE", "SHORTEST_DIAGONAL", "LONGEST_DIAGONAL"] = "BEAUTY"
    ngon_method: Literal["BEAUTY", "CLIP"] = "BEAUTY"


class Modifier(Named):
    modifier: Name
    settings: Annotated[BevelSettings | SubdivisionSettings | DecimateSettings | SolidifySettings | TriangulateSettings,
                        Field(discriminator="type")]
    show_viewport: bool = True
    show_render: bool = True


class ModifierReference(Named):
    modifier: Name


class ApplyModifier(ModifierReference):
    make_single_user: bool = False


class UVProjection(Named):
    uv_map: Name = "UVMap"
    method: Literal["SMART", "CUBE"] = "SMART"
    overwrite: bool = False
    make_single_user: bool = False
    angle_limit_degrees: FiniteFloat = Field(default=66, gt=0, le=89)
    island_margin: FiniteFloat = Field(default=0.02, ge=0, le=1)
    cube_size: FiniteFloat = Field(default=1, gt=0)


class DuplicateObject(Named):
    new_name: Name
    linked_data: bool = False
    offset: Vec3 = [0, 0, 0]


class ParentObject(Named):
    parent: Name | None = None
    keep_world: bool = True


class ApplyTransforms(Named):
    location: bool = False
    rotation: bool = True
    scale: bool = True
    make_single_user: bool = False

    @model_validator(mode="after")
    def at_least_one_transform(self):
        if not (self.location or self.rotation or self.scale):
            raise ValueError("Choose at least one transform to apply")
        return self


class FrameObjects(Named):
    objects: list[Name] = Field(min_length=1, max_length=1000)
    view: Literal["FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "ISOMETRIC"] = "ISOMETRIC"
    direction: Vec3 | None = None
    margin: FiniteFloat = Field(default=1.15, ge=1, le=10)
    active: bool = True

    @model_validator(mode="after")
    def nonzero_direction(self):
        if self.direction is not None and sum(v * v for v in self.direction) < 1e-12:
            raise ValueError("Camera direction must be nonzero")
        return self


class PreviewSetup(Model):
    objects: list[Name] = Field(min_length=1, max_length=64)
    prefix: str = Field(default="Preview", pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
    view: Literal["FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "ISOMETRIC"] = "ISOMETRIC"
    projection: Literal["PERSP", "ORTHO"] = "PERSP"
    margin: FiniteFloat = Field(default=1.15, ge=1, le=10)
    strength: FiniteFloat = Field(default=1, gt=0, le=100)


class Bone(Model):
    name: Name
    head: Vec3
    tail: Vec3
    parent: Name | None = None
    connected: bool = False
    deform: bool = True
    roll_degrees: FiniteFloat = 0


class Armature(Named):
    bones: list[Bone] = Field(min_length=1, max_length=256)
    location: Vec3 = [0, 0, 0]

    @model_validator(mode="after")
    def valid_skeleton(self):
        by_name = {bone.name: bone for bone in self.bones}
        if len(by_name) != len(self.bones):
            raise ValueError("Bone names must be unique")
        for bone in self.bones:
            if sum((a - b) ** 2 for a, b in zip(bone.head, bone.tail)) < 1e-12:
                raise ValueError("Bones must have nonzero length")
            if bone.parent and bone.parent not in by_name:
                raise ValueError("Bone parent does not exist")
            if bone.connected and (not bone.parent or any(abs(a - b) > 1e-5 for a, b in zip(bone.head, by_name[bone.parent].tail))):
                raise ValueError("Connected bone head must match its parent tail")
            seen, current = set(), bone
            while current:
                if current.name in seen:
                    raise ValueError("Bone parents must not contain cycles")
                seen.add(current.name)
                current = by_name.get(current.parent)
        return self


class VertexInfluences(Model):
    vertex: int = Field(ge=0)
    weights: dict[Name, Annotated[FiniteFloat, Field(gt=0, le=1)]] = Field(min_length=1, max_length=32)


class SkinBinding(Named):
    armature: Name
    method: Literal["AUTO", "RIGID", "EXPLICIT"] = "AUTO"
    bone: Name | None = None
    weights: list[VertexInfluences] = Field(default_factory=list, max_length=1_000_000)
    normalize: bool = True
    require_complete: bool = True
    make_single_user: bool = False

    @model_validator(mode="after")
    def method_arguments(self):
        if (self.method == "RIGID") != (self.bone is not None):
            raise ValueError("Supply bone only for RIGID binding")
        if (self.method == "EXPLICIT") != bool(self.weights):
            raise ValueError("Supply weights only for EXPLICIT binding")
        if len({entry.vertex for entry in self.weights}) != len(self.weights):
            raise ValueError("Explicit vertex entries must be unique")
        return self


class RigInspection(Named):
    meshes: list[Name] | None = Field(default=None, max_length=32)
    bone_offset: int = Field(default=0, ge=0)
    bone_limit: int = Field(default=200, ge=1, le=1000)
    sample_limit: int = Field(default=16, ge=0, le=256)


class PoseTrack(Model):
    bone: Name
    property: Literal["location", "rotation_degrees", "scale"] = "rotation_degrees"
    keyframes: list[Keyframe] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def unique_frames(self):
        if len({k.frame for k in self.keyframes}) != len(self.keyframes):
            raise ValueError("Track keyframe frames must be unique")
        return self


class PoseKeyframes(Named):
    clip: Name
    tracks: list[PoseTrack] = Field(min_length=1, max_length=1000)
    interpolation: Literal["LINEAR", "BEZIER", "CONSTANT"] = "LINEAR"

    @model_validator(mode="after")
    def bounded_tracks(self):
        if len({(t.bone, t.property) for t in self.tracks}) != len(self.tracks):
            raise ValueError("Bone/property tracks must be unique")
        if sum(len(t.keyframes) for t in self.tracks) > 100_000:
            raise ValueError("Clip update is limited to 100000 vector keyframes")
        return self


class AnimationClip(PoseKeyframes):
    activate: bool = True


class ActiveClip(Named):
    clip: Name | None = None
    slot: Name | None = None


class AnimationInspection(Named):
    clip: Name | None = None
    slot: Name | None = None
    clip_offset: int = Field(default=0, ge=0)
    clip_limit: int = Field(default=50, ge=1, le=200)
    track_limit: int = Field(default=100, ge=1, le=1000)


class AnimationSampling(ActiveClip):
    frames: list[int] = Field(min_length=1, max_length=64)
    meshes: list[Name] = Field(default_factory=list, max_length=8)
    bones: list[Name] | None = Field(default=None, max_length=32)
    sample_limit: int = Field(default=16, ge=0, le=64)


EDIT = ("allow_scene_edit",)
SNAPSHOT = ("allow_snapshots",)
# model, capability flags, tool description
COMMANDS = {
    "setup_preview_stage": (PreviewSetup, EDIT, "Create a fresh asset-relative three-area-light setup and fitted active camera. Refuses existing resource names; keeps the stage for later renders. Does not alter render settings or existing lights."),
    "create_animation_clip": (AnimationClip, EDIT, "Create a persistent named skeletal action from typed pose tracks. Bone rotations are XYZ Euler degrees in bone-local rest coordinates. Rejects existing clip names; optionally activate."),
    "set_pose_keyframes": (PoseKeyframes, EDIT, "Insert/replace specified bone vector keyframes in a clip created for this rig. Existing other tracks/frames remain; touched keys use the requested interpolation."),
    "set_active_clip": (ActiveClip, EDIT, "Activate a compatible skeletal action, or clear the action and reset pose if clip is omitted. Resets unkeyed pose channels to rest to prevent clip leakage; NLA/drivers/constraints unsupported."),
    "inspect_animation": (AnimationInspection, (), "List paginated actions/slots and inspect a selected or active clip's tracks, key counts, range and target compatibility without changing pose."),
    "sample_animation": (AnimationSampling, (), "Sample bone endpoints and evaluated mesh geometry at up to 64 frames, optionally in a selected clip. Restores frame, action, slot and pose even on cancellation; temporary evaluation can mark Blender dirty."),
    "create_armature": (Armature, EDIT, "Create a named skeleton from bone head/tail positions in armature-local coordinates, optional parents, connected joints and roll in degrees. Preserves selection."),
    "bind_mesh": (SkinBinding, EDIT, "Bind an unparented mesh to an armature in rest pose using AUTO weights, one RIGID bone, or EXPLICIT per-vertex weights. Refuses existing skin bindings/group conflicts; reports weight coverage."),
    "inspect_rig": (RigInspection, (), "Inspect paginated rest/posed bones and skin weights, including unweighted vertices, weight sums and sampled influences. Uses the current frame without editing the scene."),
    "frame_objects": (FrameObjects, EDIT, "Fit an existing perspective/orthographic camera named name to evaluated mesh bounds at the current render aspect ratio. Choose a view preset or direction from asset toward camera. Reports normalized projection bounds; may change clip distances."),
    "duplicate_object": (DuplicateObject, EDIT, "Duplicate a named object into the current collection, with independent mesh/data by default and optional world-space offset. Parent, constraints and animation action references are retained."),
    "set_parent": (ParentObject, EDIT, "Parent an object, or clear its parent when parent is omitted. Preserve world placement by default; rejects cycles and animated/constrained child transforms."),
    "apply_transforms": (ApplyTransforms, EDIT, "Bake chosen static mesh transforms into geometry, preserving child world placement. Rotation and scale default true. Shared meshes require make_single_user=true."),
    "generate_uv": (UVProjection, EDIT, "Project every face into a named UV map using SMART or CUBE projection. Existing maps require overwrite=true; shared mesh edits require make_single_user=true. Angles are degrees."),
    "inspect_mesh": (MeshInspection, (), "Inspect base or evaluated mesh topology, UVs, world bounds and sampled vertices at the current frame; does not change the scene."),
    "configure_modifier": (Modifier, EDIT, "Create/update a named bevel, subdivision, decimation, solidify or triangulation modifier using typed settings. Existing modifier type must match."),
    "apply_modifier": (ApplyModifier, EDIT, "Destructively apply a named modifier. Shared mesh data requires make_single_user=true. Object mode required."),
    "remove_modifier": (ModifierReference, EDIT, "Remove a named modifier without applying it."),
    "get_capabilities": (Empty, (), "Discover Blender domains, structured commands, engines and video codec support."),
    "inspect_resources": (Empty, (), "Inspect materials, shader nodes, packed textures, actions, render settings and VSE strips."),
    "create_primitive": (Primitive, EDIT, "Create a named mesh primitive with location and scale."),
    "create_mesh": (Mesh, EDIT, "Create a named mesh from explicit vertices and polygon faces."),
    "configure_material": (Material, EDIT, "Create/update a Principled shader and optionally connect and pack an image texture."),
    "assign_material": (AssignMaterial, EDIT, "Assign a material to named objects, optionally replacing existing slots."),
    "configure_light": (Light, EDIT, "Create/update point, sun, spot or area lighting, including energy, color and aim."),
    "configure_camera": (Camera, EDIT, "Create/update a camera, lens and aim, optionally making it active."),
    "set_keyframes": (Keyframes, EDIT, "Insert transform animation keys with explicit interpolation."),
    "configure_render": (RenderSettings, EDIT, "Configure renderer, resolution, samples, frame range and FPS."),
    "render_animation": (Animation, SNAPSHOT, "Submit a tracked PNG frame sequence with per-frame progress and cancellation between frames."),
    "render_video": (Video, SNAPSHOT, "Submit a Blender VSE timeline render to H.264 MP4; progress remains available while encoding."),
    "vse_add_strip": (Strip, EDIT, "Add an image sequence, movie, sound or text strip to Blender's video sequencer."),
    "vse_edit_strip": (EditStrip, EDIT, "Edit VSE strip timing, channel, mute, opacity or text."),
    "vse_remove_strip": (Named, EDIT, "Remove a named VSE strip."),
    "vse_list": (Empty, (), "Inspect Blender's video editing timeline and strip ranges."),
}

DOMAIN_COMMANDS = set(COMMANDS)


class Code(Model):
    code: str = Field(max_length=7_000_000)


class File(Model):
    path: str


class Save(File):
    overwrite: bool = False


class Export(Save):
    selected_only: bool = True
    animations: bool = True


class Still(Model):
    path: str | None = None
    width: int = Field(default=800, ge=16, le=4096)
    height: int = Field(default=600, ge=16, le=4096)
    engine: Literal["CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"] | None = None
    camera: str | None = None


class SceneQuery(Model):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class Selection(Model):
    names: list[Name]


class Transform(Named):
    location: Vec3 | None = None
    rotation_euler: Vec3 | None = None
    scale: Vec3 | None = None


class Frame(Model):
    frame: int


class Download(Model):
    source: Name
    resource: str
    path: str
    expected_sha256: Annotated[str, Field(pattern="^[0-9a-fA-F]{64}$")] | None = None
    overwrite: bool = False


class Upload(Model):
    source: Name
    resource: str
    path: str
    method: Literal["PUT", "POST"] = "PUT"


class APIRequest(Model):
    source: Name
    resource: str
    method: Literal["GET", "POST"] = "GET"
    payload: dict | None = None


class Shutdown(Model):
    save_path: str | None = None
    overwrite: bool = False
    discard_unsaved: bool = False


COMMANDS.update({
    "execute_code": (Code, ("allow_code_execution", "allow_scene_edit"), "Execute arbitrary bpy Python."),
    "get_scene_info": (SceneQuery, (), "Inspect scene objects."),
    "get_object_info": (Named, (), "Inspect one object."),
    "select_objects": (Selection, EDIT, "Set object selection."),
    "set_object_transform": (Transform, EDIT, "Set local object transforms."),
    "set_frame": (Frame, EDIT, "Evaluate an animation frame."),
    "open_blend": (File, EDIT, "Open a project."),
    "save_blend": (Save, ("allow_library_write",), "Save a project."),
    "import_asset": (File, EDIT, "Import a local asset."),
    "export_glb": (Export, ("allow_library_write",), "Export a GLB asset."),
    "render_still": (Still, SNAPSHOT, "Render a camera image."),
    "capture_viewport": (Still, SNAPSHOT, "Capture the GUI viewport."),
    "download_asset": (Download, ("allow_external_assets", "allow_library_write"), "Download an API asset with optional SHA-256 verification; then import_asset or configure_material."),
    "upload_asset": (Upload, ("allow_external_assets",), "Upload a local asset to a configured API source using PUT or POST."),
    "request_asset_api": (APIRequest, ("allow_external_assets",), "Call a configured JSON API for generation submission or polling; credentials stay outside the journal."),
    "checkpoint": (Empty, ("allow_library_write",), "Save a recovery copy without changing the working project path."),
    "shutdown_blender": (Shutdown, ("allow_shutdown_blender",), "Shut down Blender gracefully; save_path or explicit discard_unsaved is required for dirty projects. Stops automatic restart."),
})
DOMAIN_COMMANDS.update({"download_asset", "upload_asset", "request_asset_api", "checkpoint", "shutdown_blender"})

from .morph_commands import COMMANDS as MORPH_COMMANDS

COMMANDS.update(MORPH_COMMANDS)
DOMAIN_COMMANDS.update(MORPH_COMMANDS)

from .action_contracts import COMMANDS as ACTION_COMMANDS

COMMANDS.update(ACTION_COMMANDS)
