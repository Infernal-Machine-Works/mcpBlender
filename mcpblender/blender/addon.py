"""Optional UI panel; the bootstrap also works without installing an add-on."""
import bpy
from . import bridge


class MCPBLENDER_OT_start(bpy.types.Operator):
    bl_idname = "mcpblender.start"
    bl_label = "Start MCP bridge"

    def execute(self, context):
        try:
            bridge.start()
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MCPBLENDER_OT_stop(bpy.types.Operator):
    bl_idname = "mcpblender.stop"
    bl_label = "Stop MCP bridge"

    def execute(self, context):
        bridge.stop()
        return {"FINISHED"}


class MCPBLENDER_PT_bridge(bpy.types.Panel):
    bl_label = "mcpBlender"
    bl_idname = "MCPBLENDER_PT_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP"

    def draw(self, context):
        active = bridge._bridge is not None
        self.layout.label(text="Bridge running" if active else "Bridge stopped")
        self.layout.label(text=str(bridge._bridge.path) if active else bridge.default_socket())
        self.layout.operator("mcpblender.stop" if active else "mcpblender.start")


CLASSES = (MCPBLENDER_OT_start, MCPBLENDER_OT_stop, MCPBLENDER_PT_bridge)


def register():
    for cls in CLASSES:
        if not hasattr(bpy.types, cls.__name__):
            bpy.utils.register_class(cls)
    bridge.start()


def unregister():
    bridge.stop()
    for cls in reversed(CLASSES):
        registered = getattr(bpy.types, cls.__name__, None)
        if registered is not None:
            bpy.utils.unregister_class(registered)
