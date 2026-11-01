"""Structured skeleton construction, skinning and animation inspection."""
import math

import bpy
from mathutils import Matrix

from .assets import active_object, finished, local_object, mesh_object

COMMANDS = {"create_armature", "bind_mesh", "inspect_rig"}


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
    raise ValueError(f"Unsupported rig command: {command}")
