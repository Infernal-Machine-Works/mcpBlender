"""Relative shape-key editing and bounded inspection without user Python."""
from array import array
from contextlib import contextmanager
import hashlib
import json
import math
import struct

import bpy

from .assets import mesh_object

COMMANDS = {"create_shape_key", "edit_shape_key", "inspect_shape_keys", "set_shape_key_weights", "remove_shape_key"}
COMMANDS.update({"create_morph_clip", "set_morph_keyframes", "set_active_morph_clip", "inspect_morph_animation", "sample_morph_animation"})


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
            "show_only_shape_key": obj.show_only_shape_key,
            "active_shape_key": obj.active_shape_key.name if obj.active_shape_key else None,
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
    if command == "inspect_morph_animation":
        return inspect_animation(p)
    if command == "sample_morph_animation":
        return sample_animation(p)
    if command in {"create_morph_clip", "set_morph_keyframes", "set_active_morph_clip"}:
        return edit_animation(command, p)
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
        obj.show_only_shape_key = False
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


def animation_target(name, edit=True):
    obj = mesh_object(name, edit=edit)
    validate_target(obj)
    if not obj.data.shape_keys or len(obj.data.shape_keys.key_blocks) < 2:
        raise ValueError("Morph animation requires a mesh with non-Basis shape keys")
    if edit and obj.data.users > 1:
        raise ValueError("Morph animation needs independent mesh data; isolate the mesh first")
    if edit:
        simple_animation(obj.data.shape_keys)
    return obj


def action_slot(obj, action, identifier=None):
    from .domains import curves
    targets = managed_targets(action)
    if targets and obj.name not in targets:
        raise ValueError("Morph clip was created for a different mesh")
    slots = [s for s in action.slots if s.target_id_type == "KEY"]
    identifier = identifier or targets.get(obj.name)
    matches = [s for s in slots if s.identifier == identifier] if identifier else [s for s in slots if s.name_display == obj.name]
    if not matches and not identifier and len(slots) == 1:
        matches = slots
    if len(matches) != 1:
        raise ValueError("Choose a compatible morph KEY slot explicitly using slot")
    slot = matches[0]
    channels = list(curves(action, slot))
    allowed = {b.path_from_id("value") for b in obj.data.shape_keys.key_blocks if b != obj.data.shape_keys.reference_key}
    if not channels or any(c.data_path not in allowed or c.array_index != 0 for c in channels):
        raise ValueError("Morph slot must contain only weights of keys present on this mesh")
    return slot


def animation_snapshot(obj):
    keys, scene = obj.data.shape_keys, bpy.context.scene
    anim = keys.animation_data
    return {"had_animation": anim is not None, "action": anim.action if anim else None,
            "slot": anim.action_slot if anim else None,
            "settings": {key: getattr(anim, key) for key in ("action_blend_type", "action_extrapolation", "action_influence", "use_nla")} if anim else {},
            "values": {b.name: b.value for b in keys.key_blocks},
            "solo": obj.show_only_shape_key, "active_index": obj.active_shape_key_index,
            "frame": scene.frame_current, "subframe": scene.frame_subframe}


def assign_action(obj, action, slot=None):
    keys = obj.data.shape_keys
    anim = keys.animation_data_create()
    anim.action = action
    if slot:
        anim.action_slot = slot
    anim.action_blend_type, anim.action_extrapolation, anim.action_influence = "REPLACE", "HOLD", 1
    anim.use_nla = False
    for block in keys.key_blocks:
        if block != keys.reference_key:
            block.value = 0
    obj.show_only_shape_key = False
    bpy.context.scene.frame_set(bpy.context.scene.frame_current, subframe=bpy.context.scene.frame_subframe)
    bpy.context.view_layer.update()


def restore_animation(obj, saved):
    keys = obj.data.shape_keys
    if saved["had_animation"]:
        anim = keys.animation_data_create()
        anim.action = saved["action"]
        if saved["slot"]:
            anim.action_slot = saved["slot"]
        for key, value in saved["settings"].items():
            setattr(anim, key, value)
    else:
        keys.animation_data_clear()
    for block in keys.key_blocks:
        block.value = saved["values"][block.name]
    obj.show_only_shape_key, obj.active_shape_key_index = saved["solo"], saved["active_index"]
    bpy.context.scene.frame_set(saved["frame"], subframe=saved["subframe"])
    bpy.context.view_layer.update()


