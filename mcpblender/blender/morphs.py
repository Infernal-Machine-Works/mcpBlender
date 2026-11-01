"""Relative shape-key editing and bounded inspection without user Python."""
from array import array
import hashlib
import json
import math
import struct

import bpy

from .assets import mesh_object

COMMANDS = {"create_shape_key", "edit_shape_key", "inspect_shape_keys", "set_shape_key_weights", "remove_shape_key"}


def topology_digest(mesh):
    digest = hashlib.sha256(struct.pack("!3I", len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
    for edge in mesh.edges:
        digest.update(struct.pack("!2I", *edge.vertices))
    for face in mesh.polygons:
        digest.update(struct.pack("!I", len(face.vertices)))
        digest.update(struct.pack(f"!{len(face.vertices)}I", *face.vertices))
    return digest.hexdigest()


def managed_targets(action):
    return json.loads(action.get("mcpblender_morph_targets", "{}"))


def validate_target(obj, expected=None):
    digest = topology_digest(obj.data)
    keys = obj.data.shape_keys
    if expected and expected != digest:
        raise ValueError("Mesh topology does not match expected_topology")
    if not obj.data.vertices:
        raise ValueError("Shape keys require mesh vertices")
    if keys:
        if not keys.use_relative:
            raise ValueError("Structured editing supports relative shape keys, not absolute keys")
        if keys.library or keys.override_library:
            raise ValueError("Shape-key data must be local and editable")
        if keys.get("mcpblender_topology") and keys["mcpblender_topology"] != digest:
            raise ValueError("Stored shape-key topology no longer matches the mesh")
        if any(len(block.data) != len(obj.data.vertices) for block in keys.key_blocks):
            raise ValueError("Shape-key vertex count does not match mesh topology")
    return digest


def editable(p):
    obj = mesh_object(p["name"], edit=True)
    digest = validate_target(obj, p.get("expected_topology"))
    if obj.data.users > 1 and not p.get("make_single_user", False):
        raise ValueError("Mesh data is shared; pass make_single_user=true to isolate this object")
    return obj, digest


def isolate(obj):
    if obj.data.users > 1:
        obj.data = obj.data.copy()


def simple_animation(keys, static=False):
    animation = keys.animation_data
    if animation and (animation.drivers or animation.use_tweak_mode or
            any(not t.mute or t.is_solo for t in animation.nla_tracks)):
        raise ValueError("Morph tools require shape keys without drivers or active NLA tracks")
    if static and animation and animation.action:
        raise ValueError("Clear the active morph clip before setting static weights")


def inspect_shapes(p):
    obj = mesh_object(p["name"])
    digest, keys = topology_digest(obj.data), obj.data.shape_keys
    blocks = list(keys.key_blocks) if keys else []
    if p.get("key"):
        if not keys or p["key"] not in keys.key_blocks:
            raise ValueError("Shape key not found")
        selected = [keys.key_blocks[p["key"]]]
    else:
        selected = blocks[p.get("offset", 0):p.get("offset", 0) + p.get("limit", 20)]
    result = []
    for block in selected:
        hashed, samples, moved, invalid, maximum = hashlib.sha256(), [], 0, 0, 0.0
        for index, point in enumerate(block.data):
            co = tuple(point.co)
            finite = all(math.isfinite(v) for v in co)
            hashed.update(struct.pack("!3d", *co))
            reference = keys.reference_key.data[index].co if index < len(keys.reference_key.data) else None
            delta = [co[i] - reference[i] for i in range(3)] if reference is not None and finite else None
            valid_delta = delta is not None and all(math.isfinite(v) for v in delta)
            invalid += not finite or not valid_delta
            length = math.sqrt(sum(v * v for v in delta)) if valid_delta else 0
            moved += length > 1e-8
            maximum = max(maximum, length)
            if len(samples) < p.get("sample_limit", 16) and (length > 1e-8 or block == keys.reference_key):
                samples.append({"vertex": index, "position": list(co) if finite else None, "offset": delta if valid_delta else None})
        result.append({"name": block.name, "value": block.value, "mute": block.mute,
                       "relative_key": block.relative_key.name if block.relative_key else None,
                       "vertex_group": block.vertex_group, "slider_min": block.slider_min, "slider_max": block.slider_max,
                       "vertices": len(block.data), "moved_vertices": moved, "max_offset": maximum,
                       "non_finite_vertices": invalid, "coordinates_sha256": hashed.hexdigest(), "vertex_samples": samples})
    offset, limit = p.get("offset", 0), p.get("limit", 20)
    return {"name": obj.name, "topology_sha256": digest, "vertices": len(obj.data.vertices),
            "basis": keys.reference_key.name if keys else None, "relative": keys.use_relative if keys else None,
            "managed_topology_matches": (keys.get("mcpblender_topology") == digest) if keys and keys.get("mcpblender_topology") else None,
            "total_keys": len(blocks), "next_offset": offset + limit if not p.get("key") and offset + limit < len(blocks) else None,
            "active_clip": keys.animation_data.action.name if keys and keys.animation_data and keys.animation_data.action else None,
            "keys": result, "sampling": "Non-Basis samples contain changed vertices; positions/offsets are mesh-local and relative to Basis"}


def edit_shape(command, p):
    obj, digest = editable(p)
    keys = obj.data.shape_keys
    block = keys.key_blocks.get(p["key"]) if keys else None
    creating = command == "create_shape_key"
    if creating and (block or p["key"] == "Basis"):
        raise ValueError("Shape key name already exists or is reserved for Basis")
    if not creating and (not block or block == keys.reference_key):
        raise ValueError("Choose an existing non-Basis shape key")
    if block and block.relative_key != keys.reference_key:
        raise ValueError("Editing supports only keys relative to Basis")
    basis = keys.reference_key.data if keys else obj.data.vertices
    updates = []
    if p.get("source_object"):
        source = mesh_object(p["source_object"])
        if topology_digest(source.data) != digest:
            raise ValueError("Source object must have matching ordered mesh topology")
        source_points = source.data.shape_keys.reference_key.data if source.data.shape_keys else source.data.vertices
        updates = [(i, tuple(v.co)) for i, v in enumerate(source_points)]
    else:
        for entry in p.get("vertices", []):
            index = entry["vertex"]
            if index >= len(basis):
                raise ValueError("Vertex index is outside the mesh")
            value = entry["value"]
            updates.append((index, tuple(value[i] + (basis[index].co[i] if p.get("mode", "OFFSET") == "OFFSET" else 0) for i in range(3))))
    if any(not math.isfinite(v) or abs(v) > 3.4e38 for _, co in updates for v in co):
        raise ValueError("Shape coordinates must be finite Blender float values")
    isolate(obj)
    active_index, solo = obj.active_shape_key_index, obj.show_only_shape_key
    had_keys = obj.data.shape_keys is not None
    new_block = None
    try:
        if not had_keys:
            obj.shape_key_add(name="Basis", from_mix=False)
        keys = obj.data.shape_keys
        block = obj.shape_key_add(name=p["key"], from_mix=False) if creating else keys.key_blocks[p["key"]]
        new_block = block if creating else None
        if block.name != p["key"]:
            raise ValueError("Blender could not preserve the requested shape-key name")
        if creating:
            block.value = 0
        if creating or p.get("reset_unspecified", False):
            coordinates = array("f", [0]) * (len(keys.reference_key.data) * 3)
            keys.reference_key.data.foreach_get("co", coordinates)
            block.data.foreach_set("co", coordinates)
        for index, co in updates:
            block.data[index].co = co
        keys["mcpblender_topology"] = digest
        obj.data.update()
        bpy.context.view_layer.update()
    except Exception:
        if new_block:
            obj.shape_key_remove(new_block)
        if not had_keys and obj.data.shape_keys:
            obj.shape_key_clear()
        raise
    finally:
        obj.active_shape_key_index, obj.show_only_shape_key = active_index, solo
    return inspect_shapes({"name": obj.name, "key": p["key"]})


def animation_references(obj, block):
    from .domains import curves
    keys, associated = obj.data.shape_keys, []
    if keys.animation_data:
        anim = keys.animation_data
        if anim.action:
            associated.append((anim.action, anim.action_slot))
        associated.extend((s.action, s.action_slot) for t in anim.nla_tracks for s in t.strips if s.action)
        if any(d.data_path == block.path_from_id("value") for d in anim.drivers):
            return True
    for action in bpy.data.actions:
        identifier = managed_targets(action).get(obj.name)
        if identifier:
            associated.append((action, next((s for s in action.slots if s.identifier == identifier), None)))
    return any(c.data_path == block.path_from_id("value") for action, slot in associated if slot is not None
               for c in curves(action, slot))


def dispatch(command, p):
    if command == "inspect_shape_keys":
        return inspect_shapes(p)
    if command in {"create_shape_key", "edit_shape_key"}:
        return edit_shape(command, p)
    obj, _ = editable(p)
    keys = obj.data.shape_keys
    if not keys:
        raise ValueError("Mesh has no shape keys")
    if command == "set_shape_key_weights":
        simple_animation(keys, static=True)
        if any(name not in keys.key_blocks or keys.key_blocks[name] == keys.reference_key for name in p["values"]):
            raise ValueError("Choose existing non-Basis shape keys")
        if any(not keys.key_blocks[name].slider_min <= value <= keys.key_blocks[name].slider_max for name, value in p["values"].items()):
            raise ValueError("Requested value lies outside the shape-key slider range")
        isolate(obj)
        keys = obj.data.shape_keys
        for block in keys.key_blocks:
            if block.name in p["values"]:
                block.value = p["values"][block.name]
            elif p.get("reset_others", False) and block != keys.reference_key:
                block.value = 0
        bpy.context.view_layer.update()
        return {"name": obj.name, "values": {b.name: b.value for b in keys.key_blocks if b != keys.reference_key}}
    if command == "remove_shape_key":
        block = keys.key_blocks.get(p["key"])
        if not block or block == keys.reference_key:
            raise ValueError("Choose an existing non-Basis shape key")
        if any(b.relative_key == block and b != block for b in keys.key_blocks) or animation_references(obj, block):
            raise ValueError("Shape key has relative-key dependencies or animation references")
        isolate(obj)
        obj.shape_key_remove(obj.data.shape_keys.key_blocks[p["key"]])
        return {"name": obj.name, "removed": p["key"]}
    raise ValueError(f"Unsupported morph command: {command}")
