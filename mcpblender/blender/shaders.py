"""Structured shader graphs and trusted material presets (bpy + stdlib only)."""
import math
from pathlib import Path

import bpy

COMMANDS = {"shader_node_catalog", "inspect_shader_graph", "configure_shader_graph",
            "configure_shader_node", "connect_shader_nodes", "disconnect_shader_input",
            "remove_shader_node", "apply_material_preset"}
PROPERTIES = {
    "ShaderNodeMath": ("operation",), "ShaderNodeVectorMath": ("operation",),
    "ShaderNodeMixRGB": ("blend_type",), "ShaderNodeTexNoise": ("noise_dimensions", "normalize"),
    "ShaderNodeTexVoronoi": ("voronoi_dimensions",),
    "ShaderNodeTexImage": ("interpolation", "extension", "projection"),
    "ShaderNodeMapping": ("vector_type",), "ShaderNodeNormalMap": ("space", "uv_map"),
    "ShaderNodeSeparateColor": ("mode",), "ShaderNodeCombineColor": ("mode",),
    "ShaderNodeMapRange": ("interpolation_type",),
}
NODE_TYPES = (
    "ShaderNodeBsdfPrincipled", "ShaderNodeBsdfDiffuse", "ShaderNodeBsdfGlass",
    "ShaderNodeEmission", "ShaderNodeMixShader", "ShaderNodeAddShader", "ShaderNodeOutputMaterial",
    "ShaderNodeTexImage", "ShaderNodeTexNoise", "ShaderNodeTexVoronoi", "ShaderNodeTexChecker",
    "ShaderNodeTexCoord", "ShaderNodeMapping", "ShaderNodeMath", "ShaderNodeVectorMath",
    "ShaderNodeMixRGB", "ShaderNodeNormalMap", "ShaderNodeBump", "ShaderNodeRGB",
    "ShaderNodeValue", "ShaderNodeSeparateColor", "ShaderNodeCombineColor", "ShaderNodeMapRange",
    "ShaderNodeValToRGB",
)


