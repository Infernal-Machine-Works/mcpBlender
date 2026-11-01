"""MCP surface, per-key policy, artifacts and FreeCAD-compatible remote auth."""
from __future__ import annotations

import argparse
import asyncio
import logging
import hashlib
import os
import sys
import secrets
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

from fastmcp import FastMCP
from fastmcp.server.transforms import Transform
from fastmcp.utilities.types import Image

from . import __version__
from .config import APIKey, Config, Policy, generate_key, load_config, match_key, write_config
from .launcher import Launcher
from .wire import call
from .persistence import Records
from .commands import COMMANDS, DOMAIN_COMMANDS
from .storage import snapshot, require_space, estimate
from .broker import ensure_broker
from .actions import Registry

ACTIVE_KEY: ContextVar[APIKey | None] = ContextVar("mcpblender_key", default=None)
CAPABILITIES = {
    "execute_code": ("allow_code_execution", "allow_scene_edit"),
    "start_blender": ("allow_start_blender",),
    "restart_bridge": ("allow_start_blender",),
    "select_objects": ("allow_scene_edit",),
    "set_object_transform": ("allow_scene_edit",),
    "set_frame": ("allow_scene_edit",),
    "open_blend": ("allow_scene_edit",),
    "save_blend": ("allow_library_write",),
    "import_asset": ("allow_scene_edit",),
    "export_glb": ("allow_library_write",),
    "capture_viewport": ("allow_snapshots",),
    "render_still": ("allow_snapshots",),
    "get_image": ("allow_snapshots",),
}
CAPABILITIES.update({name: spec[1] for name, spec in COMMANDS.items()})


class AccessTransform(Transform):
    def __init__(self, state):
        self.state = state

    async def list_tools(self, tools):
        return [tool for tool in tools if self.state.allowed(tool.name)]

    async def get_tool(self, name, call_next, *, version=None):
        if not self.state.allowed(name):
            return None
        return await call_next(name, version=version)