def inspect_animation(p):
    from .domains import curves
    obj = animation_target(p["name"], edit=False)
    anim = obj.data.shape_keys.animation_data
    active = anim.action if anim else None
    action = bpy.data.actions[p["clip"]] if p.get("clip") else active
    detail = None
    if action:
        slot = action_slot(obj, action, p.get("slot"))
        channels = list(curves(action, slot))
        detail = {"name": action.name, "slot": slot.identifier, "frame_range": list(action.frame_range),
                  "total_tracks": len(channels), "tracks": [{"path": c.data_path, "keys": len(c.keyframe_points),
                  "first_key": list(c.keyframe_points[0].co) if c.keyframe_points else None,
                  "last_key": list(c.keyframe_points[-1].co) if c.keyframe_points else None}
                  for c in channels[:p.get("track_limit", 100)]]}
    actions = sorted(bpy.data.actions, key=lambda a: a.name)
    offset, limit = p.get("clip_offset", 0), p.get("clip_limit", 50)
    return {"name": obj.name, "active_clip": active.name if active else None, "detail": detail,
            "total_clips": len(actions), "next_clip_offset": offset + limit if offset + limit < len(actions) else None,
            "clips": [{"name": a.name, "frame_range": list(a.frame_range), "managed_targets": managed_targets(a),
                       "skeletal_target": a.get("mcpblender_target"), "persistent": a.use_fake_user,
                       "slots": [{"identifier": s.identifier, "target_type": s.target_id_type} for s in a.slots][:100]}
                      for a in actions[offset:offset + limit]]}


def slot_is_shared(keys, action, slot):
    for other in bpy.data.shape_keys:
        if other == keys or not other.animation_data:
            continue
        anim = other.animation_data
        if anim.action == action and anim.action_slot == slot:
            return True
        if any(s.action == action and s.action_slot == slot for t in anim.nla_tracks for s in t.strips):
            return True
    return False


def edit_animation(command, p):
    from .domains import curves
    from .bridge import check_cancelled, report_progress
    obj = animation_target(p["name"])
    keys = obj.data.shape_keys
    if command == "set_active_morph_clip":
        action = bpy.data.actions[p["clip"]] if p.get("clip") else None
        slot = action_slot(obj, action, p.get("slot")) if action else None
        assign_action(obj, action, slot)
        return {"name": obj.name, "active_clip": action.name if action else None, "weights_reset": True}
    for track in p["tracks"]:
        block = keys.key_blocks.get(track["key"])
        if block is None or block == keys.reference_key:
            raise ValueError("Choose existing non-Basis shape keys for animation tracks")
        if any(not block.slider_min <= k["value"] <= block.slider_max for k in track["keyframes"]):
            raise ValueError("Keyframe weight lies outside the shape-key slider range")
    creating = command == "create_morph_clip"
    action = bpy.data.actions.get(p["clip"])
    new_action = creating and action is None
    old_targets = managed_targets(action) if action else {}
    if creating and action:
        if not p.get("join_existing", False) or obj.name in old_targets:
            raise ValueError("Clip name already exists; explicitly join it or use set_morph_keyframes")
        owner = action.get("mcpblender_target")
        if not (owner or old_targets) or (owner and not any(m.type == "ARMATURE" and m.object and m.object.name == owner for m in obj.modifiers)):
            raise ValueError("Can join only a compatible managed action; skeletal owner must bind this mesh")
    if action and (action.library or action.override_library):
        raise ValueError("Morph action must be local and editable")
    if not creating:
        if not action or obj.name not in old_targets:
            raise ValueError("Only this mesh's managed morph slot can be edited")
        slot = action_slot(obj, action)
        if slot_is_shared(keys, action, slot):
            raise ValueError("Morph slot is shared by another shape-key datablock")
    saved, new_slot, succeeded = animation_snapshot(obj), None, False
    try:
        if new_action:
            action = bpy.data.actions.new(p["clip"])
            action.use_fake_user = True
            if action.name != p["clip"]:
                raise ValueError("Blender could not preserve the requested clip name")
        if creating:
            new_slot = action.slots.new(id_type="KEY", name=obj.name)
            slot = new_slot
        assign_action(obj, action, slot)
        touched = {}
        for index, track in enumerate(p["tracks"]):
            check_cancelled()
            block = keys.key_blocks[track["key"]]
            touched[block.path_from_id("value")] = {k["frame"] for k in track["keyframes"]}
            for key in sorted(track["keyframes"], key=lambda k: k["frame"]):
                block.value = key["value"]
                if not block.keyframe_insert(data_path="value", frame=key["frame"], group=block.name):
                    raise RuntimeError("Blender refused a morph keyframe")
            report_progress({"phase": "keying morph tracks", "fraction": (index + 1) / len(p["tracks"])})
        for channel in curves(action, slot):
            for key in channel.keyframe_points:
                if channel.data_path in touched and key.co.x in touched[channel.data_path]:
                    key.interpolation = p.get("interpolation", "LINEAR")
        action["mcpblender_morph_targets"] = json.dumps(old_targets | {obj.name: slot.identifier})
        succeeded = True
    finally:
        restore_animation(obj, saved)
        if not succeeded:
            if new_action and action:
                bpy.data.actions.remove(action)
            elif new_slot:
                action.slots.remove(new_slot)
    if creating and p.get("activate", True):
        assign_action(obj, action, slot)
    elif saved["action"] == action:
        assign_action(obj, action, slot)
    return inspect_animation({"name": obj.name, "clip": action.name})


