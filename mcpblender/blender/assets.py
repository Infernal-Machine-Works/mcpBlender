"""Structured asset tools; no user-supplied Python or arbitrary RNA paths."""
from contextlib import contextmanager
import hashlib
import math
import struct

import bmesh
import bpy
from mathutils import Matrix, Vector

COMMANDS = {"inspect_mesh", "configure_modifier", "apply_modifier", "remove_modifier", "generate_uv"}
COMMANDS.update({"duplicate_object", "set_parent", "apply_transforms"})
MODIFIER_FIELDS = {
    "BEVEL": ("width", "segments", "limit_method", "angle_limit"),
    "SUBSURF": ("subdivision_type", "levels", "render_levels"),
    "DECIMATE": ("ratio", "use_collapse_triangulate"),
    "SOLIDIFY": ("thickness", "offset", "use_even_offset"),
    "TRIANGULATE": ("quad_method", "ngon_method"),
}


def local_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    if obj.library or obj.override_library or (obj.data and obj.data.library):
        raise ValueError("Editing linked or overridden data is unsupported; make a local asset first")
    if obj.mode != "OBJECT":
        raise ValueError("Object mode required; finish the current edit before using asset tools")
    return obj


def mesh_object(name, edit=False):
    obj = local_object(name) if edit else bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        raise ValueError(f"Expected a mesh object: {name}")
    if obj.mode != "OBJECT":
        raise ValueError("Object mode required; finish the current edit before using asset tools")
    return obj


def require_static(obj):
    animation = obj.animation_data
    if obj.constraints or (animation and (animation.action or animation.drivers or animation.nla_tracks)):
        raise ValueError("Static unconstrained transforms required; animated or constrained objects are unsupported")


@contextmanager
def active_object(obj):
    """Limit operators to one mesh, restoring the user's object selection."""
    layer = bpy.context.view_layer
    if bpy.context.mode != "OBJECT" or layer.objects.get(obj.name) is not obj:
        raise ValueError("Object mode and an object in the current view layer are required")
    if obj.hide_viewport or obj.hide_select or not obj.visible_get():
        raise ValueError("Target must be visible and selectable in the current view layer")
    selected, active = list(bpy.context.selected_objects), layer.objects.active
    try:
        for other in selected:
            other.select_set(False)
        obj.select_set(True)
        layer.objects.active = obj
        yield
    finally:
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for other in list(bpy.context.selected_objects):
            other.select_set(False)
        for other in selected:
            other.select_set(True)
        layer.objects.active = active


def finished(result):
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender operator did not finish: {result}")


def modifier_info(mod):
    return {"name": mod.name, "type": mod.type, "show_viewport": mod.show_viewport,
            "show_render": mod.show_render,
            "settings": {key: getattr(mod, key) for key in MODIFIER_FIELDS.get(mod.type, ())}}


def inspect_mesh(p):
    obj = mesh_object(p["name"])
    evaluated = p.get("evaluated", True)
    bpy.context.view_layer.update()
    target = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()) if evaluated else obj
    mesh = target.to_mesh(preserve_all_data_layers=True, depsgraph=bpy.context.evaluated_depsgraph_get()) if evaluated else obj.data
    try:
        mesh.calc_loop_triangles()
        digest = hashlib.sha256()
        low, high = [math.inf] * 3, [-math.inf] * 3
        samples, invalid = [], 0
        for vertex in mesh.vertices:
            co = target.matrix_world @ vertex.co
            finite = all(math.isfinite(v) for v in co)
            invalid += not finite
            digest.update(struct.pack("!3d", *co))
            if finite:
                low = [min(a, b) for a, b in zip(low, co)]
                high = [max(a, b) for a, b in zip(high, co)]
            if len(samples) < p.get("sample_limit", 16):
                samples.append({"index": vertex.index, "world": list(co) if finite else None})
        for face in mesh.polygons:
            digest.update(struct.pack("!I", len(face.vertices)))
            digest.update(struct.pack(f"!{len(face.vertices)}I", *face.vertices))
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            topology = {"boundary_edges": sum(e.is_boundary for e in bm.edges),
                        "non_manifold_edges": sum(not e.is_manifold for e in bm.edges),
                        "loose_vertices": sum(not v.link_edges for v in bm.verts),
                        "degenerate_faces": sum(f.calc_area() <= 1e-12 for f in bm.faces)}
        finally:
            bm.free()
        layers = []
        for layer in mesh.uv_layers:
            finite_uvs = [tuple(loop.uv) for loop in layer.data if all(math.isfinite(x) for x in loop.uv)]
            layers.append({"name": layer.name, "loops": len(layer.data),
                           "non_finite_loops": len(layer.data) - len(finite_uvs),
                           "bounds": {"min": [min(v[i] for v in finite_uvs) for i in range(2)],
                                      "max": [max(v[i] for v in finite_uvs) for i in range(2)]} if finite_uvs else None,
                           "zero_area_faces": sum(abs(sum(
                               layer.data[a].uv.x * layer.data[b].uv.y - layer.data[b].uv.x * layer.data[a].uv.y
                               for a, b in zip(list(face.loop_indices), list(face.loop_indices)[1:] + list(face.loop_indices)[:1]))) <= 1e-12
                               for face in mesh.polygons)})
        return {"name": obj.name, "evaluated": evaluated, "frame": bpy.context.scene.frame_current,
                "vertices": len(mesh.vertices), "edges": len(mesh.edges), "polygons": len(mesh.polygons),
                "triangles": len(mesh.loop_triangles), "non_finite_vertices": invalid,
                "world_bounds": {"min": low, "max": high} if len(mesh.vertices) and not invalid else None,
                "topology": topology, "uv_layers": layers, "vertex_samples": samples,
                "geometry_sha256": digest.hexdigest(), "modifiers": [modifier_info(m) for m in obj.modifiers],
                "evaluation": "Current viewport dependency graph; render-only modifier settings are not evaluated"}
    finally:
        if evaluated:
            target.to_mesh_clear()


