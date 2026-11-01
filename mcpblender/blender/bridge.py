"""All socket polling and bpy operations run on Blender's main thread."""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import time
import traceback
import uuid
import types
from pathlib import Path

import bpy

from mcpblender.wire import call, encode
from mcpblender.broker import ensure_broker
from mcpblender.storage import require_space, estimate, snapshot
from mcpblender.wire import private_directory

_bridge = None
_APPLICATION_SESSION = uuid.uuid4().hex


class OperationCancelled(Exception):
    pass


def report_progress(progress):
    if _bridge and _bridge.active:
        if _bridge.active["command"] == "run_action":
            from .actions import decorate_progress
            progress = decorate_progress(progress)
        policy = _bridge.active.get("storage", {})
        progress = progress | {"storage": snapshot(policy.get("path", _bridge.path.parent),
                                                    reserve_bytes=policy.get("reserve_bytes", 256 * 1024**2))}
        reply = _bridge.control("progress", operation_id=_bridge.active["operation_id"], progress=progress)
        _bridge.cancel_requested = reply["cancel_requested"]


def check_cancelled():
    report_progress({"phase": "checking cancellation"})
    if _bridge.cancel_requested:
        raise OperationCancelled("Operation cancelled at a cooperative boundary; partial edits may remain")


def object_info(obj, detailed=False):
    result = {
        "name": obj.name, "type": obj.type,
        "parent": obj.parent.name if obj.parent else None,
        "location": list(obj.location), "rotation_euler": list(obj.rotation_euler),
        "rotation_mode": obj.rotation_mode, "scale": list(obj.scale),
        "dimensions": list(obj.dimensions), "visible": obj.visible_get(),
        "selected": obj.select_get(),
        "materials": [s.material.name if s.material else None for s in obj.material_slots],
    }
    if obj.type == "MESH":
        result["shape_keys"] = len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 0
    if detailed:
        result["matrix_world"] = [list(row) for row in obj.matrix_world]
        result["modifiers"] = [{"name": m.name, "type": m.type,
                                "show_viewport": m.show_viewport, "show_render": m.show_render}
                               for m in obj.modifiers]
        if obj.type == "MESH":
            result["mesh"] = dict(vertices=len(obj.data.vertices), edges=len(obj.data.edges),
                                   polygons=len(obj.data.polygons))
        result["collections"] = [c.name for c in obj.users_collection]
    return result


class LimitedOutput(io.StringIO):
    def write(self, text):
        remaining = max(0, 128_000 - self.tell())
        super().write(text[:remaining])
        return len(text)


@contextlib.contextmanager
def gltf_compatibility():
    """Work around Blender 5.0's unsupported-platform Draco probe, in memory only.

    The bundled add-on joins a None library name on FreeBSD even for uncompressed
    exports. Leave fixed versions untouched and restore the probe after the call.
    """
    from io_scene_gltf2.io.com import draco
    original = draco.dll_exists
    workaround = False
    if sys.platform.startswith("freebsd"):
        try:
            draco.dll_path()
        except TypeError:
            workaround = True
            draco.dll_exists = lambda quiet=False: False
    try:
        yield workaround
    finally:
        draco.dll_exists = original


