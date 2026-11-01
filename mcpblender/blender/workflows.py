"""Trusted structured workflow shims; no caller-provided Python."""
import math

import bpy
from mathutils import Vector

from . import assets

COMMANDS = {"setup_preview_stage", "asset_report", "preflight_game_asset", "export_objects_glb"}


def preflight_game_asset(p):
    edits = p["apply_rotation_scale"] or p["generate_uv"] or p["triangulate"]
    for name in p["objects"]:
        obj = assets.mesh_object(name, edit=edits)
        if edits or p.get("export_path"):
            if obj.find_armature() or obj.data.shape_keys:
                raise ValueError(f"Use rig/morph-aware editing and delivery for {name}; game preparation edits/export support static meshes")
        if p["apply_rotation_scale"]:
            assets.require_static(obj)
        if (p["apply_rotation_scale"] or p["generate_uv"]) and obj.data.users > 1 and not p["make_single_user"]:
            raise ValueError(f"Shared mesh requires make_single_user=true: {name}")
        if p["apply_rotation_scale"] or p["generate_uv"]:
            if bpy.context.mode != "OBJECT" or bpy.context.view_layer.objects.get(name) is not obj or obj.hide_viewport or obj.hide_select or not obj.visible_get():
                raise ValueError(f"Edits require a visible, selectable target in the current view layer: {name}")
        if p["generate_uv"] and obj.data.uv_layers.get("UVMap") and not p["overwrite_uv"]:
            raise ValueError(f"UVMap already exists on {name}; set overwrite_uv=true explicitly")
        mod = obj.modifiers.get("MCP_GameTriangulate")
        if p["triangulate"] and mod and mod.type != "TRIANGULATE":
            raise ValueError(f"MCP_GameTriangulate has a different modifier type on {name}")
    return {"objects": p["objects"], "edits_requested": bool(edits), "checked": True,
            "note": "Preflight is not a transaction; later edits or file output can still fail."}


def asset_report(p):
    objects, total, warnings, errors = [], 0, [], []
    for name in dict.fromkeys(p["objects"]):
        obj = assets.mesh_object(name)
        report = assets.inspect_mesh({"name": name, "sample_limit": 0})
        total += report["triangles"]
        issues = []
        def issue(code, severity, message):
            item = {"object": name, "code": code, "severity": severity, "message": message}
            issues.append(item)
            (errors if severity == "error" else warnings).append(item)
        if not report["vertices"] or not report["polygons"]:
            issue("empty_geometry", "error", "No renderable mesh faces")
        if report["non_finite_vertices"]:
            issue("non_finite_geometry", "error", "Non-finite world coordinates")
        if not report["uv_layers"]:
            issue("missing_uv", "warning", "No UV map; required for UV-mapped textures")
        elif any(layer["non_finite_loops"] for layer in report["uv_layers"]):
            issue("invalid_uv", "error", "Non-finite UV coordinates")
        if not obj.data.materials or any(material is None for material in obj.data.materials):
            issue("missing_material", "warning", "Missing material slots or unassigned slot")
        if report["topology"]["non_manifold_edges"]:
            issue("non_manifold", "warning", "Open or non-manifold edges; suitability depends on the asset")
        if report["topology"]["degenerate_faces"]:
            issue("degenerate_faces", "warning", "Mesh contains zero-area faces")
        if any(abs(v - 1) > 1e-5 for v in obj.scale) or any(abs(v) > 1e-5 for v in obj.rotation_euler):
            issue("unapplied_transforms", "warning", "Review object rotation/scale before delivery")
        if any(mod.show_render != mod.show_viewport for mod in obj.modifiers):
            issue("modifier_visibility", "warning", "Viewport and render modifier settings differ")
        if obj.constraints or (obj.animation_data and obj.animation_data.drivers):
            issue("procedural_motion", "warning", "Constraints/drivers may need baking for engine delivery")
        objects.append({"name": name, "mesh": report,
                        "materials": [m.name if m else None for m in obj.data.materials],
                        "issues": issues})
    if total > p["triangle_budget"]:
        warnings.append({"object": None, "code": "triangle_budget", "severity": "warning",
                         "message": f"{total} evaluated triangles exceed budget {p['triangle_budget']}"})
    return {"objects": objects, "total_triangles": total, "triangle_budget": p["triangle_budget"],
            "errors": errors, "warnings": warnings, "needs_review": bool(errors or warnings),
            "scope": "Current viewport evaluation; review scale/axes, textures, collision and animation in the target engine. No engine certification."}


