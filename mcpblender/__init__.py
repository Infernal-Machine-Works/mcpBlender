"""Keep this package dependency-free so Blender can import the bridge."""

__version__ = "0.2.0"

bl_info = {
    "name": "mcpBlender Unix Bridge", "author": "Thomas Spielauer",
    "version": (0, 2, 0), "blender": (4, 5, 0),
    "location": "View3D > Sidebar > MCP", "category": "Interface",
}


def register():
    from .blender.addon import register as enable
    enable()


def unregister():
    from .blender.addon import unregister as disable
    disable()