def dispatch(command, params):
    from . import domains
    if command in domains.COMMANDS:
        return domains.dispatch(command, params)
    scene = bpy.context.scene
    if command == "status":
        return dict(protocol=1, blender_version=bpy.app.version_string, pid=os.getpid(),
                    background=bpy.app.background, session_id=_bridge.session_id,
                    filepath=bpy.data.filepath, dirty=bpy.data.is_dirty, scene=scene.name,
                    frame=scene.frame_current, checkpoint_path=_bridge.checkpoint_path,
                    checkpoint_error=_bridge.checkpoint_error,
                    recovered_from=os.environ.get("MCPBLENDER_RECOVER"))
    if command == "checkpoint":
        return _bridge.checkpoint(force=True)
    if command == "shutdown_blender":
        if params.get("_checkpoint_before_exit"):
            _bridge.checkpoint(force=True)
        if params.get("save_path"):
            dispatch("save_blend", {"path": params["save_path"], "overwrite": params.get("overwrite", False)})
        elif bpy.data.is_dirty and not params.get("discard_unsaved", False):
            raise RuntimeError("Project has unsaved changes; provide save_path or explicitly discard_unsaved")
        _bridge.control("desired_state", running=False)
        _bridge.quit_when_flushed = True
        return {"shutting_down": True, "pid": os.getpid()}
    if command == "get_scene_info":
        objects = sorted(scene.objects, key=lambda o: o.name)
        offset = max(0, int(params.get("offset", 0)))
        limit = min(1000, max(1, int(params.get("limit", 200))))
        return dict(scene=scene.name, filepath=bpy.data.filepath,
                    frame=scene.frame_current, frame_start=scene.frame_start, frame_end=scene.frame_end,
                    units=scene.unit_settings.system, unit_scale=scene.unit_settings.scale_length,
                    camera=scene.camera.name if scene.camera else None,
                    active_object=bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
                    objects=[object_info(o) for o in objects[offset:offset + limit]],
                    total_objects=len(objects), next_offset=offset + limit if offset + limit < len(objects) else None)
    if command == "get_object_info":
        return object_info(bpy.data.objects[params["name"]], detailed=True)
    if command == "execute_code":
        stdout, stderr = LimitedOutput(), LimitedOutput()
        namespace = {"bpy": bpy, "__name__": "__mcpblender__", "result": None,
                     "report_progress": report_progress, "check_cancelled": check_cancelled}
        error = None
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(compile(params["code"], "<mcpblender>", "exec"), namespace)
                json.dumps(namespace.get("result"), allow_nan=False)
            except OperationCancelled:
                raise
            except BaseException:
                error = traceback.format_exc(limit=12)
        return dict(success=error is None, stdout=stdout.getvalue(), stderr=stderr.getvalue(),
                    result=namespace.get("result") if error is None else None, traceback=error)
    if command == "select_objects":
        objects = [scene.objects[name] for name in params["names"]]
        for obj in scene.objects:
            obj.select_set(obj in objects)
        bpy.context.view_layer.objects.active = objects[0] if objects else None
        return {"selected": [o.name for o in objects]}
    if command == "set_object_transform":
        obj = scene.objects[params["name"]]
        for field in ("location", "rotation_euler", "scale"):
            value = params.get(field)
            if value is not None:
                if len(value) != 3:
                    raise ValueError(f"{field} requires three numbers")
                setattr(obj, field, value)
        bpy.context.view_layer.update()
        return object_info(obj, True)
    if command == "set_frame":
        scene.frame_set(int(params["frame"]))
        return {"frame": scene.frame_current}
    if command == "open_blend":
        result = bpy.ops.wm.open_mainfile(filepath=params["path"], load_ui=False, use_scripts=False)
        if "FINISHED" not in result:
            raise RuntimeError(f"Open failed: {result}")
        return {"path": bpy.data.filepath}
    if command == "save_blend":
        path = params["path"]
        if Path(path).exists() and not params.get("overwrite", False):
            raise FileExistsError(path)
        result = bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        if "FINISHED" not in result:
            raise RuntimeError(f"Save failed: {result}")
        return {"path": bpy.data.filepath}
    if command == "import_asset":
        path = params["path"]
        suffix = Path(path).suffix.lower()
        before = set(bpy.data.objects)
        if suffix in (".glb", ".gltf"):
            with gltf_compatibility():
                result = bpy.ops.import_scene.gltf(filepath=path)
        elif suffix == ".obj":
            result = bpy.ops.wm.obj_import(filepath=path)
        elif suffix == ".stl":
            result = bpy.ops.wm.stl_import(filepath=path)
        else:
            raise ValueError("Supported imports: .glb, .gltf, .obj, .stl")
        if "FINISHED" not in result:
            raise RuntimeError(f"Import failed: {result}")
        return {"path": path, "objects": sorted(o.name for o in set(bpy.data.objects) - before)}
    if command == "export_glb":
        from .rigging import export_managed_clips
        from .morphs import export_managed_clips as export_morph_clips
        path = params["path"]
        if params.get("selected_only", True) and not bpy.context.selected_objects:
            raise ValueError("No scene objects selected for export")
        if Path(path).exists() and not params.get("overwrite", False):
            raise FileExistsError(path)
        clips = export_managed_clips(params.get("selected_only", True)) if params.get("animations", True) else contextlib.nullcontext()
        morph_clips = export_morph_clips(params.get("selected_only", True)) if params.get("animations", True) else contextlib.nullcontext()
        with gltf_compatibility() as workaround, clips, morph_clips:
            result = bpy.ops.export_scene.gltf(filepath=path, check_existing=False, export_format="GLB",
                                              use_selection=params.get("selected_only", True),
                                              export_draco_mesh_compression_enable=False,
                                              export_yup=True, export_animations=params.get("animations", True),
                                              export_animation_mode="ACTIONS", export_anim_single_armature=False,
                                              export_merge_animation="ACTION", export_morph=True, export_morph_animation=True)
        if "FINISHED" not in result:
            raise RuntimeError(f"Export failed: {result}")
        return {"path": path, "bytes": Path(path).stat().st_size, "format": "GLB",
                "freebsd_draco_probe_workaround": workaround}
    if command in ("capture_viewport", "render_still"):
        return capture(command, params)
    raise ValueError(f"Unknown bridge command: {command}")