def dispatch(command, p):
    if command == "inspect_mesh":
        return inspect_mesh(p)
    if command in {"duplicate_object", "set_parent", "apply_transforms"}:
        return edit_object(command, p)
    obj = mesh_object(p["name"], edit=True)
    if command == "generate_uv":
        return generate_uv(obj, p)
    mod = obj.modifiers.get(p["modifier"])
    if command == "configure_modifier":
        settings = p["settings"]
        kind = settings["type"]
        if kind not in MODIFIER_FIELDS:
            raise ValueError("Unsupported structured modifier type")
        if mod and mod.type != kind:
            raise ValueError("Existing modifier type must match; remove it explicitly to replace it")
        mod = mod or obj.modifiers.new(p["modifier"], kind)
        for key in MODIFIER_FIELDS[kind]:
            if key in settings:
                setattr(mod, key, settings[key])
        mod.show_viewport, mod.show_render = p.get("show_viewport", True), p.get("show_render", True)
        return {"name": obj.name, "modifier": modifier_info(mod)}
    if mod is None:
        raise ValueError(f"Modifier not found: {p['modifier']}")
    if command == "remove_modifier":
        obj.modifiers.remove(mod)
        return {"name": obj.name, "removed": p["modifier"]}
    if command == "apply_modifier":
        if obj.data.shape_keys:
            raise ValueError("Applying modifiers to meshes with shape keys is unsupported")
        if obj.data.users > 1 and not p.get("make_single_user", False):
            raise ValueError("Mesh data is shared; pass make_single_user=true to isolate this object")
        with active_object(obj):
            if obj.data.users > 1:
                obj.data = obj.data.copy()
            finished(bpy.ops.object.modifier_apply(modifier=mod.name))
        return inspect_mesh({"name": obj.name, "evaluated": False})
    raise ValueError(f"Unsupported asset command: {command}")


def generate_uv(obj, p):
    if not obj.data.polygons:
        raise ValueError("UV projection requires polygon faces")
    existing = obj.data.uv_layers.get(p.get("uv_map", "UVMap"))
    if existing and not p.get("overwrite", False):
        raise ValueError("UV map already exists; pass overwrite=true to replace its coordinates")
    if obj.data.users > 1 and not p.get("make_single_user", False):
        raise ValueError("Mesh data is shared; pass make_single_user=true to isolate this object")
    with active_object(obj):
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        mesh = obj.data
        layer = mesh.uv_layers.get(p.get("uv_map", "UVMap")) or mesh.uv_layers.new(name=p.get("uv_map", "UVMap"))
        mesh.uv_layers.active = layer
        flags = [[item.select for item in collection] for collection in (mesh.vertices, mesh.edges, mesh.polygons)]
        try:
            finished(bpy.ops.object.mode_set(mode="EDIT"))
            finished(bpy.ops.mesh.select_all(action="SELECT"))
            if p.get("method", "SMART") == "SMART":
                finished(bpy.ops.uv.smart_project(angle_limit=math.radians(p.get("angle_limit_degrees", 66)),
                    island_margin=p.get("island_margin", .02), correct_aspect=False, scale_to_bounds=True))
            else:
                finished(bpy.ops.uv.cube_project(cube_size=p.get("cube_size", 1), correct_aspect=False,
                                               clip_to_bounds=False, scale_to_bounds=True))
        finally:
            if obj.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for collection, values in zip((mesh.vertices, mesh.edges, mesh.polygons), flags):
                for item, selected in zip(collection, values):
                    item.select = selected
    return inspect_mesh({"name": obj.name, "evaluated": False})


