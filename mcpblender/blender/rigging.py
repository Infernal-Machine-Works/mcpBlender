"""Structured skeleton construction, skinning and animation inspection."""
from contextlib import contextmanager
import math

import bpy
from mathutils import Matrix

from .assets import active_object, finished, local_object, mesh_object

COMMANDS = {"create_armature", "bind_mesh", "inspect_rig"}
COMMANDS.update({"create_animation_clip", "set_pose_keyframes", "set_active_clip", "inspect_animation", "sample_animation"})


def armature_object(name, edit=False):
    obj = local_object(name) if edit else bpy.data.objects.get(name)
    if obj is None or obj.type != "ARMATURE":
        raise ValueError(f"Expected an armature object: {name}")
    if obj.mode != "OBJECT":
        raise ValueError("Object mode required for structured rig tools")
    return obj


def matrix_rows(matrix):
    return [list(row) for row in matrix]


def weight_info(mesh, rig, sample_limit=16):
    deform = {b.name for b in rig.data.bones if b.use_deform}
    groups = {g.index: g.name for g in mesh.vertex_groups if g.name in deform}
    stats = {name: {"bone": name, "vertices": 0, "total_weight": 0.0} for name in groups.values()}
    samples, unweighted, unnormalized, invalid, maximum = [], 0, 0, 0, 0
    minimum_sum, maximum_sum = math.inf, 0.0
    for vertex in mesh.data.vertices:
        influences = {groups[g.group]: g.weight for g in vertex.groups if g.group in groups and g.weight > 0}
        total = sum(influences.values())
        unweighted += total <= 1e-8
        unnormalized += total > 1e-8 and abs(total - 1) > 1e-4
        invalid += any(not math.isfinite(v) for v in influences.values())
        maximum = max(maximum, len(influences))
        minimum_sum, maximum_sum = min(minimum_sum, total), max(maximum_sum, total)
        for name, value in influences.items():
            stats[name]["vertices"] += 1
            stats[name]["total_weight"] += value
        if len(samples) < sample_limit:
            samples.append({"vertex": vertex.index, "weights": influences})
    return {"name": mesh.name, "vertices": len(mesh.data.vertices), "unweighted_vertices": unweighted,
            "unnormalized_vertices": unnormalized, "non_finite_vertices": invalid,
            "max_influences": maximum, "weight_sum_min": minimum_sum if mesh.data.vertices else 0,
            "weight_sum_max": maximum_sum, "groups": list(stats.values())[:1000],
            "total_groups": len(stats), "vertex_samples": samples,
            "armature_modifiers": [{"name": m.name, "show_viewport": m.show_viewport, "show_render": m.show_render}
                                   for m in mesh.modifiers if m.type == "ARMATURE" and m.object == rig]}


def inspect_rig(p):
    rig = armature_object(p["name"])
    bpy.context.view_layer.update()
    offset, limit = p.get("bone_offset", 0), p.get("bone_limit", 200)
    bones = list(rig.data.bones)
    if p.get("meshes") is not None:
        meshes = [mesh_object(name) for name in p["meshes"]]
    else:
        meshes = sorted((o for o in bpy.context.scene.objects if o.type == "MESH" and
                         any(m.type == "ARMATURE" and m.object == rig for m in o.modifiers)), key=lambda o: o.name)
    posed = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {"name": rig.name, "frame": bpy.context.scene.frame_current, "pose_position": rig.data.pose_position,
            "matrix_world": matrix_rows(rig.matrix_world), "active_clip": rig.animation_data.action.name
                if rig.animation_data and rig.animation_data.action else None,
            "total_bones": len(bones), "next_bone_offset": offset + limit if offset + limit < len(bones) else None,
            "bones": [{"name": b.name, "parent": b.parent.name if b.parent else None, "connected": b.use_connect,
                       "deform": b.use_deform, "rest_head": list(b.head_local), "rest_tail": list(b.tail_local),
                       "rest_matrix": matrix_rows(b.matrix_local),
                       "pose_matrix": matrix_rows(posed.pose.bones[b.name].matrix),
                       "pose_head_world": list(posed.matrix_world @ posed.pose.bones[b.name].head),
                       "pose_tail_world": list(posed.matrix_world @ posed.pose.bones[b.name].tail)}
                      for b in bones[offset:offset + limit]],
            "total_meshes": len(meshes), "meshes": [weight_info(m, rig, p.get("sample_limit", 16)) for m in meshes[:32]]}