def capture(command, params):
    scene = bpy.context.scene
    render = scene.render
    width, height = int(params.get("width", 800)), int(params.get("height", 600))
    if not (16 <= width <= 4096 and 16 <= height <= 4096):
        raise ValueError("Image dimensions must be between 16 and 4096")
    policy = _bridge.active.get("storage", {}) if _bridge and _bridge.active else {}
    size, kind = estimate(command, params)
    require_space(params["path"], size, reserve_bytes=policy.get("reserve_bytes", 256 * 1024**2), estimate_kind=kind)
    settings = {k: getattr(render, k) for k in ("filepath", "resolution_x", "resolution_y", "resolution_percentage", "engine")}
    image_format = render.image_settings.file_format
    media_type = getattr(render.image_settings, "media_type", None)
    camera = scene.camera
    try:
        render.filepath = params["path"]
        render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
        if media_type is not None:
            render.image_settings.media_type = "IMAGE"
        render.image_settings.file_format = "PNG"
        if command == "capture_viewport":
            if bpy.app.background:
                raise RuntimeError("Viewport capture requires GUI Blender; use render_still in background mode")
            window = next((w for w in bpy.context.window_manager.windows if any(a.type == "VIEW_3D" for a in w.screen.areas)), None)
            if window is None:
                raise RuntimeError("No VIEW_3D area is open")
            area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                result = bpy.ops.render.opengl(write_still=True, view_context=True)
        else:
            if params.get("camera"):
                candidate = bpy.data.objects[params["camera"]]
                if candidate.type != "CAMERA":
                    raise ValueError("Requested camera is not a CAMERA object")
                scene.camera = candidate
            if not scene.camera:
                raise RuntimeError("Scene has no active camera")
            if params.get("engine"):
                render.engine = params["engine"]
            result = bpy.ops.render.render(write_still=True)
        if "FINISHED" not in result:
            raise RuntimeError(f"Image capture failed: {result}")
        return {"path": params["path"], "width": width, "height": height}
    finally:
        for key, value in settings.items():
            setattr(render, key, value)
        if media_type is not None:
            render.image_settings.media_type = media_type
        render.image_settings.file_format = image_format
        scene.camera = camera


