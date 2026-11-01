"""Typed shader editing without arbitrary RNA paths or executable nodes."""
from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from .config import Model

Name = Annotated[str, Field(min_length=1, max_length=255)]
Socket = int | Name
Value = FiniteFloat | bool | Annotated[list[FiniteFloat], Field(min_length=3, max_length=4)]
Color = Annotated[list[Annotated[FiniteFloat, Field(ge=0, le=1)]], Field(min_length=4, max_length=4)]
NODE_TYPES = (
    "ShaderNodeBsdfPrincipled", "ShaderNodeBsdfDiffuse", "ShaderNodeBsdfGlass",
    "ShaderNodeEmission", "ShaderNodeMixShader", "ShaderNodeAddShader", "ShaderNodeOutputMaterial",
    "ShaderNodeTexImage", "ShaderNodeTexNoise", "ShaderNodeTexVoronoi", "ShaderNodeTexChecker",
    "ShaderNodeTexCoord", "ShaderNodeMapping", "ShaderNodeMath", "ShaderNodeVectorMath",
    "ShaderNodeMixRGB", "ShaderNodeNormalMap", "ShaderNodeBump", "ShaderNodeRGB",
    "ShaderNodeValue", "ShaderNodeSeparateColor", "ShaderNodeCombineColor", "ShaderNodeMapRange",
    "ShaderNodeValToRGB",
)


class SocketValue(Model):
    socket: Socket
    value: Value


class RampStop(Model):
    position: FiniteFloat = Field(ge=0, le=1)
    color: Color


class NodeProperties(Model):
    operation: Name | None = None
    blend_type: Name | None = None
    noise_dimensions: Literal["1D", "2D", "3D", "4D"] | None = None
    voronoi_dimensions: Literal["1D", "2D", "3D", "4D"] | None = None
    interpolation_type: Name | None = None
    normalize: bool | None = None
    interpolation: Name | None = None
    extension: Literal["REPEAT", "EXTEND", "CLIP", "MIRROR"] | None = None
    projection: Literal["FLAT", "BOX", "SPHERE", "TUBE"] | None = None
    vector_type: Literal["POINT", "TEXTURE", "VECTOR", "NORMAL"] | None = None
    space: Name | None = None
    uv_map: Name | None = None
    mode: Name | None = None


class ShaderNode(Model):
    name: Name
    type: Literal[NODE_TYPES]
    label: str = Field(default="", max_length=255)
    properties: NodeProperties = Field(default_factory=NodeProperties)
    inputs: list[SocketValue] = Field(default_factory=list, max_length=64)
    output_value: Value | None = None
    image_path: str | None = None
    color_space: Literal["sRGB", "Non-Color"] = "sRGB"
    pack_image: bool = True
    ramp: list[RampStop] | None = Field(default=None, min_length=2, max_length=32)

    @model_validator(mode="after")
    def valid_options(self):
        if self.image_path and self.type != "ShaderNodeTexImage":
            raise ValueError("image_path requires ShaderNodeTexImage")
        if self.ramp and self.type != "ShaderNodeValToRGB":
            raise ValueError("ramp requires ShaderNodeValToRGB")
        if self.output_value is not None and self.type not in {"ShaderNodeRGB", "ShaderNodeValue"}:
            raise ValueError("output_value requires an RGB or Value node")
        return self


class ShaderLink(Model):
    from_node: Name
    from_socket: Socket
    to_node: Name
    to_socket: Socket


class MaterialName(Model):
    material: Name


class ShaderGraph(MaterialName):
    nodes: list[ShaderNode] = Field(min_length=1, max_length=128)
    links: list[ShaderLink] = Field(default_factory=list, max_length=256)
    replace: bool = False

    @model_validator(mode="after")
    def unique_nodes(self):
        if len({n.name for n in self.nodes}) != len(self.nodes):
            raise ValueError("Shader node names must be unique")
        return self


class ConfigureNode(MaterialName):
    node: ShaderNode


class ConnectNodes(MaterialName):
    links: list[ShaderLink] = Field(min_length=1, max_length=256)
    replace_existing: bool = False


class ShaderInput(MaterialName):
    node: Name
    socket: Socket


class RemoveNode(MaterialName):
    node: Name


class ShaderCatalog(Model):
    node_type: Literal[NODE_TYPES] | None = None


class MaterialPreset(Model):
    material: Name
    preset: Literal["PBR", "TEXTURED_PBR", "GLASS", "EMISSION"] = "PBR"
    replace: bool = False
    base_color: Color = [.8, .8, .8, 1]
    roughness: FiniteFloat = Field(default=.5, ge=0, le=1)
    metallic: FiniteFloat = Field(default=0, ge=0, le=1)
    ior: FiniteFloat = Field(default=1.45, ge=1, le=4)
    emission_color: Color = [1, 1, 1, 1]
    emission_strength: FiniteFloat = Field(default=1, ge=0, le=100_000)
    base_color_texture: str | None = None
    roughness_texture: str | None = None
    metallic_texture: str | None = None
    normal_texture: str | None = None
    normal_strength: FiniteFloat = Field(default=1, ge=0, le=10)
    texture_scale: Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)] = [1, 1, 1]
    pack_images: bool = True

    @model_validator(mode="after")
    def valid_textures(self):
        textures = any((self.base_color_texture, self.roughness_texture, self.metallic_texture, self.normal_texture))
        if self.preset == "TEXTURED_PBR" and not textures:
            raise ValueError("TEXTURED_PBR requires at least one texture")
        if self.preset in {"GLASS", "EMISSION"} and textures:
            raise ValueError("Texture maps are supported by the PBR presets")
        return self


EDIT = ("allow_scene_edit",)
COMMANDS = {
    "shader_node_catalog": (ShaderCatalog, (), "Discover supported node types, properties, socket identifiers/indexes and defaults from installed Blender. Specify node_type for details."),
    "inspect_shader_graph": (MaterialName, (), "Inspect shader nodes, socket values, textures and links, including imported node types."),
    "configure_shader_graph": (ShaderGraph, EDIT, "Create a material shader graph from typed nodes/links. Existing materials require replace=true. Preflights on a temporary material before editing."),
    "configure_shader_node": (ConfigureNode, EDIT, "Create/update one named shader node without clearing unrelated nodes or links. Existing type must match; omitted inputs/properties remain."),
    "connect_shader_nodes": (ConnectNodes, EDIT, "Connect named shader sockets; use identifiers or indexes for ambiguous socket names. Existing input links require replace_existing=true."),
    "disconnect_shader_input": (ShaderInput, EDIT, "Remove incoming links from a particular material node input."),
    "remove_shader_node": (RemoveNode, EDIT, "Remove a named material node and its links."),
    "apply_material_preset": (MaterialPreset, EDIT, "Create PBR, TEXTURED_PBR, GLASS or EMISSION material graphs. PBR accepts packed base-color/roughness/metallic/normal maps. Existing material requires replace=true."),
}