def edit_object(command, p):
    from .bridge import object_info
    obj = local_object(p["name"])
    bpy.context.view_layer.update()
    if command == "duplicate_object":
        if p["new_name"] in bpy.data.objects:
            raise ValueError("Object name already exists")
        duplicate = obj.copy()
        if obj.data and not p.get("linked_data", False):
            duplicate.data = obj.data.copy()
        duplicate.name = p["new_name"]
        bpy.context.collection.objects.link(duplicate)
        world = obj.matrix_world.copy()
        world.translation += Vector(p.get("offset", [0, 0, 0]))
        duplicate.matrix_world = world
        bpy.context.view_layer.update()
        return object_info(duplicate, True) | {"linked_data": bool(obj.data and duplicate.data is obj.data),
            "animation_actions_shared": bool(obj.animation_data)}
    require_static(obj)
    if command == "set_parent":
        parent = local_object(p["parent"]) if p.get("parent") else None
        ancestor = parent
        while ancestor:
            if ancestor is obj:
                raise ValueError("Parent relationship would create a cycle")
            ancestor = ancestor.parent
        if parent and abs(parent.matrix_world.determinant()) < 1e-12:
            raise ValueError("Parent world transform must be invertible")
        if obj.parent and obj.parent_type != "OBJECT":
            raise ValueError("Only object parenting is supported; bone/vertex parenting requires a specialized tool")
        world, basis = obj.matrix_world.copy(), obj.matrix_basis.copy()
        inherited = obj.parent.matrix_world @ obj.matrix_parent_inverse if obj.parent else Matrix.Identity(4)
        if parent is None and p.get("keep_world", True):
            location, rotation, scale = world.decompose()
            representable = Matrix.LocRotScale(location, rotation, scale)
            if any(abs(world[i][j] - representable[i][j]) > 1e-5 for i in range(4) for j in range(4)):
                raise ValueError("Cannot clear parent while preserving a sheared world transform; remove shear first")
        obj.parent = parent
        obj.parent_type = "OBJECT"
        obj.matrix_parent_inverse = Matrix.Identity(4)
        if p.get("keep_world", True) and parent:
            obj.matrix_parent_inverse = parent.matrix_world.inverted() @ inherited
            obj.matrix_basis = basis
        elif p.get("keep_world", True):
            obj.matrix_world = world
        else:
            obj.matrix_basis = basis
        bpy.context.view_layer.update()
        return object_info(obj, True)
    mesh_object(obj.name, edit=True)
    if obj.data.shape_keys:
        raise ValueError("Applying transforms to shape-key meshes is not yet supported")
    if obj.data.users > 1 and not p.get("make_single_user", False):
        raise ValueError("Mesh data is shared; pass make_single_user=true to isolate this object")
    # Restore descendants from parent to child after applying the native operator.
    descendants = []
    def collect(parent):
        for child in parent.children:
            require_static(child)
            local_object(child.name)
            if child.parent_type != "OBJECT" or abs(parent.matrix_world.determinant()) < 1e-12:
                raise ValueError("Descendants require object parenting and invertible parent transforms")
            descendants.append((child, child.matrix_basis.copy(), parent.matrix_world @ child.matrix_parent_inverse))
            collect(child)
    collect(obj)
    with active_object(obj):
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        finished(bpy.ops.object.transform_apply(location=p.get("location", False),
            rotation=p.get("rotation", True), scale=p.get("scale", True), properties=False))
        bpy.context.view_layer.update()
        for child, basis, inherited in descendants:
            child.matrix_parent_inverse = child.parent.matrix_world.inverted() @ inherited
            child.matrix_basis = basis
            bpy.context.view_layer.update()
    return object_info(obj, True)
