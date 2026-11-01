"""Available-space reporting and output admission checks (stdlib only)."""
import math
import os
import shutil
import time
from pathlib import Path

DEFAULT_RESERVE = 256 * 1024 * 1024


def snapshot(path, estimated_bytes=0, reserve_bytes=DEFAULT_RESERVE, warn_below_bytes=2 * 1024**3, estimate_kind="unknown"):
    path = Path(path).expanduser().resolve()
    existing = path
    while not existing.exists():
        existing = existing.parent
    if existing.is_file():
        existing = existing.parent
    usage = shutil.disk_usage(existing)
    available = max(0, usage.free - reserve_bytes)
    return {"path": str(path), "filesystem_path": str(existing), "device": existing.stat().st_dev,
            "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
            "reserve_bytes": reserve_bytes, "available_after_reserve_bytes": available,
            "estimated_output_bytes": estimated_bytes, "estimate_kind": estimate_kind,
            "enough_space": available >= estimated_bytes and usage.free >= reserve_bytes,
            "warning": "Low available storage" if usage.free < warn_below_bytes else None,
            "checked_at": time.time()}


def require_space(path, estimated_bytes=0, **policy):
    report = snapshot(path, estimated_bytes, **policy)
    if not report["enough_space"]:
        raise OSError(f"Insufficient storage: free_bytes={report['free_bytes']}, reserve_bytes={report['reserve_bytes']}, "
                      f"estimated_output_bytes={estimated_bytes}, estimate_kind={report['estimate_kind']}")
    return report


def estimate(command, params):
    if command in ("render_still", "capture_viewport", "render_animation"):
        count = 1
        if command == "render_animation":
            count = (params["frame_end"] - params["frame_start"]) // params.get("frame_step", 1) + 1
        # PNG may be RGBA16. Include modest compression overhead, not an assumed ratio.
        return math.ceil(params.get("width", 800) * params.get("height", 600) * 8 * 1.1) * count, "conservative_pixel_bound"
    if command == "download_asset":
        return params.get("max_bytes", 0), "configured_download_limit"
    if command == "render_video":
        count = params["frame_end"] - params["frame_start"] + 1
        return params.get("width", 640) * params.get("height", 360) * 4 * count, "uncompressed_video_heuristic"
    if command == "save_blend":
        path = Path(params["path"])
        return max(16 * 1024**2, path.stat().st_size * 2 if path.is_file() else 0), "project_size_heuristic"
    return 0, "unknown"