class BridgeServer:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.session_id = _APPLICATION_SESSION
        self.active = None
        self.iterator = None
        self.pending = None
        self.cancel_requested = False
        self.last_status = 0
        self.claim_id = None
        self.pending_checkpointed = False
        self.quit_when_flushed = False
        self.checkpoint_path = os.environ.get("MCPBLENDER_RECOVER")
        self.checkpoint_error = None
        self.last_checkpoint = 0
        ensure_broker(self.path)

    def register(self):
        return call(self.path, "register_worker", {"status": dispatch("status", {})}, timeout=5)

    def control(self, command, **params):
        return call(self.path, command, params | {"session_id": self.session_id}, timeout=5)

    def finish(self, state, result=None, error=None):
        try:
            encode({"result": result})
        except (ValueError, TypeError):
            state, result, error = "failed", None, "Operation result is not JSON-compatible or exceeds 8 MiB; partial edits/files may remain"
        self.pending = dict(operation_id=self.active["operation_id"], state=state, result=result, error=error)
        self.pending_checkpointed = False
        self.iterator = None

    def checkpoint(self, force=False):
        interval = float(os.environ.get("MCPBLENDER_CHECKPOINT_INTERVAL", "30"))
        if not force and time.monotonic() - self.last_checkpoint < interval:
            return {"checkpoint_path": self.checkpoint_path}
        directory = self.path.parent / "recovery"
        private_directory(directory)
        previous_size = Path(self.checkpoint_path).stat().st_size if self.checkpoint_path and Path(self.checkpoint_path).is_file() else 0
        require_space(directory, max(16 * 1024**2, previous_size * 2),
                      reserve_bytes=int(os.environ.get("MCPBLENDER_RESERVE_BYTES", str(256 * 1024**2))), estimate_kind="checkpoint_size_heuristic")
        path = directory / f"checkpoint-{time.time_ns()}.blend"
        original = bpy.data.filepath
        result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, compress=True, check_existing=False)
        if "FINISHED" not in result or not path.is_file():
            raise RuntimeError("Recovery checkpoint save did not complete")
        record = {"checkpoint_path": str(path), "original_filepath": original, "saved_at": time.time(), "session_id": self.session_id}
        temporary = directory / "latest.json.tmp"
        with temporary.open("w") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(directory / "latest.json")
        self.checkpoint_path, self.checkpoint_error, self.last_checkpoint = str(path), None, time.monotonic()
        keep = int(os.environ.get("MCPBLENDER_CHECKPOINT_COPIES", "3"))
        for old in sorted(directory.glob("checkpoint-*.blend"), reverse=True)[keep:]:
            old.unlink()
        return record

    def maybe_checkpoint(self, force=False):
        try:
            return self.checkpoint(force)
        except Exception as exc:
            self.checkpoint_error = str(exc)
            return {"checkpoint_error": self.checkpoint_error}

    def tick(self):
        try:
            if self.pending:
                reads = {"get_scene_info", "get_object_info", "inspect_mesh", "inspect_shape_keys", "inspect_morph_animation", "sample_morph_animation", "inspect_rig", "inspect_animation", "sample_animation", "get_capabilities", "inspect_resources", "vse_list", "checkpoint", "shutdown_blender", "capture_viewport", "render_still", "render_animation", "render_video"}
                reads.update({"asset_report", "preflight_game_asset", "render_animation_preview"})
                mutates = self.active["command"] not in reads
                if self.active["command"] == "run_action":
                    mutates = any(step["command"] not in reads for step in self.active["params"]["steps"])
                if not self.pending_checkpointed and self.pending["state"] == "succeeded" and mutates:
                    recovery = self.maybe_checkpoint(force=self.active["command"] == "open_blend")
                    if isinstance(self.pending.get("result"), dict):
                        self.pending["result"]["recovery"] = recovery
                    self.pending_checkpointed = True
                self.control("complete", **self.pending, status=dispatch("status", {}))
                self.pending = self.active = None
                if self.quit_when_flushed:
                    self.close()
                    bpy.ops.wm.quit_blender()
                    return None
            if self.active is None:
                status = {}
                if time.monotonic() - self.last_status > 0.5:
                    status = dispatch("status", {})
                    self.last_status = time.monotonic()
                self.claim_id = self.claim_id or uuid.uuid4().hex
                self.active = self.control("claim", status=status, claim_id=self.claim_id)["operation"]
                self.claim_id = None
                self.cancel_requested = False
                if self.active:
                    try:
                        render_commands = {"render_still", "render_animation", "render_video", "render_animation_preview"}
                        renders = self.active["command"] in render_commands or (self.active["command"] == "run_action" and
                            any(s["command"] in render_commands for s in self.active["params"]["steps"]))
                        if renders and bpy.data.is_dirty:
                            self.maybe_checkpoint(force=True)
                        result = dispatch(self.active["command"], self.active["params"])
                        if isinstance(result, types.GeneratorType):
                            self.iterator = result
                        else:
                            self.finish("failed" if result.get("success") is False else "succeeded", result)
                    except OperationCancelled as exc:
                        self.finish("cancelled", getattr(exc, "result", None), error=str(exc))
                    except Exception as exc:
                        self.finish("failed", getattr(exc, "result", None), error=f"{type(exc).__name__}: {exc}")
            elif self.iterator:
                try:
                    report_progress(next(self.iterator))
                    if self.cancel_requested:
                        if self.active["command"] == "run_action":
                            self.iterator.throw(OperationCancelled("Cancelled at an action boundary; partial effects may remain"))
                        else:
                            self.iterator.close()
                            self.finish("cancelled", error="Cancelled between animation frames; completed files remain")
                except StopIteration as completed:
                    self.finish("succeeded", completed.value)
                except OperationCancelled as exc:
                    self.finish("cancelled", getattr(exc, "result", None), error=str(exc))
                except Exception as exc:
                    self.finish("failed", getattr(exc, "result", None), error=f"{type(exc).__name__}: {exc}")
        except (OSError, RuntimeError) as exc:
            # Preserve active/pending work; reconnecting must never repeat bpy edits.
            try:
                ensure_broker(self.path)
                self.register()
            except Exception:
                print(f"mcpBlender broker unavailable: {exc}", flush=True)
            return 0.5
        return 0.05

    def close(self):
        if self.iterator:
            self.iterator.close()
        try:
            self.control("detach_worker")
        except (OSError, RuntimeError):
            pass


def default_socket():
    return os.environ.get("MCPBLENDER_BRIDGE_SOCKET", str(Path.home() / ".local/state/mcpblender/bridge.sock"))


def start(path=None):
    global _bridge
    if _bridge is not None:
        return _bridge
    _bridge = BridgeServer(path or default_socket())
    _bridge.register()
    if not bpy.app.background:
        bpy.app.timers.register(tick, first_interval=0.02, persistent=True)
    return _bridge


def tick():
    return _bridge.tick() if _bridge else None


def stop():
    global _bridge
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    if _bridge:
        _bridge.close()
        _bridge = None