def create_armature(p):
    if p["name"] in bpy.data.objects:
        raise ValueError("Object name already exists")
    if bpy.context.mode != "OBJECT":
        raise ValueError("Object mode required for armature creation")
    data = bpy.data.armatures.new(p["name"] + "Skeleton")
    rig = bpy.data.objects.new(p["name"], data)
    bpy.context.collection.objects.link(rig)
    rig.location = p.get("location", [0, 0, 0])
    try:
        with active_object(rig):
            finished(bpy.ops.object.mode_set(mode="EDIT"))
            for spec in p["bones"]:
                bone = data.edit_bones.new(spec["name"])
                bone.head, bone.tail = spec["head"], spec["tail"]
                bone.roll = math.radians(spec.get("roll_degrees", 0))
                bone.use_deform = spec.get("deform", True)
            for spec in p["bones"]:
                bone = data.edit_bones[spec["name"]]
                if spec.get("parent"):
                    bone.parent = data.edit_bones[spec["parent"]]
                    bone.use_connect = spec.get("connected", False)
            finished(bpy.ops.object.mode_set(mode="OBJECT"))
        for bone in rig.pose.bones:
            bone.rotation_mode = "XYZ"
        rig.show_in_front = True
        return inspect_rig({"name": rig.name})
    except Exception:
        bpy.data.objects.remove(rig, do_unlink=True)
        bpy.data.armatures.remove(data)
        raise


def bind_mesh(p):
    from .bridge import report_progress
    mesh, rig = mesh_object(p["name"], edit=True), armature_object(p["armature"], edit=True)
    if mesh.parent or any(m.type == "ARMATURE" for m in mesh.modifiers):
        raise ValueError("Mesh already has a parent or skin binding; use an unbound mesh")
    deform = {b.name for b in rig.data.bones if b.use_deform}
    if not deform or not mesh.data.vertices:
        raise ValueError("Binding requires deform bones and mesh vertices")
    if any(g.name in deform for g in mesh.vertex_groups):
        raise ValueError("Mesh already contains bone-named vertex groups")
    if mesh.data.users > 1 and not p.get("make_single_user", False):
        raise ValueError("Mesh data is shared; pass make_single_user=true to isolate this object")
    if rig.constraints or (rig.animation_data and (rig.animation_data.action or rig.animation_data.drivers or rig.animation_data.nla_tracks)):
        raise ValueError("Bind before animating the armature; a rest pose without constraints is required")
    identity = Matrix.Identity(4)
    if any(b.constraints or any(abs(b.matrix_basis[i][j] - identity[i][j]) > 1e-5 for i in range(4) for j in range(4)) for b in rig.pose.bones):
        raise ValueError("Binding requires all bones in their unconstrained rest pose")
    bpy.context.view_layer.update()
    if abs(rig.matrix_world.determinant()) < 1e-12:
        raise ValueError("Armature transform must be invertible")
    method = p.get("method", "AUTO")
    if method == "RIGID" and p["bone"] not in deform:
        raise ValueError("Rigid binding bone is not a deform bone")
    entries = p.get("weights", [])
    if method == "EXPLICIT":
        if any(e["vertex"] >= len(mesh.data.vertices) or set(e["weights"]) - deform for e in entries):
            raise ValueError("Explicit weights reference an unknown vertex or deform bone")
        if p.get("require_complete", True) and len(entries) != len(mesh.data.vertices):
            raise ValueError("Explicit weights must cover every vertex")
    with active_object(mesh):
        if bpy.context.view_layer.objects.get(rig.name) is not rig or not rig.visible_get() or rig.hide_select:
            raise ValueError("Armature must be visible and selectable in the current view layer")
        if mesh.data.users > 1:
            mesh.data = mesh.data.copy()
        old_groups, old_modifiers = set(mesh.vertex_groups), set(mesh.modifiers)
        world = mesh.matrix_world.copy()
        try:
            report_progress({"phase": "binding skin", "method": method})
            if method == "AUTO":
                rig.select_set(True)
                bpy.context.view_layer.objects.active = rig
                finished(bpy.ops.object.parent_set(type="ARMATURE_AUTO", keep_transform=True))
            else:
                groups = {name: mesh.vertex_groups.new(name=name) for name in sorted(deform)}
                if method == "RIGID":
                    groups[p["bone"]].add(list(range(len(mesh.data.vertices))), 1, "REPLACE")
                else:
                    for entry in entries:
                        total = sum(entry["weights"].values()) if p.get("normalize", True) else 1
                        for name, value in entry["weights"].items():
                            groups[name].add([entry["vertex"]], value / total, "REPLACE")
                mod = mesh.modifiers.new("Skin", "ARMATURE")
                mod.object = rig
                mesh.parent = rig
                mesh.matrix_parent_inverse = rig.matrix_world.inverted()
                mesh.matrix_world = world
            bpy.context.view_layer.update()
            info = weight_info(mesh, rig)
            if p.get("require_complete", True) and (info["unweighted_vertices"] or info["non_finite_vertices"]):
                raise ValueError("Binding left unweighted or non-finite vertices; binding changes were removed")
            report_progress({"phase": "skin bound", "fraction": 1})
            return info | {"armature": rig.name, "method": method}
        except Exception:
            for mod in list(mesh.modifiers):
                if mod not in old_modifiers:
                    mesh.modifiers.remove(mod)
            for group in list(mesh.vertex_groups):
                if group not in old_groups:
                    mesh.vertex_groups.remove(group)
            mesh.parent = None
            mesh.matrix_parent_inverse = Matrix.Identity(4)
            mesh.matrix_world = world
            raise


