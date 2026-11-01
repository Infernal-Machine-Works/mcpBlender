"""Execute an already validated action plan serially in one Blender operation."""
import types
from pathlib import Path

COMMANDS = {"run_action"}
_running = None


class ActionFailure(Exception):
    def __init__(self, error, result):
        super().__init__(error)
        self.result = result


def decorate_progress(value):
    if value.get("_action_envelope"):
        return {k: v for k, v in value.items() if k != "_action_envelope"}
    if _running:
        return progress(_running, value)
    return value


def progress(result, value=None):
    return {"phase": "running action", "action": result["action"],
            "completed_steps": sum(s["state"] == "succeeded" for s in result["steps"]),
            "total_steps": result["total_steps"], "steps": result["steps"],
            "step_progress": value, "partial_effects_possible": True}


def partial_output(step):
    directory = step.get("params", {}).get("directory")
    if step.get("command") in {"render_animation", "render_animation_preview"} and directory and Path(directory).is_dir():
        paths = sorted(Path(directory).glob("*.png"))
        return {"directory": directory, "existing_frame_count": len(paths), "existing_frames": [str(p) for p in paths[:16]]}
    return None


def run(plan):
    from .bridge import OperationCancelled, dispatch, report_progress, _bridge
    from mcpblender.storage import require_space
    global _running
    result = {"action": plan["action"], "version": plan["version"], "definition_sha256": plan["definition_sha256"],
              "state": "running", "steps": [], "total_steps": len(plan["steps"]), "partial_effects_possible": True}
    _running = result
    current, step, child = None, None, None
    original_storage = _bridge.active.get("storage")
    try:
        for step in plan["steps"]:
            current = {"id": step["id"], "command": step["command"], "state": "not_started"}
            result["steps"].append(current)
            yield progress(result) | {"_action_envelope": True}
            current["state"] = "running"
            _bridge.active["storage"] = step["storage"]
            current["storage"] = require_space(**step["storage"])
            report_progress(progress(result) | {"_action_envelope": True})
            value = dispatch(step["command"], step["params"])
            if isinstance(value, types.GeneratorType):
                child = value
                try:
                    while True:
                        try:
                            update = next(child)
                        except StopIteration as completed:
                            value = completed.value
                            break
                        yield progress(result, update) | {"_action_envelope": True}
                finally:
                    child.close()
                    child = None
            current["result"] = value
            if isinstance(value, dict) and value.get("success") is False:
                raise RuntimeError("Action step returned success=false")
            current["state"] = "succeeded"
            yield progress(result) | {"_action_envelope": True}
        result["state"] = "succeeded"
        return result
    except (OperationCancelled, Exception) as exc:
        cancelled = isinstance(exc, OperationCancelled)
        result["state"] = "cancelled" if cancelled else "failed"
        if current and current["state"] != "succeeded":
            current.update(state="cancelled" if cancelled else "failed", error=f"{type(exc).__name__}: {exc}")
            if step:
                current["partial_output"] = partial_output(step)
        if cancelled:
            exc.result = result
            raise
        raise ActionFailure(f"Action {plan['action']} failed at {current['id'] if current else 'startup'}: {exc}", result) from exc
    finally:
        _bridge.active["storage"] = original_storage
        _running = None


def dispatch(command, p):
    if command == "run_action":
        return run(p)
    raise ValueError(f"Unsupported action command: {command}")