def sample_animation(p):
    from .assets import inspect_mesh
    from .bridge import check_cancelled, report_progress
    from . import rigging
    obj = animation_target(p["name"])
    keys = obj.data.shape_keys
    action = bpy.data.actions[p["clip"]] if p.get("clip") else (keys.animation_data.action if keys.animation_data else None)
    slot = action_slot(obj, action, p.get("slot")) if action else None
    rig, rig_saved, skeletal_action, skeletal_slot = None, None, None, None
    if p.get("armature"):
        rig = rigging.armature_object(p["armature"], edit=True)
        rigging.simple_animation(rig)
        if not any(m.type == "ARMATURE" and m.object == rig for m in obj.modifiers):
            raise ValueError("Mesh must be bound to the sampled armature")
        skeletal_action = bpy.data.actions[p["skeletal_clip"]] if p.get("skeletal_clip") else (rig.animation_data.action if rig.animation_data else None)
        skeletal_slot = rigging.action_slot(rig, skeletal_action, p.get("skeletal_slot")) if skeletal_action else None
        rig_saved = rigging.animation_snapshot(rig)
    saved, samples = animation_snapshot(obj), []
    try:
        if action:
            assign_action(obj, action, slot)
        if rig and skeletal_action:
            rigging.assign_action(rig, skeletal_action, skeletal_slot)
        for index, frame in enumerate(p["frames"]):
            check_cancelled()
            bpy.context.scene.frame_set(frame)
            info = inspect_mesh({"name": obj.name, "sample_limit": p.get("sample_limit", 16)})
            samples.append({"frame": frame, "values": {b.name: b.value for b in keys.key_blocks if b != keys.reference_key},
                            "mesh": {k: info[k] for k in ("vertices", "world_bounds", "geometry_sha256", "vertex_samples", "non_finite_vertices")}})
            report_progress({"phase": "sampling morph animation", "fraction": (index + 1) / len(p["frames"]), "frame": frame})
    finally:
        if rig_saved:
            rigging.restore_animation(rig, rig_saved)
        restore_animation(obj, saved)
    return {"name": obj.name, "clip": action.name if action else None, "samples": samples,
            "skeletal_clip": skeletal_action.name if skeletal_action else None,
            "restored_frame": saved["frame"], "restored_clip": saved["action"].name if saved["action"] else None}


@contextmanager
def export_managed_clips(selected_only=True):
    objects = list(bpy.context.selected_objects if selected_only else bpy.context.scene.objects)
    meshes, seen = [], set()
    for obj in objects:
        if obj.type == "MESH" and obj.data.shape_keys and obj.data.shape_keys.use_relative and obj.data.shape_keys not in seen:
            seen.add(obj.data.shape_keys)
            meshes.append(obj)
    saved = [(obj, animation_snapshot(obj)) for obj in meshes]
    tracks = []
    try:
        for obj in meshes:
            keys = obj.data.shape_keys
            existing = {(keys.animation_data.action, keys.animation_data.action_slot)} if keys.animation_data else set()
            if keys.animation_data:
                existing.update((s.action, s.action_slot) for t in keys.animation_data.nla_tracks for s in t.strips)
            for action in bpy.data.actions:
                if obj.name not in managed_targets(action):
                    continue
                slot = action_slot(obj, action)
                if (action, slot) in existing:
                    continue
                track = keys.animation_data_create().nla_tracks.new()
                tracks.append((keys, track))
                track.name, track.mute = action.name, True
                strip = track.strips.new(action.name, int(math.floor(action.frame_range[0])), action)
                strip.action_slot = slot
        yield
    finally:
        for keys, track in reversed(tracks):
            keys.animation_data.nla_tracks.remove(track)
        for obj, state in saved:
            restore_animation(obj, state)
