"""Passed to the installed Blender with --python; never modifies Blender itself."""
import atexit
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import bpy
from mcpblender.blender import bridge
from mcpblender.blender.addon import register

if os.environ.get("MCPBLENDER_RECOVER"):
    bpy.ops.wm.open_mainfile(filepath=os.environ["MCPBLENDER_RECOVER"], load_ui=False, use_scripts=False)
register()


def normal_exit():
    if bridge._bridge:
        try:
            bridge._bridge.control("desired_state", running=False)
        except (OSError, RuntimeError):
            pass
    bridge.stop()


atexit.register(normal_exit)
print("mcpBlender bridge ready", flush=True)
if bpy.app.background:
    try:
        while True:
            time.sleep(bridge.tick())
    except KeyboardInterrupt:
        normal_exit()
