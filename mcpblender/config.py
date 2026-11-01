"""JSON configuration and FreeCAD-compatible API key hashes."""
from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Literal

from argon2.low_level import Type, hash_secret_raw
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Policy(Model):
    library_root: str = str(Path.home() / "blender-mcp-library")
    allow_code_execution: bool = True
    allow_library_write: bool = True
    allow_snapshots: bool = True
    allow_scene_edit: bool = True
    allow_start_blender: bool = True
    allow_external_assets: bool = True
    allow_shutdown_blender: bool = True
    enable_autonomous_execution: bool = True


class APIKey(Policy):
    id: str
    kdf: dict


class Bridge(Model):
    socket_path: str = str(Path.home() / ".local/state/mcpblender/bridge.sock")
    timeout_seconds: float = Field(default=120, gt=0)


class Launch(Model):
    executable: str = "blender"
    background: bool = False
    auto_start: bool = True
    factory_startup: bool = True
    startup_timeout_seconds: float = Field(default=30, gt=0)
    log_path: str = str(Path.home() / ".local/state/mcpblender/blender.log")
    auto_recover: bool = True
    max_recovery_attempts: int = Field(default=3, ge=0, le=100)
    checkpoint_interval_seconds: float = Field(default=30, ge=0)
    checkpoint_copies: int = Field(default=3, ge=1, le=100)
    shutdown_on_exit: bool = False


class Storage(Model):
    reserve_bytes: int = Field(default=256 * 1024**2, ge=0)
    warn_below_bytes: int = Field(default=2 * 1024**3, ge=0)


class Remote(Model):
    host: str = "127.0.0.1"
    port: int | None = Field(default=18081, ge=1, le=65535)
    uds: str | None = None
    url_prefix: str | None = None


class AssetSource(Model):
    base_url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=120, gt=0)
    max_bytes: int = Field(default=256 * 1024 * 1024, ge=1)


class Config(Model):
    mode: Literal["stdio", "remotehttp"] = "stdio"
    bridge: Bridge = Field(default_factory=Bridge)
    launch: Launch = Field(default_factory=Launch)
    stdio: Policy = Field(default_factory=Policy)
    remote_server: Remote = Field(default_factory=Remote)
    api_keys: list[APIKey] = Field(default_factory=list)
    asset_sources: dict[str, AssetSource] = Field(default_factory=dict)
    storage: Storage = Field(default_factory=Storage)

    @model_validator(mode="after")
    def unique_key_ids(self):
        ids = [key.id for key in self.api_keys]
        if any(not key_id.strip() for key_id in ids) or len(ids) != len(set(ids)):
            raise ValueError("API key IDs must be nonempty and unique")
        return self


def load_config(path: str | Path) -> Config:
    return Config.model_validate_json(Path(path).expanduser().read_text())


def hash_key(token: str, kdf: dict) -> str:
    if kdf.get("algorithm") != "argon2id":
        raise ValueError("Only argon2id is supported")
    return base64.b64encode(hash_secret_raw(
        token.encode(), base64.b64decode(kdf["salt"], validate=True),
        int(kdf["time_cost"]), int(kdf["memory_cost"]), int(kdf["parallelism"]),
        int(kdf["hash_len"]), Type.ID,
    )).decode()


def match_key(token: str | None, keys: list[APIKey]) -> APIKey | None:
    if not token or len(token) > 4096:
        return None
    for key in keys:
        try:
            if hmac.compare_digest(key.kdf["hash"], hash_key(token, key.kdf)):
                return key
        except (ValueError, TypeError, KeyError):
            continue
    return None


def generate_key(path: Path, key_id: str) -> str:
    """Print-once key; atomic private config replacement. Existing policies survive rotation."""
    config = load_config(path)
    token = secrets.token_urlsafe(48)
    kdf = dict(algorithm="argon2id", salt=base64.b64encode(os.urandom(16)).decode(),
               time_cost=3, memory_cost=65536, parallelism=1, hash_len=32)
    kdf["hash"] = hash_key(token, kdf)
    old = next((k for k in config.api_keys if k.id == key_id), None)
    entry = APIKey(**(old.model_dump(exclude={"kdf"}) if old else {"id": key_id}), kdf=kdf)
    config.api_keys = [k for k in config.api_keys if k.id != key_id] + [entry]
    write_config(path, config)
    return token


def write_config(path: Path, config: Config) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".mcpblender-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(config.model_dump(), indent=2) + "\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
