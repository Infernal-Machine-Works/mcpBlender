"""Structured Blender operations. Full bpy access remains available through Python."""
import json
from pathlib import Path

import bpy
from mathutils import Vector
from . import assets

COMMANDS = {"get_capabilities", "inspect_resources", "create_primitive", "create_mesh",
            "configure_material", "assign_material", "configure_light", "configure_camera",
            "set_keyframes", "configure_render", "render_animation", "render_video",
            "vse_add_strip", "vse_edit_strip", "vse_remove_strip", "vse_list"}
COMMANDS.update(assets.COMMANDS)


def strips(editor):
    return editor.strips if hasattr(editor, "strips") else editor.sequences


def strip_info(strip):
    return {"name": strip.name, "type": strip.type, "channel": strip.channel,
            "frame_start": strip.frame_final_start, "frame_end": strip.frame_final_end,
            "mute": strip.mute, "blend_type": strip.blend_type}


def curves(action, slot):
    if hasattr(action, "fcurves"):
        yield from action.fcurves
    else:
        for layer in action.layers:
            for strip in layer.strips:
                bag = strip.channelbag(slot)
                if bag:
                    yield from bag.fcurves


def dispatch(command, p):
    if command in assets.COMMANDS:
        return assets.dispatch(command, p)
    from .bridge import object_info
    scene = bpy.context.scene
    if command == "get_capabilities":
        return {"blender_version": bpy.app.version_string,
                "domains": ["mesh_modeling", "modifiers", "geometry_nodes", "materials", "shader_nodes",
                            "lighting", "cameras", "animation", "rigging", "compositing", "video_sequencer", "asset_import_export"],
                "structured_commands": sorted(COMMANDS), "operations_protocol": 2,
                "python_api": "All installed bpy functionality; discover optional add-ons before use",
                "render_engines": ["BLENDER_WORKBENCH", "BLENDER_EEVEE"] + (["CYCLES"] if hasattr(scene, "cycles") else []),
                "ffmpeg": bpy.app.build_options.codec_ffmpeg, "background": bpy.app.background}
    if command == "inspect_resources":
        return {"dirty": bpy.data.is_dirty, "filepath": bpy.data.filepath,
                "materials": [{"name": m.name, "nodes": [{"name": n.name, "type": n.bl_idname} for n in m.node_tree.nodes]
                               if m.node_tree else []} for m in bpy.data.materials],
                "images": [{"name": i.name, "path": i.filepath, "size": list(i.size), "packed": bool(i.packed_file)} for i in bpy.data.images],
                "actions": [{"name": a.name, "range": list(a.frame_range)} for a in bpy.data.actions],
                "render": {"engine": scene.render.engine, "width": scene.render.resolution_x,
                           "height": scene.render.resolution_y, "fps": scene.render.fps},
                "timeline": {"start": scene.frame_start, "end": scene.frame_end},
                "strips": [strip_info(s) for s in strips(scene.sequence_editor)] if scene.sequence_editor else []}
    if command == "create_primitive":
        if p["name"] in bpy.data.objects:
            raise ValueError("Object name already exists")
        operations = {"CUBE": bpy.ops.mesh.primitive_cube_add, "PLANE": bpy.ops.mesh.primitive_plane_add,
                      "UV_SPHERE": bpy.ops.mesh.primitive_uv_sphere_add, "CYLINDER": bpy.ops.mesh.primitive_cylinder_add,
                      "CONE": bpy.ops.mesh.primitive_cone_add}
        operations[p.get("kind", "CUBE")](location=p.get("location", [0, 0, 0]))
        obj = bpy.context.object
        obj.name, obj.scale = p["name"], p.get("scale", [1, 1, 1])
        bpy.context.view_layer.update()
        return object_info(obj, True)
    if command == "create_mesh":
        if p["name"] in bpy.data.objects:
            raise ValueError("Object name already exists")
        mesh = bpy.data.meshes.new(p["name"] + "Mesh")
        try:
            mesh.from_pydata(p["vertices"], [], p["faces"])
            if mesh.validate(verbose=False):
                raise ValueError("Invalid mesh input required Blender correction")
            mesh.update()
            obj = bpy.data.objects.new(p["name"], mesh)
            scene.collection.objects.link(obj)
        except Exception:
            bpy.data.meshes.remove(mesh)
            raise
        return object_info(obj, True)
    if command == "configure_material":
        material = bpy.data.materials.get(p["name"]) or bpy.data.materials.new(p["name"])
        material.use_nodes = True
        node = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if node is None:
            node = material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        for parameter, socket in (("base_color", "Base Color"), ("roughness", "Roughness"), ("metallic", "Metallic")):
            if p.get(parameter) is not None:
                node.inputs[socket].default_value = p[parameter]
        if p.get("base_color"):
            material.diffuse_color = p["base_color"]
        if p.get("image_path"):
            texture = material.node_tree.nodes.get("MCP_BaseColorTexture") or material.node_tree.nodes.new("ShaderNodeTexImage")
            texture.name = "MCP_BaseColorTexture"
            texture.image = bpy.data.images.load(p["image_path"], check_existing=True)
            if p.get("pack_image", True):
                texture.image.pack()
            coordinates = material.node_tree.nodes.get("MCP_TextureCoordinates") or material.node_tree.nodes.new("ShaderNodeTexCoord")
            coordinates.name = "MCP_TextureCoordinates"
            mapping = material.node_tree.nodes.get("MCP_TextureMapping") or material.node_tree.nodes.new("ShaderNodeMapping")
            mapping.name = "MCP_TextureMapping"
            mapping.inputs["Scale"].default_value = p.get("texture_scale", [1, 1, 1])
            material.node_tree.links.new(coordinates.outputs["UV"], mapping.inputs["Vector"])
            material.node_tree.links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
            material.node_tree.links.new(texture.outputs["Color"], node.inputs["Base Color"])
        return {"name": material.name, "nodes": [n.bl_idname for n in material.node_tree.nodes]}
    if command == "assign_material":
        material = bpy.data.materials[p["material"]]
        objects = [bpy.data.objects[name] for name in p["objects"]]
        if any(not hasattr(obj.data, "materials") for obj in objects):
            raise ValueError("All objects must support material slots")
        for obj in objects:
            if p.get("replace", True):
                obj.data.materials.clear()
            obj.data.materials.append(material)
        return {"material": material.name, "objects": p["objects"]}
    if command in ("configure_light", "configure_camera"):
        kind = "LIGHT" if command == "configure_light" else "CAMERA"
        obj = bpy.data.objects.get(p["name"])
        if obj and obj.type != kind:
            raise ValueError(f"Existing object is not a {kind}")
        if obj is None:
            data = bpy.data.lights.new(p["name"], p.get("type", "AREA")) if kind == "LIGHT" else bpy.data.cameras.new(p["name"])
            obj = bpy.data.objects.new(p["name"], data)
            scene.collection.objects.link(obj)
        obj.location = p.get("location", list(obj.location))
        if p.get("rotation_euler") is not None:
            obj.rotation_euler = p["rotation_euler"]
        if p.get("target") is not None:
            direction = Vector(p["target"]) - obj.location
            if direction.length == 0:
                raise ValueError("Target must differ from object location")
            obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        if kind == "LIGHT":
            obj.data.type, obj.data.energy = p.get("type", obj.data.type), p.get("energy", obj.data.energy)
            obj.data.color = p.get("color", list(obj.data.color))
            if obj.data.type == "AREA":
                obj.data.size = p.get("size", obj.data.size)
        else:
            obj.data.lens = p.get("lens", obj.data.lens)
            if p.get("active", True):
                scene.camera = obj
        bpy.context.view_layer.update()
        return object_info(obj, True)
    if command == "set_keyframes":
        obj, path = bpy.data.objects[p["name"]], p["data_path"]
        if path not in ("location", "rotation_euler", "scale"):
            raise ValueError("Use location, rotation_euler or scale; other properties are available through Python")
        original = list(getattr(obj, path))
        try:
            for key in p["keyframes"]:
                setattr(obj, path, key["value"])
                obj.keyframe_insert(data_path=path, frame=key["frame"])
            action = obj.animation_data.action
            for curve in curves(action, obj.animation_data.action_slot):
                if curve.data_path == path:
                    for key in curve.keyframe_points:
                        key.interpolation = p.get("interpolation", "LINEAR")
        finally:
            setattr(obj, path, original)
            scene.frame_set(scene.frame_current)
        return {"object": obj.name, "action": action.name, "frame_range": list(action.frame_range)}
    if command == "configure_render":
        if p.get("engine"):
            scene.render.engine = p["engine"]
        for key in ("resolution_x", "resolution_y", "fps"):
            if p.get(key) is not None:
                setattr(scene.render, key, p[key])
        for key in ("frame_start", "frame_end"):
            if p.get(key) is not None:
                setattr(scene, key, p[key])
        if p.get("samples") is not None and hasattr(scene, "cycles"):
            scene.cycles.samples = p["samples"]
        return {"engine": scene.render.engine, "frame_start": scene.frame_start, "frame_end": scene.frame_end}
    if command == "render_animation":
        return render_animation(p)
    if command == "render_video":
        return render_video(p)
    if command == "vse_list":
        return {"strips": [strip_info(s) for s in strips(scene.sequence_editor)] if scene.sequence_editor else []}
    if command == "vse_add_strip":
        editor = strips(scene.sequence_editor_create())
        if editor.get(p["name"]):
            raise ValueError("Strip name already exists")
        kind = p["kind"]
        common = dict(name=p["name"], channel=p.get("channel", 1), frame_start=p.get("frame_start", 1))
        if kind == "IMAGE":
            paths = [Path(path) for path in p["paths"]]
            if any(path.parent != paths[0].parent for path in paths):
                raise ValueError("An image sequence must use a single directory")
            strip = editor.new_image(filepath=str(paths[0]), **common)
            for path in paths[1:]:
                strip.elements.append(path.name)
            strip.frame_final_duration = p.get("duration", len(paths))
        elif kind == "MOVIE":
            strip = editor.new_movie(filepath=p["paths"][0], **common)
        elif kind == "SOUND":
            strip = editor.new_sound(filepath=p["paths"][0], **common)
        elif kind == "TEXT":
            duration = p.get("duration", 24)
            timing = {"length": duration} if "length" in editor.bl_rna.functions["new_effect"].parameters else {"frame_end": common["frame_start"] + duration}
            strip = editor.new_effect(type="TEXT", **timing, **common)
            strip.text = p.get("text", "")
            strip.font_size, strip.color = p.get("font_size", 36), p.get("color", [1, 1, 1, 1])
            strip.location = p.get("location", [0.5, 0.12])
        else:
            raise ValueError("Supported strip kinds: IMAGE, MOVIE, SOUND, TEXT")
        return strip_info(strip)
    if command in ("vse_edit_strip", "vse_remove_strip"):
        editor = strips(scene.sequence_editor_create())
        strip = editor[p["name"]]
        if command == "vse_remove_strip":
            editor.remove(strip)
            return {"removed": p["name"]}
        for key in ("frame_final_start", "frame_final_end", "channel", "mute", "blend_alpha", "text"):
            if key in p:
                setattr(strip, key, p[key])
        return strip_info(strip)
    raise ValueError(f"Unsupported domain command: {command}")