def export_objects_glb(p):
    from .bridge import dispatch as core_dispatch
    if bpy.context.mode != "OBJECT":
        raise ValueError("Object mode required for explicit-object export")
    targets = [bpy.data.objects.get(name) for name in p["objects"]]
    if any(obj is None or bpy.context.view_layer.objects.get(obj.name) is not obj or obj.hide_select or not obj.visible_get() for obj in targets):
        raise ValueError("Export targets must be visible/selectable objects in the current view layer")
    selected, active = list(bpy.context.selected_objects), bpy.context.view_layer.objects.active
    try:
        for obj in selected:
            obj.select_set(False)
        for obj in targets:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = targets[0]
        return core_dispatch("export_glb", {"path": p["path"], "selected_only": True,
                            "animations": p["animations"], "overwrite": p["overwrite"]})
    finally:
        for obj in list(bpy.context.selected_objects):
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active


def setup_preview_stage(p):
    scene = bpy.context.scene
    names = [p["prefix"] + suffix for suffix in ("_Camera", "_Key", "_Fill", "_Rim")]
    for name in names:
        if name in bpy.data.objects or name in bpy.data.cameras or name in bpy.data.lights:
            raise ValueError(f"Preview resource name already exists: {name}; choose a fresh prefix")
    # Inspect every input before creating anything; reject off-layer or empty assets.
    bounds = []
    for name in dict.fromkeys(p["objects"]):
        obj = assets.mesh_object(name)
        if bpy.context.view_layer.objects.get(name) is not obj:
            raise ValueError(f"Object is outside the current view layer: {name}")
        report = assets.inspect_mesh({"name": name, "sample_limit": 0})
        if not report["world_bounds"]:
            raise ValueError(f"Preview requires finite, nonempty meshes: {name}")
        bounds.append(report["world_bounds"])
    low = Vector([min(b["min"][i] for b in bounds) for i in range(3)])
    high = Vector([max(b["max"][i] for b in bounds) for i in range(3)])
    center, extent = (low + high) / 2, max((high - low).length, .001)
    if not all(math.isfinite(v) for v in (*center, extent, extent * extent)):
        raise ValueError("Preview bounds exceed supported finite range")
    created, data_created, previous_camera = [], [], scene.camera
    try:
        camera_data = bpy.data.cameras.new(names[0])
        data_created.append(camera_data)
        camera_data.type = p["projection"]
        camera = bpy.data.objects.new(names[0], camera_data)
        created.append(camera)
        scene.collection.objects.link(camera)
        fitted = assets.frame_objects({"name": camera.name, "objects": p["objects"],
                                       "view": p["view"], "margin": p["margin"], "active": True})
        if not fitted["fully_in_frame"]:
            raise ValueError("Could not fit every asset in the preview camera")
        lights = []
        for name, offset, energy, color in zip(names[1:],
                ((-1, -1.5, 2), (1.5, -.5, 1), (.5, 1.5, 1.5)),
                (180, 75, 210), ((1, .9, .8), (.75, .85, 1), (1, 1, 1))):
            data = bpy.data.lights.new(name, "AREA")
            data_created.append(data)
            data.energy, data.color, data.size = energy * extent**2 * p["strength"], color, extent
            obj = bpy.data.objects.new(name, data)
            created.append(obj)
            scene.collection.objects.link(obj)
            obj.location = center + Vector(offset) * extent
            obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
            lights.append({"name": name, "location": list(obj.location), "energy": data.energy,
                           "size": data.size, "color": list(data.color)})
        bpy.context.view_layer.update()
        return {"camera": fitted, "lights": lights, "created_objects": names,
                "previous_camera": previous_camera.name if previous_camera else None,
                "asset_center": list(center), "asset_extent": extent,
                "note": "Persistent stage; existing lights remain. Fit is for the current frame and render aspect."}
    except Exception:
        scene.camera = previous_camera
        for obj in reversed(created):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in reversed(data_created):
            (bpy.data.cameras if isinstance(data, bpy.types.Camera) else bpy.data.lights).remove(data)
        raise


def dispatch(command, p):
    if command == "setup_preview_stage":
        return setup_preview_stage(p)
    if command == "preflight_game_asset":
        return preflight_game_asset(p)
    if command == "asset_report":
        return asset_report(p)
    if command == "export_objects_glb":
        return export_objects_glb(p)
    raise ValueError(f"Unsupported workflow command: {command}")
