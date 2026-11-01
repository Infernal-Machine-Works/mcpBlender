"""Built-in workflow compilers. Emitted commands use ordinary permission checks."""
from .commands import PreviewSetup


def definitions():
    from .actions import Definition
    yield Definition("preview_setup", "1",
        "Create persistent key/fill/rim area lights and a fitted active camera around named meshes. "
        "Uses evaluated bounds at the current frame and current render aspect. Existing lights remain. "
        "Choose a fresh prefix; refit after aspect or pose changes.",
        PreviewSetup, {"setup_preview_stage"},
        lambda values: {"steps": [{"id": "stage", "command": "setup_preview_stage", "params": values}]},
        {"name": "preview_setup", "version": "1", "schema": PreviewSetup.model_json_schema(),
         "steps": ["setup_preview_stage"]})
