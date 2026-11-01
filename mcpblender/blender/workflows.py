"""Trusted structured workflow shims; no caller-provided Python."""
import math

import bpy
from mathutils import Vector

from . import assets

COMMANDS = {"setup_preview_stage"}


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
    raise ValueError(f"Unsupported workflow command: {command}")
