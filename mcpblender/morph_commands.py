"""Typed relative shape-key and morph-animation contracts."""
from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from .config import Model

Name = Annotated[str, Field(min_length=1, max_length=255)]
KeyName = Annotated[str, Field(min_length=1, max_length=63)]
Vec3 = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
Weight = Annotated[FiniteFloat, Field(ge=0, le=1)]
Digest = Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
Frame = Annotated[int, Field(ge=-1048574, le=1048574)]


class Target(Model):
    name: Name


class ShapeVertex(Model):
    vertex: int = Field(ge=0)
    value: Vec3


class NewShape(Target):
    key: KeyName
    vertices: list[ShapeVertex] = Field(default_factory=list, max_length=1_000_000)
    mode: Literal["OFFSET", "POSITION"] = "OFFSET"
    source_object: Name | None = None
    expected_topology: Digest | None = None
    make_single_user: bool = False

    @model_validator(mode="after")
    def coordinate_source(self):
        if self.source_object and self.vertices:
            raise ValueError("Choose source_object or vertices, not both")
        if len({v.vertex for v in self.vertices}) != len(self.vertices):
            raise ValueError("Vertex indices must be unique")
        if len(self.key.encode("utf-8")) > 63:
            raise ValueError("Shape-key name must fit in 63 UTF-8 bytes")
        return self


class EditShape(NewShape):
    reset_unspecified: bool = False

    @model_validator(mode="after")
    def needs_coordinates(self):
        if not (self.source_object or self.vertices or self.reset_unspecified):
            raise ValueError("Supply coordinates or reset_unspecified=true")
        return self


class ShapeInspection(Target):
    key: KeyName | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=200)
    sample_limit: int = Field(default=16, ge=0, le=256)


class ShapeWeights(Target):
    values: dict[KeyName, Weight] = Field(min_length=1, max_length=256)
    reset_others: bool = False
    make_single_user: bool = False


class RemoveShape(Target):
    key: KeyName
    make_single_user: bool = False


class MorphKeyframe(Model):
    frame: Frame
    value: Weight


class MorphTrack(Model):
    key: KeyName
    keyframes: list[MorphKeyframe] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def unique_frames(self):
        if len({k.frame for k in self.keyframes}) != len(self.keyframes):
            raise ValueError("Track frames must be unique")
        return self


class MorphKeyframes(Target):
    clip: Name
    tracks: list[MorphTrack] = Field(min_length=1, max_length=256)
    interpolation: Literal["LINEAR", "BEZIER", "CONSTANT"] = "LINEAR"

    @model_validator(mode="after")
    def bounded_tracks(self):
        if len({t.key for t in self.tracks}) != len(self.tracks):
            raise ValueError("Shape-key tracks must be unique")
        if sum(len(t.keyframes) for t in self.tracks) > 100_000:
            raise ValueError("Clip update is limited to 100000 scalar keyframes")
        return self


class MorphClip(MorphKeyframes):
    activate: bool = True
    join_existing: bool = False


class MorphActive(Target):
    clip: Name | None = None
    slot: Name | None = None


class MorphInspection(MorphActive):
    clip_offset: int = Field(default=0, ge=0)
    clip_limit: int = Field(default=50, ge=1, le=200)
    track_limit: int = Field(default=100, ge=1, le=1000)


class MorphSampling(MorphActive):
    frames: list[Frame] = Field(min_length=1, max_length=64)
    sample_limit: int = Field(default=16, ge=0, le=64)
    armature: Name | None = None
    skeletal_clip: Name | None = None
    skeletal_slot: Name | None = None

    @model_validator(mode="after")
    def paired_skeleton(self):
        if (self.skeletal_clip or self.skeletal_slot) and not self.armature:
            raise ValueError("Supply armature when choosing a skeletal clip/slot")
        return self


EDIT = ("allow_scene_edit",)
COMMANDS = {
    "create_shape_key": (NewShape, EDIT, "Create a named relative shape key from Basis, sparse vertex OFFSETs/POSITIONs, or a same-topology source_object in mesh-local coordinates. Basis is created automatically; shared meshes require explicit isolation."),
    "edit_shape_key": (EditShape, EDIT, "Edit a non-Basis key using Basis-relative offsets or absolute mesh-local positions. Unspecified vertices remain unless reset_unspecified=true. Checks vertex indices, stored topology and optional expected_topology."),
    "inspect_shape_keys": (ShapeInspection, (), "Inspect paginated relative/absolute keys, weights, topology fingerprint, coordinate digests, deltas and bounded vertex samples without editing."),
    "set_shape_key_weights": (ShapeWeights, EDIT, "Set static named morph values in [0,1], optionally resetting other values. Clear the active morph clip first; drivers/active NLA unsupported."),
    "remove_shape_key": (RemoveShape, EDIT, "Remove one non-Basis key. Refuses relative-key dependencies and animation references, including inactive managed clips."),
    "create_morph_clip": (MorphClip, EDIT, "Create a persistent named morph action with scalar keyframe tracks. join_existing=true explicitly adds a KEY slot to a compatible managed action, including a bound rig's skeletal clip for combined GLB animation."),
    "set_morph_keyframes": (MorphKeyframes, EDIT, "Insert/replace specified scalar keys in this mesh's managed morph slot; preserves other action slots and untouched frames."),
    "set_active_morph_clip": (MorphActive, EDIT, "Activate a compatible morph action/KEY slot, resetting unkeyed weights to zero; omit clip to clear/reset. Supports muted import stashes, not active NLA blending."),
    "inspect_morph_animation": (MorphInspection, (), "List actions/slots and inspect a selected or active morph slot's tracks and key counts."),
    "sample_morph_animation": (MorphSampling, (), "Sample evaluated morph geometry at up to 64 frames, optionally with an explicit armature/skeletal_clip. Restores weights, actions, slots, rig pose and frame even on cancellation; temporary evaluation can mark Blender dirty."),
}