class State:
    def __init__(self, config: Config):
        self.config = config
        self.actions = Registry(config.action_files)
        self.launcher = Launcher(config)
        self.launcher.frontend_managed = True
        self.artifacts = Records(Path(config.bridge.socket_path).expanduser().parent)

    def policy(self) -> Policy:
        if self.config.mode == "stdio":
            return self.config.stdio
        key = ACTIVE_KEY.get()
        if key is None:
            raise PermissionError("No authenticated API key in request context")
        return key

    def owner(self):
        policy = self.policy()
        return policy.id if isinstance(policy, APIKey) else "stdio"

    def allowed(self, name):
        policy = self.policy()
        return all(getattr(policy, capability) for capability in CAPABILITIES.get(name, ()))

    def check(self, name):
        if not self.allowed(name):
            raise PermissionError(f"Active policy does not allow {name}")

    def library_path(self, path: str, *, write=False) -> Path:
        root = Path(self.policy().library_root).expanduser().resolve()
        candidate = Path(path).expanduser()
        candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not candidate.is_relative_to(root) or candidate == root:
            raise ValueError("File path must be inside the active library_root")
        if write:
            candidate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    async def control(self, action, **params):
        try:
            return await asyncio.to_thread(call, self.config.bridge.socket_path, action, params, 5)
        except (OSError, RuntimeError) as exc:
            if isinstance(exc, RuntimeError) and "Bridge disconnected before a complete response" not in str(exc):
                raise
            await asyncio.to_thread(ensure_broker, self.config.bridge.socket_path, sys.executable)
            return await asyncio.to_thread(call, self.config.bridge.socket_path, action, params, 5)

    async def submit(self, command, params, request_id=None):
        self.check(command)
        request_id = request_id or uuid.uuid4().hex
        params = self.prepare(command, params, request_id)
        path = params.get("path") or params.get("directory") or self.policy().library_root
        size, kind = estimate(command, params)
        storage = dict(self.config.storage.model_dump(), path=str(path), estimated_bytes=size, estimate_kind=kind)
        request = dict(command=command, params=params, owner=self.owner(), request_id=request_id, storage=storage)
        try:
            return await self.control("submit", **request)
        except RuntimeError as exc:
            if "No Blender worker attached" not in str(exc) or not self.config.launch.auto_start or command == "shutdown_blender" or not self.allowed("start_blender"):
                raise
            await asyncio.to_thread(self.launcher.start)
            return await self.control("submit", **request)

    def prepare(self, command, params, request_id=None):
        if command not in COMMANDS:
            raise ValueError("Unknown operation command; use get_capabilities for supported operations")
        params = COMMANDS[command][0].model_validate(params).model_dump(exclude_none=True)
        if command == "run_action":
            return self.compile_action(params["action"], params["inputs"], request_id or uuid.uuid4().hex)
        stable = hashlib.sha256((self.owner() + command + request_id).encode()).hexdigest()[:32] if request_id else uuid.uuid4().hex
        if command in ("render_still", "capture_viewport", "render_video"):
            extension = ".mp4" if command == "render_video" else ".png"
            params["path"] = str(self.library_path(params.get("path", f"artifacts/{stable}{extension}"), write=True))
            if Path(params["path"]).suffix.lower() != extension:
                raise ValueError(f"Output must end in {extension}")
        if command == "render_animation":
            params["directory"] = str(self.library_path(params.get("directory", f"artifacts/{stable}"), write=True))
        if command in ("open_blend", "import_asset", "save_blend", "export_glb", "export_objects_glb"):
            params["path"] = str(self.library_path(params["path"], write=command in ("save_blend", "export_glb", "export_objects_glb")))
            extension = {"save_blend": ".blend", "open_blend": ".blend", "export_glb": ".glb", "export_objects_glb": ".glb"}.get(command)
            if extension and Path(params["path"]).suffix.lower() != extension:
                raise ValueError(f"File must end in {extension}")
        if command == "configure_material" and params.get("image_path"):
            params["image_path"] = str(self.library_path(params["image_path"]))
        if command == "vse_add_strip":
            params["paths"] = [str(self.library_path(path)) for path in params["paths"]]
        if command == "shutdown_blender" and params.get("save_path"):
            self.check("save_blend")
            params["save_path"] = str(self.library_path(params["save_path"], write=True))
            if Path(params["save_path"]).suffix.lower() != ".blend":
                raise ValueError("Shutdown save_path must end in .blend")
        if command in ("download_asset", "upload_asset", "request_asset_api"):
            source = self.config.asset_sources.get(params["source"])
            if source is None:
                raise ValueError("Asset source is not configured")
            base = source.base_url.rstrip("/") + "/"
            url = urljoin(base, params.pop("resource"))
            parsed, expected = urlsplit(url), urlsplit(base)
            if parsed.scheme not in ("http", "https") or parsed[:2] != expected[:2] or parsed.username or parsed.password:
                raise ValueError("Asset resource must remain on the configured HTTP(S) origin")
            params.update(url=url, headers=source.headers, timeout_seconds=source.timeout_seconds, max_bytes=source.max_bytes)
            if command != "request_asset_api":
                params["path"] = str(self.library_path(params["path"], write=command == "download_asset"))
        return params

    def compile_action(self, name, inputs, request_id):
        plan = self.actions.compile(name, inputs)
        for step in plan["steps"]:
            self.check(step["command"])
        steps = []
        for step in plan["steps"]:
            params = self.prepare(step["command"], step["params"], request_id + ":" + step["id"])
            size, kind = estimate(step["command"], params)
            path = params.get("path") or params.get("directory") or self.policy().library_root
            storage = dict(self.config.storage.model_dump(), path=str(path), estimated_bytes=size, estimate_kind=kind)
            steps.append(step | {"params": params, "storage": storage})
        return plan | {"steps": steps}

    async def operation(self, identifier):
        operation = await self.control("get_operation", operation_id=identifier, owner=self.owner())
        result = operation.get("result")
        if isinstance(result, dict) and operation["command"] == "run_action":
            for step in result.get("steps", []):
                value = step.get("result")
                if isinstance(value, dict):
                    step["result"] = self.enrich_result(value, identifier + ":" + step["id"])
                partial = step.get("partial_output")
                if partial and partial.get("existing_frames"):
                    partial["preview_artifacts"] = [self.artifact(p, "image", hashlib.sha256((identifier + p).encode()).hexdigest()[:32])
                                                    for p in partial["existing_frames"][:3] if Path(p).is_file()]
        elif operation["state"] == "succeeded" and isinstance(result, dict):
            operation["result"] = self.enrich_result(result, identifier)
        return operation

    def enrich_result(self, result, identifier):
        if isinstance(result, dict):
            if result.get("path") and Path(result["path"]).is_file():
                suffix = Path(result["path"]).suffix.lower()
                kind = {".png": "image", ".glb": "model", ".mp4": "video", ".json": "manifest"}.get(suffix, "file")
                artifact_id = hashlib.sha256((identifier + result["path"]).encode()).hexdigest()[:32]
                result = result | self.artifact(result["path"], kind, artifact_id)
            if result.get("frames"):
                frames = result["frames"]
                previews = []
                for path in dict.fromkeys([frames[0], frames[len(frames) // 2], frames[-1]]):
                    artifact_id = hashlib.sha256((identifier + path).encode()).hexdigest()[:32]
                    previews.append(self.artifact(path, "image", artifact_id))
                result["preview_artifacts"] = previews
        return result

    async def rpc(self, command, **params):
        if command == "status":
            return await self.control("status")
        operation = await self.submit(command, params)
        deadline = time.monotonic() + self.config.bridge.timeout_seconds
        while True:
            operation = await self.operation(operation["operation_id"])
            if operation["state"] in ("succeeded", "failed", "cancelled", "interrupted"):
                if isinstance(operation.get("result"), dict):
                    return operation["result"] | {"operation_id": operation["operation_id"]}
                raise RuntimeError(f"Operation {operation['operation_id']} {operation['state']}: {operation.get('error')}")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Operation {operation['operation_id']} is still {operation['state']}; poll get_operation. Execution was not cancelled.")
            await asyncio.sleep(0.05)

    def artifact(self, path, kind="image", identifier=None):
        identifier = identifier or uuid.uuid4().hex
        record = self.artifacts.get(identifier) or {"path": str(path), "owner": self.owner(), "kind": kind,
                  "token": secrets.token_urlsafe(32), "expires": time.time() + 600}
        if record["expires"] <= time.time():
            record.update(token=secrets.token_urlsafe(32), expires=time.time() + 600)
        self.artifacts[identifier] = record
        result = {"artifact_id": identifier, "path": str(path), "kind": kind}
        prefix = self.config.remote_server.url_prefix
        if self.config.mode == "remotehttp" and prefix:
            url = urljoin(prefix.rstrip("/") + "/", f"artifacts/{identifier}")
            result.update(download_url=url, download_url_with_token=f"{url}?token={record['token']}",
                          token_expires_at=record["expires"])
        return result

    def resolve_artifact(self, identifier, *, token=None):
        record = self.artifacts.get(identifier)
        if record is None:
            raise FileNotFoundError("Artifact not found")
        if token is not None:
            if time.time() >= record["expires"] or not secrets.compare_digest(token, record["token"]):
                raise FileNotFoundError("Artifact not found")
        elif record["owner"] != self.owner():
            raise FileNotFoundError("Artifact not found")
        if record["kind"] in ("image", "video", "manifest") and token is None:
            self.check("get_image")
        path = Path(record["path"])
        if not path.is_file():
            raise FileNotFoundError("Artifact file is no longer available")
        return record


def build_server(config: Config):
    state = State(config)
    server = FastMCP("mcpBlender", version=__version__,
                     instructions="Inspect the scene before editing; prefer named actions and structured tools for supported workflows. "
                     "Inspect geometry and images after changes. Blender is a shared session. "
                     "A bridge timeout does not cancel execution. start_blender launches explicitly.")
    server.add_transform(AccessTransform(state))

    @server.tool()
    async def list_actions() -> dict:
        """Discover permitted named actions. Use describe_action for typed inputs and plan_action before editing."""
        return {"actions": [d.describe() for d in state.actions.definitions.values()
                            if all(state.allowed(c) for c in d.required_commands)]}

    @server.tool()
    async def describe_action(action: str) -> dict:
        """Return an action's input schema, possible commands, capabilities and definition digest."""
        definition = state.actions.get(action)
        for command in definition.required_commands:
            state.check(command)
        return definition.describe()

    @server.tool()
    async def plan_action(action: str, inputs: dict | None = None, request_id: str | None = None) -> dict:
        """Validate an action and every step/permission/path without starting Blender or applying scene edits. Reuse request_id to retain generated output paths."""
        identifier = request_id or uuid.uuid4().hex
        return state.compile_action(action, inputs or {}, identifier) | {"request_id": identifier}

    @server.tool()
    async def run_action(action: str, inputs: dict | None = None, wait: bool = False, request_id: str | None = None) -> dict:
        """Run one serial tracked action. Default returns immediately. With wait=true return the operation, including state and partial results on failure/cancellation; no rollback or replay."""
        queued = await state.submit("run_action", {"action": action, "inputs": inputs or {}}, request_id)
        if not wait:
            return queued
        deadline = time.monotonic() + config.bridge.timeout_seconds
        while True:
            result = await state.operation(queued["operation_id"])
            if result["state"] in ("succeeded", "failed", "cancelled", "interrupted") or time.monotonic() >= deadline:
                return result
            await asyncio.sleep(.05)

    @server.tool()
    async def storage_status(path: str | None = None) -> dict:
        """Report current free/available bytes and reserve for the active library or an output path."""
        root = Path(state.policy().library_root).expanduser().resolve()
        target = (root / Path(path).expanduser()).resolve() if path else root
        if not target.is_relative_to(root):
            raise ValueError("Storage path must be inside library_root")
        return snapshot(target, **state.config.storage.model_dump())

    @server.tool()
    async def submit_operation(command: str, params: dict, request_id: str | None = None) -> dict:
        """Submit any validated operation without waiting. Use get_operation/wait_operation to recover results.

        Reuse request_id with identical arguments to recover the same operation across
        reconnects and MCP restarts. Changed arguments with the same ID are rejected.
        """
        return await state.submit(command, params, request_id)

    def domain_tool(command):
        model, _, description = COMMANDS[command]
        default_wait = command not in ("render_animation", "render_video", "download_asset", "upload_asset", "request_asset_api")

        async def operation(params=None, wait: bool = default_wait, request_id: str | None = None) -> dict:
            values = params.model_dump(exclude_none=True) if params is not None else {}
            queued = await state.submit(command, values, request_id)
            if not wait:
                return queued
            deadline = time.monotonic() + state.config.bridge.timeout_seconds
            while True:
                result = await state.operation(queued["operation_id"])
                if result["state"] in ("failed", "cancelled", "interrupted"):
                    raise RuntimeError(f"Operation {result['operation_id']} {result['state']}: {result.get('error')}")
                if result["state"] == "succeeded":
                    return result["result"] | {"operation_id": result["operation_id"]}
                if time.monotonic() >= deadline:
                    return result
                await asyncio.sleep(0.05)

        operation.__annotations__["params"] = model | None
        server.tool(operation, name=command, description=description + " Arguments are in params; use wait=false for asynchronous execution.")

    for command in sorted(DOMAIN_COMMANDS):
        domain_tool(command)

    @server.tool()
    async def bridge_status() -> dict:
        """Inspect live Blender connectivity, version, process/session identity and active policy."""
        policy = state.policy()
        return {"bridge": await state.rpc("status"), "storage": snapshot(policy.library_root, **state.config.storage.model_dump()),
                "active_key_policy": policy.model_dump(exclude={"kdf"}),
                "key_id": state.owner()}

    @server.tool()
    async def start_blender(background: bool | None = None, recover: bool = True) -> dict:
        """Launch installed Blender and the Unix bridge, or reuse the existing live bridge. No rebuild."""
        state.check("start_blender")
        return await asyncio.to_thread(state.launcher.start, background, recover)

    @server.tool()
    async def restart_bridge() -> dict:
        """Restart the socket broker, preserve Blender and its journal, and reconnect automatically."""
        state.check("start_blender")
        before = await state.control("status")
        await state.control("restart_broker")
        for _ in range(100):
            await asyncio.sleep(.1)
            current = await state.control("status")
            if current["broker_epoch"] != before["broker_epoch"]:
                return current
        raise RuntimeError("Broker restart is still pending")

    @server.tool()
    async def get_scene_info(offset: int = 0, limit: int = 200) -> dict:
        """Inspect scene objects, transforms, materials, selection, units, camera and animation range."""
        return await state.rpc("get_scene_info", offset=offset, limit=limit)

    @server.tool()
    async def get_object_info(name: str) -> dict:
        """Inspect one object's mesh counts, modifiers, world transform and collection membership."""
        return await state.rpc("get_object_info", name=name)

    @server.tool()
    async def execute_code(code: str, wait: bool = True, request_id: str | None = None) -> dict:
        """Execute arbitrary Python on Blender's main thread. bpy is available; assign JSON result to result.

        Returns success, stdout, stderr, result and traceback. A failed script can leave partial edits.
        This has Blender's full OS permissions; library_root does not sandbox Python.
        """
        state.check("execute_code")
        if wait and request_id is None:
            return await state.rpc("execute_code", code=code)
        operation = await state.submit("execute_code", {"code": code}, request_id)
        if not wait:
            return operation
        return await wait_operation(operation["operation_id"], state.config.bridge.timeout_seconds)

    @server.tool()
    async def select_objects(names: list[str]) -> dict:
        """Replace selection with these scene objects and activate the first; empty list deselects."""
        state.check("select_objects")
        return await state.rpc("select_objects", names=names)

    @server.tool()
    async def set_object_transform(name: str, location: list[float] | None = None,
                                   rotation_euler: list[float] | None = None,
                                   scale: list[float] | None = None) -> dict:
        """Set local transforms, with Euler angles in radians. Returns updated object details."""
        state.check("set_object_transform")
        return await state.rpc("set_object_transform", name=name, location=location,
                               rotation_euler=rotation_euler, scale=scale)

    @server.tool()
    async def set_frame(frame: int) -> dict:
        """Evaluate an animation frame in the active scene."""
        state.check("set_frame")
        return await state.rpc("set_frame", frame=frame)

    @server.tool()
    async def open_blend(path: str) -> dict:
        """Replace the current session's project from library_root. Save wanted edits first."""
        state.check("open_blend")
        return await state.rpc("open_blend", path=str(state.library_path(path)))

    @server.tool()
    async def save_blend(path: str, overwrite: bool = False) -> dict:
        """Save a .blend project under library_root; replacing an existing file requires overwrite."""
        state.check("save_blend")
        target = state.library_path(path, write=True)
        if target.suffix.lower() != ".blend":
            raise ValueError("Project filename must end in .blend")
        return await state.rpc("save_blend", path=str(target), overwrite=overwrite)

    @server.tool()
    async def import_asset(path: str) -> dict:
        """Import a local GLB/glTF, OBJ or STL asset from library_root into the current scene."""
        state.check("import_asset")
        return await state.rpc("import_asset", path=str(state.library_path(path)))

    @server.tool()
    async def export_glb(path: str, selected_only: bool = True, animations: bool = True,
                         overwrite: bool = False) -> dict:
        """Export a Godot-friendly GLB under library_root, using glTF +Y up. Inspect selection first."""
        state.check("export_glb")
        target = state.library_path(path, write=True)
        if target.suffix.lower() != ".glb":
            raise ValueError("Export filename must end in .glb")
        if selected_only:
            scene = await state.rpc("get_scene_info", limit=1000)
            if scene["total_objects"] > 1000:
                raise ValueError("Use scene inspection and execute_code for exports of scenes over 1000 objects")
            if not any(obj["selected"] for obj in scene["objects"]):
                raise ValueError("No scene objects selected for export")
        result = await state.rpc("export_glb", path=str(target), selected_only=selected_only,
                                 animations=animations, overwrite=overwrite)
        return result | state.artifact(target, "model")

    @server.tool()
    async def capture_viewport(width: int = 800, height: int = 600) -> dict:
        """Capture a GUI viewport to an artifact. Use get_image to view it; requires GUI Blender."""
        state.check("capture_viewport")
        target = state.library_path(f"artifacts/{uuid.uuid4().hex}.png", write=True)
        result = await state.rpc("capture_viewport", path=str(target), width=width, height=height)
        return result | state.artifact(target)

    @server.tool()
    async def render_still(width: int = 800, height: int = 600, camera: str | None = None,
                           engine: Literal["CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"] | None = None) -> dict:
        """Submit a camera render; poll get_job and view the resulting artifact with get_image.

        Blender's GUI can be busy until it finishes. Job state is server-side, not render percentage.
        """
        state.check("render_still")
        if not (16 <= width <= 4096 and 16 <= height <= 4096):
            raise ValueError("Image dimensions must be between 16 and 4096")
        target = state.library_path(f"artifacts/{uuid.uuid4().hex}.png", write=True)
        return await state.submit("render_still", dict(path=str(target), width=width, height=height,
                                                 camera=camera, engine=engine))

    @server.tool()
    async def get_job(job_id: str) -> dict:
        """Poll this key's render job. A timeout failure does not prove Blender stopped rendering."""
        return await state.operation(job_id)

    @server.tool()
    async def get_operation(operation_id: str) -> dict:
        """Read durable operation status/results without entering Blender's main-thread queue."""
        return await state.operation(operation_id)

    @server.tool()
    async def wait_operation(operation_id: str, timeout_seconds: float = 10) -> dict:
        """Wait up to 60 seconds for completion; return current status without cancelling on timeout."""
        deadline = time.monotonic() + min(60, max(0, timeout_seconds))
        while True:
            operation = await state.operation(operation_id)
            if operation["state"] in ("succeeded", "failed", "cancelled", "interrupted") or time.monotonic() >= deadline:
                return operation
            await asyncio.sleep(0.1)

    @server.tool()
    async def list_operations(limit: int = 30) -> dict:
        """List this key's persisted operations after reconnect or server restart."""
        return await state.control("list_operations", owner=state.owner(), limit=limit)

    @server.tool()
    async def cancel_operation(operation_id: str) -> dict:
        """Cancel queued work, or request cooperative cancellation of running work.

        Animation stops between frames; Python can call check_cancelled(). Blocking bpy
        calls are not forcibly interrupted. Partial edits and completed files remain.
        """
        return await state.control("cancel_operation", operation_id=operation_id, owner=state.owner())

    @server.tool()
    async def get_artifact(artifact_id: str) -> dict:
        """Resolve this key's persistent artifact metadata and download links, including video."""
        record = state.resolve_artifact(artifact_id)
        return state.artifact(record["path"], record["kind"], artifact_id)

    @server.tool()
    async def get_image(artifact_id: str) -> Image:
        """Return an image artifact as MCP image content for direct visual inspection."""
        state.check("get_image")
        record = state.resolve_artifact(artifact_id)
        if record["kind"] != "image":
            raise ValueError("Artifact is not an image")
        return Image(path=record["path"])

    return server, state


def extract_token(request):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else None
    return token or request.headers.get("x-api-key") or request.query_params.get("mcp") or request.query_params.get("api_key")


def build_http_app(server, state):
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse
    # FastAPI resolves postponed annotations in module globals.
    globals()["Request"] = Request
    if state.config.mode != "remotehttp" or not state.config.api_keys:
        raise ValueError("Remote HTTP requires remotehttp mode and at least one API key")
    mcp_app = server.http_app(path="/", transport="streamable-http", stateless_http=True,
                              host_origin_protection=False)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(title="mcpBlender", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path == "/status":
            return await call_next(request)
        if request.url.path.startswith("/artifacts/") and request.query_params.get("token"):
            return await call_next(request)
        key = await asyncio.to_thread(match_key, extract_token(request), state.config.api_keys)
        if key is None:
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
        token = ACTIVE_KEY.set(key)
        try:
            return await call_next(request)
        finally:
            ACTIVE_KEY.reset(token)

    @app.get("/status")
    async def status():
        return {"running": True, "version": __version__}

    @app.get("/artifacts/{artifact_id}")
    async def download(artifact_id: str, request: Request):
        try:
            record = state.resolve_artifact(artifact_id, token=request.query_params.get("token"))
        except (FileNotFoundError, PermissionError):
            return JSONResponse({"detail": "Artifact not found"}, status_code=404)
        return FileResponse(record["path"], media_type={"image": "image/png", "model": "model/gltf-binary", "video": "video/mp4", "manifest": "application/json"}.get(record["kind"], "application/octet-stream"),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    app.mount("/mcp", mcp_app)
    return app


def addon_zip(destination: Path):
    source = Path(__file__).parent
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in [source / "__init__.py", source / "wire.py", source / "broker.py", source / "network.py", source / "storage.py", *sorted((source / "blender").glob("*.py"))]:
            archive.write(path, str(Path("mcpblender") / path.relative_to(source)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path.home() / ".config/mcpblender.conf"))
    parser.add_argument("--transport", choices=["stdio", "remotehttp"])
    parser.add_argument("--remote", action="store_true")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--init-config", action="store_true")
    commands.add_argument("--genkey", metavar="ID")
    commands.add_argument("--start-blender", action="store_true")
    commands.add_argument("--addon-zip", metavar="PATH")
    parser.add_argument("--background", action="store_true", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    path = Path(args.config).expanduser()
    if args.init_config:
        if path.exists():
            parser.error("Config already exists")
        write_config(path, Config())
        return
    if args.addon_zip:
        addon_zip(Path(args.addon_zip))
        return
    if args.genkey:
        print(generate_key(path, args.genkey))
        return
    config = load_config(path)
    if args.background is not None:
        config.launch.background = args.background
    if args.start_blender:
        import json
        print(json.dumps(Launcher(config).start(args.background), indent=2))
        return
    config.mode = "remotehttp" if args.remote else (args.transport or config.mode)
    server, state = build_server(config)
    try:
        if config.mode == "stdio":
            server.run("stdio", show_banner=False)
        else:
            import uvicorn
            remote = config.remote_server
            options = {"uds": remote.uds} if remote.uds else {"host": remote.host, "port": remote.port}
            # Disable uvicorn access logging: its request target includes query API keys.
            uvicorn.run(build_http_app(server, state), **options, access_log=False)
    finally:
        if config.launch.shutdown_on_exit and state.launcher.process is not None:
            state.launcher.shutdown()



if __name__ == "__main__":
    main()