def render_animation(p):
    from .bridge import capture
    scene, directory = bpy.context.scene, Path(p["directory"])
    original = scene.frame_current
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths, step = [], p.get("frame_step", 1)
    frames = list(range(p["frame_start"], p["frame_end"] + 1, step))
    try:
        for number, frame in enumerate(frames):
            scene.frame_set(frame)
            path = directory / f"frame-{frame:06d}.png"
            capture("render_still", p | {"path": str(path)})
            paths.append(str(path))
            yield {"phase": "rendering", "frame": frame, "completed_frames": number + 1,
                   "total_frames": len(frames), "fraction": (number + 1) / len(frames)}
    finally:
        scene.frame_set(original)
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({"frames": paths, "fps": scene.render.fps, "frame_step": step}, indent=2))
    return {"path": str(manifest), "frames": paths, "fps": scene.render.fps, "frame_count": len(paths)}


def render_video(p):
    from .bridge import report_progress
    scene = bpy.context.scene
    render = scene.render
    settings = {key: getattr(render, key) for key in ("filepath", "resolution_x", "resolution_y", "resolution_percentage", "use_sequencer")}
    image_format = render.image_settings.file_format
    media_type = getattr(render.image_settings, "media_type", None)
    ffmpeg = {key: getattr(render.ffmpeg, key) for key in ("format", "codec", "constant_rate_factor", "audio_codec")}
    frame_settings = scene.frame_start, scene.frame_end, scene.frame_current
    path = Path(p["path"])
    if path.exists() and not p.get("overwrite", False):
        raise FileExistsError(path)

    def written(render_scene, *args):
        report_progress({"phase": "encoding video", "frame": render_scene.frame_current,
                         "completed_frames": render_scene.frame_current - scene.frame_start + 1,
                         "total_frames": scene.frame_end - scene.frame_start + 1})

    try:
        render.filepath = str(path)
        render.resolution_x, render.resolution_y, render.resolution_percentage = p.get("width", 640), p.get("height", 360), 100
        render.use_sequencer = True
        if media_type is not None:
            render.image_settings.media_type = "VIDEO"
        render.image_settings.file_format = "FFMPEG"
        render.ffmpeg.format, render.ffmpeg.codec = "MPEG4", "H264"
        render.ffmpeg.constant_rate_factor, render.ffmpeg.audio_codec = "MEDIUM", "AAC"
        scene.frame_start, scene.frame_end = p.get("frame_start", scene.frame_start), p.get("frame_end", scene.frame_end)
        bpy.app.handlers.render_write.append(written)
        result = bpy.ops.render.render(animation=True)
        if "FINISHED" not in result or not path.is_file():
            raise RuntimeError("Video render did not produce the requested MP4")
        return {"path": str(path), "frame_count": scene.frame_end - scene.frame_start + 1, "fps": render.fps}
    finally:
        if written in bpy.app.handlers.render_write:
            bpy.app.handlers.render_write.remove(written)
        for key, value in settings.items():
            setattr(render, key, value)
        if media_type is not None:
            render.image_settings.media_type = media_type
        render.image_settings.file_format = image_format
        for key, value in ffmpeg.items():
            setattr(render.ffmpeg, key, value)
        scene.frame_start, scene.frame_end = frame_settings[:2]
        scene.frame_set(frame_settings[2])