def dispatch(command, p):
    if command == "create_armature":
        return create_armature(p)
    if command == "bind_mesh":
        return bind_mesh(p)
    if command == "inspect_rig":
        return inspect_rig(p)
    if command == "inspect_animation":
        return inspect_animation(p)
    if command == "sample_animation":
        return sample_animation(p)
    if command in {"create_animation_clip", "set_pose_keyframes", "set_active_clip"}:
        return edit_animation(command, p)
    raise ValueError(f"Unsupported rig command: {command}")


def simple_animation(rig):
    animation = rig.animation_data
    if rig.constraints or any(b.constraints for b in rig.pose.bones) or (animation and
            (animation.drivers or any(not t.mute or t.is_solo for t in animation.nla_tracks) or animation.use_tweak_mode)):
        raise ValueError("Structured clip tools require a rig without constraints, drivers or active NLA tracks")


def action_slot(rig, action, identifier=None):
    from .domains import curves
    owner = action.get("mcpblender_target")
    if owner and owner != rig.name:
        raise ValueError("Clip was created for a different armature")
    slots = [s for s in getattr(action, "slots", ()) if s.target_id_type == "OBJECT"]
    if slots:
        matches = [s for s in slots if s.identifier == identifier] if identifier else [s for s in slots if s.name_display == rig.name]
        if not matches and identifier is None and len(slots) == 1:
            matches = slots
        if len(matches) != 1:
            raise ValueError("Choose a compatible action slot explicitly using slot")
        slot = matches[0]
    else:
        if identifier:
            raise ValueError("Legacy action has no selectable slots")
        slot = None
    channels = list(curves(action, slot))
    if not channels:
        raise ValueError("Clip has no animation channels")
    for channel in channels:
        # Skeletal tools intentionally exclude object transforms/custom properties.
        if not channel.data_path.startswith("pose.bones["):
            raise ValueError("Clip contains non-skeletal channels; use a specialized animation workflow")
        try:
            rig.path_resolve(channel.data_path)
        except (ValueError, KeyError) as exc:
            raise ValueError("Clip references bones/properties absent from the target rig") from exc
    return slot


def animation_snapshot(rig):
    anim = rig.animation_data
    return {"had_animation": anim is not None, "action": anim.action if anim else None,
            "slot": getattr(anim, "action_slot", None) if anim else None,
            "settings": {key: getattr(anim, key) for key in ("action_blend_type", "action_extrapolation", "action_influence", "use_nla")} if anim else {},
            "pose": {b.name: {key: list(getattr(b, key)) for key in
                     ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale")}
                     for b in rig.pose.bones},
            "frame": bpy.context.scene.frame_current, "subframe": bpy.context.scene.frame_subframe}


def reset_pose(rig):
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)


def assign_action(rig, action, slot=None):
    anim = rig.animation_data_create()
    anim.action = action
    if slot is not None:
        anim.action_slot = slot
    anim.action_blend_type, anim.action_extrapolation, anim.action_influence = "REPLACE", "HOLD", 1
    anim.use_nla = False
    reset_pose(rig)
    bpy.context.scene.frame_set(bpy.context.scene.frame_current, subframe=bpy.context.scene.frame_subframe)


