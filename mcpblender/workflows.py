"""Built-in workflow compilers. Emitted commands use ordinary permission checks."""
from .commands import PreviewSetup, GameAssetPrepare


def game_asset_plan(values):
    objects = values["objects"]
    report = {"objects": objects, "triangle_budget": values["triangle_budget"]}
    steps = [{"id": "preflight", "command": "preflight_game_asset", "params": values},
             {"id": "before", "command": "asset_report", "params": report}]
    for number, name in enumerate(objects):
        if values["apply_rotation_scale"]:
            steps.append({"id": f"transforms{number}", "command": "apply_transforms",
                          "params": {"name": name, "make_single_user": values["make_single_user"]}})
        if values["generate_uv"]:
            steps.append({"id": f"uv{number}", "command": "generate_uv",
                          "params": {"name": name, "overwrite": values["overwrite_uv"],
                                     "make_single_user": values["make_single_user"]}})
        if values["triangulate"]:
            steps.append({"id": f"triangles{number}", "command": "configure_modifier",
                          "params": {"name": name, "modifier": "MCP_GameTriangulate", "settings": {"type": "TRIANGULATE"}}})
    steps.append({"id": "report", "command": "asset_report", "params": report})
    if values.get("save_path"):
        steps.append({"id": "save", "command": "save_blend", "params": {"path": values["save_path"]}})
    if values.get("export_path"):
        steps.append({"id": "export", "command": "export_objects_glb", "params": {"objects": objects, "path": values["export_path"]}})
    return {"steps": steps}


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
    yield Definition("game_asset_prepare", "1",
        "Inspect named meshes for game/simulation delivery. Default is inspection only. "
        "Explicit options apply static rotation/scale, generate UVMap, add a live triangulation modifier, "
        "save the project and export just these meshes. Edits have no rollback. "
        "Skinned assets require separate rig-aware delivery; this workflow refuses their edits/export.",
        GameAssetPrepare,
        {"preflight_game_asset", "asset_report", "apply_transforms", "generate_uv", "configure_modifier", "save_blend", "export_objects_glb"},
        game_asset_plan,
        {"name": "game_asset_prepare", "version": "1", "schema": GameAssetPrepare.model_json_schema(),
         "steps": ["preflight", "before", "optional_transforms_uv_triangulate", "report", "optional_save_export"]},
        required_commands={"preflight_game_asset", "asset_report"})