def plain(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        return list(value)
    except TypeError:
        return None


def socket_info(socket, index):
    return {"index": index, "name": socket.name, "identifier": socket.identifier,
            "type": socket.type, "enabled": socket.enabled and not socket.is_unavailable,
            "linked": socket.is_linked, "default": plain(getattr(socket, "default_value", None))}


def resolve(sockets, reference):
    if isinstance(reference, int) and not isinstance(reference, bool):
        if not 0 <= reference < len(sockets):
            raise ValueError(f"Socket index out of range: {reference}")
        socket = sockets[reference]
    else:
        matches = [s for s in sockets if s.identifier == reference]
        if not matches:
            matches = [s for s in sockets if s.name == reference]
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous socket {reference!r}; use catalog/inspection identifiers or indexes")
        socket = matches[0]
    if socket.is_unavailable or not socket.enabled:
        raise ValueError(f"Socket is unavailable in the current node mode: {reference}")
    return socket


def set_value(socket, value):
    if not hasattr(socket, "default_value"):
        raise ValueError(f"Socket has no editable default: {socket.name}")
    expected = socket.type
    if expected in {"RGBA", "VECTOR"}:
        if not isinstance(value, list) or len(value) != (4 if expected == "RGBA" else 3):
            raise ValueError(f"Socket {socket.name} requires a {'4' if expected == 'RGBA' else '3'} component value")
        values = value
    elif expected == "BOOLEAN":
        if not isinstance(value, bool):
            raise ValueError("Boolean socket requires a boolean")
        values = [value]
    elif expected in {"VALUE", "INT"}:
        if isinstance(value, (bool, list)) or not isinstance(value, (int, float)):
            raise ValueError("Numeric socket requires a number")
        if expected == "INT" and int(value) != value:
            raise ValueError("Integer socket requires an integer")
        values = [value]
    else:
        raise ValueError(f"Unsupported socket default type: {expected}")
    prop = socket.bl_rna.properties["default_value"]
    if any(not math.isfinite(v) or v < getattr(prop, "hard_min", -math.inf) or v > getattr(prop, "hard_max", math.inf) for v in values):
        raise ValueError(f"Socket default is outside its supported range: {socket.name}")
    socket.default_value = int(value) if expected == "INT" else value


def material(name, edit=False):
    result = bpy.data.materials.get(name)
    if result is None:
        raise ValueError(f"Material not found: {name}")
    if edit and (result.library or result.override_library):
        raise ValueError("Shader editing requires a local material")
    if edit and any(data and data.animation_data for data in (result, result.node_tree)):
        raise ValueError("Animated/driven materials require a specialized editing workflow")
    return result


def inspect(mat):
    tree = mat.node_tree
    nodes = []
    for node in list(tree.nodes)[:128] if tree else []:
        info = {"name": node.name, "type": node.bl_idname, "label": node.label,
                "inputs": [socket_info(s, i) for i, s in enumerate(node.inputs)],
                "outputs": [socket_info(s, i) for i, s in enumerate(node.outputs)],
                "properties": {key: plain(getattr(node, key)) for key in PROPERTIES.get(node.bl_idname, ()) if hasattr(node, key)}}
        if node.bl_idname == "ShaderNodeTexImage" and node.image:
            info["image"] = {"name": node.image.name, "path": node.image.filepath,
                             "color_space": node.image.colorspace_settings.name, "packed": bool(node.image.packed_file)}
        if node.bl_idname == "ShaderNodeValToRGB":
            info["ramp"] = [{"position": e.position, "color": list(e.color)} for e in node.color_ramp.elements]
        nodes.append(info)
    return {"material": mat.name, "use_nodes": mat.use_nodes, "nodes": nodes,
            "total_nodes": len(tree.nodes) if tree else 0,
            "links": [{"from_node": l.from_node.name, "from_socket": l.from_socket.identifier,
                       "to_node": l.to_node.name, "to_socket": l.to_socket.identifier,
                       "valid": l.is_valid} for l in list(tree.links)[:512]] if tree else []}


def node_catalog(p):
    if not p.get("node_type"):
        return {"node_types": list(NODE_TYPES), "note": "Request node_type for installed socket/property details. Identifiers are preferred; duplicate names require an index."}
    if p["node_type"] not in NODE_TYPES:
        raise ValueError("Unsupported shader node type")
    scratch = bpy.data.materials.new("MCP_ShaderCatalog")
    try:
        scratch.use_nodes = True
        scratch.node_tree.nodes.clear()
        node = scratch.node_tree.nodes.new(p["node_type"])
        data = inspect(scratch)["nodes"][0]
        data["editable_properties"] = {}
        for key in PROPERTIES.get(node.bl_idname, ()):
            prop = node.bl_rna.properties.get(key)
            if prop:
                data["editable_properties"][key] = {"type": prop.type,
                    "choices": [e.identifier for e in prop.enum_items] if prop.type == "ENUM" else None}
        return data
    finally:
        bpy.data.materials.remove(scratch)


def image_for(spec):
    path, color = Path(spec["image_path"]).resolve(), spec.get("color_space", "sRGB")
    pack = spec.get("pack_image", True)
    for image in bpy.data.images:
        if image.source == "FILE" and image.filepath and Path(bpy.path.abspath(image.filepath)).resolve() == path and image.colorspace_settings.name == color and (not pack or image.packed_file):
            return image
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = color
    if pack:
        image.pack()
    return image


def configure_node(tree, spec):
    if spec["type"] not in NODE_TYPES:
        raise ValueError("Unsupported shader node type")
    node = tree.nodes.get(spec["name"])
    if node and node.bl_idname != spec["type"]:
        raise ValueError("Existing shader node has a different type")
    node = node or tree.nodes.new(spec["type"])
    node.name, node.label = spec["name"], spec.get("label", "")
    for key, value in spec.get("properties", {}).items():
        if value is None:
            continue
        if key not in PROPERTIES.get(spec["type"], ()) or key not in node.bl_rna.properties:
            raise ValueError(f"Property {key} is unsupported for {spec['type']}")
        prop = node.bl_rna.properties[key]
        if prop.type == "ENUM" and value not in {item.identifier for item in prop.enum_items}:
            raise ValueError(f"Unsupported {key} value: {value}; use shader_node_catalog")
        setattr(node, key, value)
    if spec.get("image_path"):
        node.image = image_for(spec)
    for item in spec.get("inputs", []):
        set_value(resolve(node.inputs, item["socket"]), item["value"])
    if spec.get("output_value") is not None:
        set_value(node.outputs[0], spec["output_value"])
    if spec.get("ramp"):
        stops = sorted(spec["ramp"], key=lambda s: s["position"])
        ramp = node.color_ramp
        while len(ramp.elements) > 2:
            ramp.elements.remove(ramp.elements[-1])
        for element, stop in zip(ramp.elements, stops[:2]):
            element.position, element.color = stop["position"], stop["color"]
        for stop in stops[2:]:
            ramp.elements.new(stop["position"]).color = stop["color"]
    return node


def connect(tree, links, replace):
    used = set()
    for item in links:
        source, target = tree.nodes.get(item["from_node"]), tree.nodes.get(item["to_node"])
        if source is None or target is None:
            raise ValueError("Shader link references a missing node")
        output, input = resolve(source.outputs, item["from_socket"]), resolve(target.inputs, item["to_socket"])
        if input in used:
            raise ValueError("Each destination socket may occur only once")
        used.add(input)
        if (output.type == "SHADER") != (input.type == "SHADER"):
            raise ValueError("Shader sockets can only connect to shader sockets")
        if input.is_linked and not replace:
            raise ValueError("Input already linked; set replace_existing=true explicitly")
        tree.links.new(output, input)
    graph = {node.name: [] for node in tree.nodes}
    for link in tree.links:
        graph[link.from_node.name].append(link.to_node.name)
    visited, visiting = set(), set()
    def visit(name):
        if name in visiting:
            raise ValueError("Shader links must not create cycles")
        if name not in visited:
            visiting.add(name)
            for target in graph[name]:
                visit(target)
            visiting.remove(name)
            visited.add(name)
    for name in graph:
        visit(name)


def edit(command, p):
    existing = bpy.data.materials.get(p["material"])
    if existing:
        material(existing.name, edit=True)
        if command == "configure_shader_graph" and not p["replace"]:
            raise ValueError("Existing material requires replace=true")
        if existing.node_tree and len(existing.node_tree.nodes) > 256:
            raise ValueError("Structured editing supports up to 256 existing nodes")
    elif command != "configure_shader_graph":
        raise ValueError("Create the material with configure_shader_graph or a preset first")
    images_before = set(bpy.data.images)
    staged = existing.copy() if existing else bpy.data.materials.new("MCP_ShaderPreflight")
    def apply(mat):
        mat.use_nodes = True
        tree = mat.node_tree
        if command == "configure_shader_graph":
            tree.nodes.clear()
            for spec in p["nodes"]:
                configure_node(tree, spec)
            connect(tree, p["links"], False)
        elif command == "configure_shader_node":
            configure_node(tree, p["node"])
        elif command == "connect_shader_nodes":
            connect(tree, p["links"], p["replace_existing"])
        elif command == "disconnect_shader_input":
            node = tree.nodes.get(p["node"])
            if node is None:
                raise ValueError("Shader node not found")
            for link in list(resolve(node.inputs, p["socket"]).links):
                tree.links.remove(link)
        elif command == "remove_shader_node":
            node = tree.nodes.get(p["node"])
            if node is None:
                raise ValueError("Shader node not found")
            tree.nodes.remove(node)
    try:
        apply(staged)
        if existing:
            apply(existing)
            return inspect(existing)
        staged.name = p["material"]
        result = inspect(staged)
        staged = None
        return result
    finally:
        if staged is not None:
            bpy.data.materials.remove(staged)
        for image in set(bpy.data.images) - images_before:
            if image.users == 0:
                bpy.data.images.remove(image)


def preset(p):
    def value(socket, value):
        return {"socket": socket, "value": value}
    nodes = [{"name": "Output", "type": "ShaderNodeOutputMaterial"}]
    links = []
    def link(source, output, target, input):
        links.append({"from_node": source, "from_socket": output, "to_node": target, "to_socket": input})
    if p["preset"] == "EMISSION":
        nodes.append({"name": "Surface", "type": "ShaderNodeEmission", "inputs": [value("Color", p["emission_color"]), value("Strength", p["emission_strength"])]})
    else:
        inputs = [value("Base Color", p["base_color"]), value("Roughness", p["roughness"]),
                  value("Metallic", p["metallic"]), value("IOR", p["ior"])]
        if p["preset"] == "GLASS":
            inputs.append(value("Transmission Weight", 1))
        nodes.append({"name": "Surface", "type": "ShaderNodeBsdfPrincipled", "inputs": inputs})
    link("Surface", 0, "Output", "Surface")
    maps = [("base_color_texture", "BaseColor", "Base Color", "sRGB"),
            ("roughness_texture", "Roughness", "Roughness", "Non-Color"),
            ("metallic_texture", "Metallic", "Metallic", "Non-Color"),
            ("normal_texture", "NormalTexture", "Normal", "Non-Color")]
    if any(p.get(key) for key, *_ in maps):
        nodes.extend([{"name": "Coordinates", "type": "ShaderNodeTexCoord"},
                      {"name": "Mapping", "type": "ShaderNodeMapping", "inputs": [value("Scale", p["texture_scale"])]}])
        link("Coordinates", "UV", "Mapping", "Vector")
    for key, name, socket, color in maps:
        if not p.get(key):
            continue
        nodes.append({"name": name, "type": "ShaderNodeTexImage", "image_path": p[key], "color_space": color, "pack_image": p["pack_images"]})
        link("Mapping", "Vector", name, "Vector")
        if key == "normal_texture":
            nodes.append({"name": "NormalMap", "type": "ShaderNodeNormalMap", "inputs": [value("Strength", p["normal_strength"])]})
            link(name, "Color", "NormalMap", "Color")
            link("NormalMap", "Normal", "Surface", socket)
        elif key in {"roughness_texture", "metallic_texture"}:
            channel = name + "Channels"
            nodes.append({"name": channel, "type": "ShaderNodeSeparateColor", "properties": {"mode": "RGB"}})
            link(name, "Color", channel, "Color")
            link(channel, "Red", "Surface", socket)
        else:
            link(name, "Color", "Surface", socket)
    return edit("configure_shader_graph", {"material": p["material"], "replace": p["replace"], "nodes": nodes, "links": links}) | {"preset": p["preset"]}


def dispatch(command, p):
    if command == "shader_node_catalog":
        return node_catalog(p)
    if command == "inspect_shader_graph":
        return inspect(material(p["material"]))
    if command == "apply_material_preset":
        return preset(p)
    return edit(command, p)