def restore_animation(rig, saved):
    if saved["had_animation"]:
        anim = rig.animation_data_create()
        anim.action = saved["action"]
        if saved["slot"] is not None:
            anim.action_slot = saved["slot"]
        for key, value in saved["settings"].items():
            setattr(anim, key, value)
    else:
        rig.animation_data_clear()
    for bone in rig.pose.bones:
        for key, value in saved["pose"][bone.name].items():
            setattr(bone, key, value)
    bpy.context.scene.frame_set(saved["frame"], subframe=saved["subframe"])
    bpy.context.view_layer.update()


def inspect_animation(p):
    from .domains import curves
    rig = armature_object(p["name"])
    actions = sorted(bpy.data.actions, key=lambda a: a.name)
    offset, limit = p.get("clip_offset", 0), p.get("clip_limit", 50)
    active = rig.animation_data.action if rig.animation_data else None
    action = bpy.data.actions[p["clip"]] if p.get("clip") else active
    detail = None
    if action:
        slot = action_slot(rig, action, p.get("slot"))
        channels = list(curves(action, slot))
        detail = {"name": action.name, "slot": slot.identifier if slot else None,
                  "frame_range": list(action.frame_range), "total_tracks": len(channels),
                  "tracks": [{"path": c.data_path, "component": c.array_index, "keys": len(c.keyframe_points),
                              "first_key": list(c.keyframe_points[0].co) if c.keyframe_points else None,
                              "last_key": list(c.keyframe_points[-1].co) if c.keyframe_points else None}
                             for c in channels[:p.get("track_limit", 100)]]}
    return {"name": rig.name, "active_clip": active.name if active else None, "detail": detail,
            "total_clips": len(actions), "next_clip_offset": offset + limit if offset + limit < len(actions) else None,
            "clips": [{"name": a.name, "frame_range": list(a.frame_range), "managed_target": a.get("mcpblender_target"),
                       "persistent": a.use_fake_user,
                       "slots": [{"identifier": s.identifier, "target_type": s.target_id_type} for s in getattr(a, "slots", ())][:100]}
                      for a in actions[offset:offset + limit]]}


def edit_animation(command, p):
    from .domains import curves
    rig = armature_object(p["name"], edit=True)
    simple_animation(rig)
    if command == "set_active_clip":
        action = bpy.data.actions[p["clip"]] if p.get("clip") else None
        slot = action_slot(rig, action, p.get("slot")) if action else None
        assign_action(rig, action, slot)
        return {"name": rig.name, "active_clip": action.name if action else None, "pose_reset": True}
    for track in p["tracks"]:
        bone = rig.pose.bones.get(track["bone"])
        if bone is None:
            raise ValueError(f"Bone not found: {track['bone']}")
        if track["property"] == "rotation_degrees" and bone.rotation_mode != "XYZ":
            raise ValueError("Authored rotation tracks require XYZ bone rotation mode")
    creating = command == "create_animation_clip"
    if creating and p["clip"] in bpy.data.actions:
        raise ValueError("Clip name already exists; use set_pose_keyframes to edit it")
    action = None
    if not creating:
        action = bpy.data.actions[p["clip"]]
        if action.get("mcpblender_target") != rig.name or action.library:
            raise ValueError("Only clips created for this rig can be edited by set_pose_keyframes")
        owned_slot = action_slot(rig, action)
        for other in bpy.data.objects:
            if other == rig or not other.animation_data:
                continue
            anim = other.animation_data
            if (anim.action == action and anim.action_slot == owned_slot) or any(
                    s.action == action and s.action_slot == owned_slot for t in anim.nla_tracks for s in t.strips):
                raise ValueError("Skeletal slot is shared by another object; shared animation editing is refused")
    saved = animation_snapshot(rig)
    succeeded = False
    try:
        if creating:
            action = bpy.data.actions.new(p["clip"])
            action.use_fake_user = True
            action["mcpblender_target"] = rig.name
            rig.animation_data_create().action = action
            slot = action.slots.new(id_type="OBJECT", name=rig.name) if hasattr(action, "slots") else None
            if slot is not None:
                rig.animation_data.action_slot = slot
            reset_pose(rig)
        else:
            slot = action_slot(rig, action)
            assign_action(rig, action, slot)
        paths, touched = {}, {}
        for track in p["tracks"]:
            bone = rig.pose.bones[track["bone"]]
            prop = "rotation_euler" if track["property"] == "rotation_degrees" else track["property"]
            path = bone.path_from_id(prop)
            paths[path] = prop
            touched[path] = {key["frame"] for key in track["keyframes"]}
            for key in sorted(track["keyframes"], key=lambda k: k["frame"]):
                value = [math.radians(v) for v in key["value"]] if prop == "rotation_euler" else key["value"]
                setattr(bone, prop, value)
                if not bone.keyframe_insert(data_path=prop, frame=key["frame"], group=bone.name):
                    raise RuntimeError("Blender refused a pose keyframe")
        for channel in curves(action, slot):
            if channel.data_path in paths:
                for key in channel.keyframe_points:
                    if key.co.x in touched[channel.data_path]:
                        key.interpolation = p.get("interpolation", "LINEAR")
        succeeded = True
    finally:
        restore_animation(rig, saved)
        if creating and not succeeded and action:
            bpy.data.actions.remove(action)
    if creating and p.get("activate", True):
        assign_action(rig, action, slot)
    elif saved["action"] == action:
        reset_pose(rig)
        bpy.context.scene.frame_set(saved["frame"], subframe=saved["subframe"])
    return inspect_animation({"name": rig.name, "clip": action.name})


def sample_animation(p):
    from .assets import inspect_mesh
    from .bridge import check_cancelled, report_progress
    rig = armature_object(p["name"], edit=True)
    simple_animation(rig)
    action = bpy.data.actions[p["clip"]] if p.get("clip") else (rig.animation_data.action if rig.animation_data else None)
    slot = action_slot(rig, action, p.get("slot")) if action else None
    names = p.get("bones") if p.get("bones") is not None else [b.name for b in rig.data.bones][:32]
    if any(name not in rig.pose.bones for name in names):
        raise ValueError("Sampling requested an unknown bone")
    for name in p.get("meshes", []):
        mesh = mesh_object(name)
        if not any(m.type == "ARMATURE" and m.object == rig for m in mesh.modifiers):
            raise ValueError("Sampled meshes must be bound to the target armature")
    saved, samples = animation_snapshot(rig), []
    try:
        if action:
            assign_action(rig, action, slot)
        for index, frame in enumerate(p["frames"]):
            check_cancelled()
            bpy.context.scene.frame_set(frame)
            posed = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            meshes = []
            for name in p.get("meshes", []):
                info = inspect_mesh({"name": name, "sample_limit": p.get("sample_limit", 16)})
                meshes.append({key: info[key] for key in ("name", "vertices", "world_bounds", "geometry_sha256", "vertex_samples", "non_finite_vertices")})
            samples.append({"frame": frame, "bones": [{"name": name,
                "head_world": list(posed.matrix_world @ posed.pose.bones[name].head),
                "tail_world": list(posed.matrix_world @ posed.pose.bones[name].tail)} for name in names], "meshes": meshes})
            report_progress({"phase": "sampling animation", "fraction": (index + 1) / len(p["frames"]), "frame": frame})
    finally:
        restore_animation(rig, saved)
    return {"name": rig.name, "clip": action.name if action else None, "samples": samples,
            "restored_frame": saved["frame"], "restored_clip": saved["action"].name if saved["action"] else None}


@contextmanager
def export_managed_clips(selected_only=True):
    """Expose only each exported rig's managed clips to native ACTIONS export.

    Muted temporary NLA tracks are discovered by glTF without blending into the
    live pose. Disable native single-armature broadcast separately at export.
    """
    objects = list(bpy.context.selected_objects if selected_only else bpy.context.scene.objects)
    rigs = {o for o in objects if o.type == "ARMATURE"}
    rigs.update(m.object for o in objects if o.type == "MESH" for m in o.modifiers
                if m.type == "ARMATURE" and m.object)
    saved = [(rig, animation_snapshot(rig)) for rig in rigs]
    tracks = []
    try:
        for rig in rigs:
            existing = {rig.animation_data.action} if rig.animation_data else set()
            if rig.animation_data:
                existing.update(s.action for t in rig.animation_data.nla_tracks for s in t.strips)
            for action in bpy.data.actions:
                if action.get("mcpblender_target") != rig.name or action in existing:
                    continue
                slot = action_slot(rig, action)
                track = rig.animation_data_create().nla_tracks.new()
                tracks.append((rig, track))
                track.name, track.mute = action.name, True
                strip = track.strips.new(action.name, int(math.floor(action.frame_range[0])), action)
                if slot is not None:
                    strip.action_slot = slot
        yield
    finally:
        for rig, track in reversed(tracks):
            rig.animation_data.nla_tracks.remove(track)
        for rig, state in saved:
            restore_animation(rig, state)
