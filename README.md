# Blender MCP Server `mcpblender`

`mcpblender` is an MCP server for creating, inspecting, editing, animating,
rendering and exporting Blender scenes. It connects coding agents to a locally
installed Blender through a private Unix domain socket. Typed tools cover the
usual workflows; optional Python execution exposes the rest of Blender's API.
Blender does **not** need rebuilding.

__WORK IN PROGRESS — 0.2 implementation__

The main workflows are animation production, game/simulation asset modeling and
model visualization. Structured editing, textured materials, skeletal and morph
animation, rendering, video assembly and recovery are implemented and live-tested
on FreeBSD with Blender 5.0.1. Other Blender versions and platforms remain
unqualified. See [VALIDATION.md](VALIDATION.md) for the tested scope and limits.

The Python distribution is named **`mcpBlender-tspspi`**; the import package and
command are `mcpblender`. This is a separate implementation, without exact tool
compatibility with other Blender MCP servers.

## Features

- scene and object inspection, mesh creation, modifiers, UV generation,
  duplication, parenting, transforms and explicit asset preparation
- structured shader graphs, reusable PBR/glass/emission material presets,
  packed image textures and material assignment
- lighting, cameras, fitted preview stages, viewport screenshots and still renders
- keyframes, skeletal rigs and clips, relative shape keys, deformation sampling,
  frame sequences and animation previews
- Blender video sequence editing and video output, subject to installed FFmpeg
  support
- project save/open, local GLB/glTF, OBJ and STL import, GLB export, and configured
  external asset/API adapters
- validated named actions and operator-defined recipes for repeated workflows
- stdio MCP and authenticated Streamable HTTP with mcpFreeCAD-style API-key policies
- managed GUI/headless Blender startup, automatic reconnects, checkpoints and
  crash recovery without automatic replay of interrupted edits
- multiple clients sharing one Blender session, durable operation tracking,
  free-space feedback and scoped temporary-output retention
- repository-owned [coding-agent skills](#skills) for design, rendering and animation

## Installation

Requires Python 3.11+, Blender 4.5+ and a Unix system with same-user socket peer
checks (`getpeereid` or `SO_PEERCRED`). Live qualification currently covers only
FreeBSD Blender 5.0.1.

Install from this checkout in a Python environment containing the declared
dependencies:

```sh
pip install -e .
```

For remote HTTP mode, install its optional dependencies as well:

```sh
pip install -e ".[remote]"
```

Neither command rebuilds Blender, installs a Blender add-on, creates configuration,
installs skills or enables a service. MCP dependencies run outside Blender; the
Blender bridge uses `bpy` and Python's standard library.

For a wheel-based runtime and persistent FreeBSD service, see
[persistent installation](docs/DEPLOYMENT.md).

## Quick Start

1. Create a private configuration and set its `stdio.library_root` to a directory
   you control. Set `launch.executable` if Blender is not on the server's PATH:

   ```sh
   mcpblender --config ./mcpblender.conf --init-config
   ```

2. Start the stdio MCP server:

   ```sh
   mcpblender --config ./mcpblender.conf
   ```

3. From an MCP client, call `start_blender`, then `bridge_status` and
   `get_scene_info`. Create an object with `create_primitive`, for example:

   ```json
   {"params": {"name": "Building", "kind": "CUBE", "scale": [2, 1, 1.5]}}
   ```

4. Apply a material, set up a camera and lights with `preview_setup` through
   `run_action`, and submit `render_still`. Poll `get_job`, then use `get_image`
   to inspect the rendered artifact. Domain-tool arguments go inside `params`;
   named actions take `action` and `inputs` directly.

The MCP server can start without Blender running. With `launch.auto_start=true`,
the first operation needing Blender also starts it lazily. Default startup opens
a fresh GUI with factory settings, script auto-execution disabled and the bridge
bootstrapped. The welcome splash is disabled for that session; user preferences
are not saved or overwritten. Use `start_blender(background=true)` for headless
work; viewport screenshots require a GUI.

For complete workflows and tool arguments, see [USER_MANUAL.md](USER_MANUAL.md).

## Configuration

The default configuration path is `~/.config/mcpblender.conf`. Create it explicitly
with `--init-config`, or copy [examples/mcpblender.conf](examples/mcpblender.conf).
The server does not create configuration during ordinary startup.

The JSON configuration separates the local bridge, application launch settings,
stdio policy, HTTP listener and API-key policies. A typical local configuration
uses this structure:

```json
{
  "mode": "stdio",
  "bridge": {
    "socket_path": "~/.local/state/mcpblender/bridge.sock",
    "timeout_seconds": 120
  },
  "launch": {
    "executable": "blender",
    "background": false,
    "auto_start": true,
    "auto_recover": true
  },
  "stdio": {
    "library_root": "~/blender-mcp-library",
    "allow_code_execution": false
  }
}
```

This example selects structured-only editing. Generated defaults allow Python
execution; set `allow_code_execution=false` in each relevant policy to disable it.
Python execution has Blender's full OS permissions and is not sandboxed by
`library_root`. Structured file tools confine their entry paths to the active
library; Blender may resolve linked dependencies when loading projects or assets.

The bridge directory belongs to the desktop account with mode `0700`; its socket
is `0600` and verifies peer identity. It has no network listener or bridge token.
All clients share one Blender scene; API keys do not create separate applications.

For HTTP, use `remote_server.host="127.0.0.1"` and port `18081`, then generate a
key explicitly:

```sh
mcpblender --config ./mcpblender.conf --genkey design-agent
```

The command prints the key once and stores only its salted Argon2id verifier.
Each key has its own policy and library root. Keep actual configurations and keys
outside Git. If copying the example, replace its placeholder
`remote_server.url_prefix` with the deployed base URL, or set it to `null`.

## Connecting with Agents

### Codex example (stdio)

Adjust the executable and configuration paths for your installation:

```toml
[mcp_servers.mcpblender]
command = "mcpblender"
args = ["--config", "/path/to/mcpblender.conf", "--transport", "stdio"]
startup_timeout_sec = 60
tool_timeout_sec = 120
```

### Codex example (remote HTTP)

```toml
[mcp_servers.mcpblender]
url = "http://127.0.0.1:18081/mcp/?api_key=<MCPBLENDER_API_KEY>"
startup_timeout_sec = 60
tool_timeout_sec = 120
```

### PQC example (remote HTTP)

Enable MCP in the PQC instance and add this entry beneath `mcp.servers`:

```json
{
  "mcpblender": {
    "enabled": true,
    "transport": "http",
    "url": "http://127.0.0.1:18081/mcp/",
    "auth": {
      "mode": "query_param",
      "token": "<MCPBLENDER_API_KEY>",
      "param_name": "api_key"
    },
    "policy": {
      "enabled": true,
      "visible_by_default": true,
      "network_classification": "local"
    }
  }
}
```

Use separate issued keys for separate clients. Reload the clients after changing
connections. For long-running work, request asynchronous execution and poll the
operation ID instead of holding one synchronous call open indefinitely.

### FreeBSD service

The service is not installed or enabled automatically. The repository includes an
[rc.d template](examples/rc.d/mcpblender) for a supervised HTTP frontend running
under the desktop account. See [the deployment guide](docs/DEPLOYMENT.md) for
runtime installation, boot configuration, graphical-session access and private logs.

## Running

Run stdio MCP mode:

```sh
mcpblender --config ./mcpblender.conf --transport stdio
```

Run authenticated Streamable HTTP mode:

```sh
mcpblender --config ./mcpblender.conf --transport remotehttp
```

HTTP mode requires an issued key. Authentication accepts `Authorization: Bearer`,
`X-API-Key`, `?mcp=` or `?api_key=`. `/status` is a public frontend health check;
`/mcp/` requires authentication. Keep query strings out of proxy access logs.
For TLS proxy configuration, see [examples/apache.conf](examples/apache.conf).

Use `shutdown_blender` to close the application; it requires saving or explicitly
discarding unsaved edits. Ordinary frontend disconnection leaves Blender running.
Operations execute serially in Blender, while the broker keeps status available.
Native rendering can occupy the GUI. Crash recovery restores a completed
checkpoint and reports interrupted work as uncertain; it does not replay edits.

## Skills

- [blender-mcp-design](skill/blender-mcp-design/SKILL.md): modeling, inspection and asset preparation
- [blender-mcp-render](skill/blender-mcp-render/SKILL.md): materials, lighting, cameras and image review
- [blender-mcp-animation](skill/blender-mcp-animation/SKILL.md): animation, frame sequences and video delivery

Install the bundled skills explicitly, without overwriting existing directories:

```sh
python3 tools/install_skills.py --client codex --dest "$HOME/.codex/skills"
python3 tools/install_skills.py --client pqc --dest "$HOME/.local/share/mcpblender/skills"
```

For PQC, enable skill discovery and add its destination as a skill root. The
installer adds the metadata PQC needs while preserving the shared skill bodies.

## Repository Layout

- `mcpblender/`: MCP server, policies, launcher, operation broker and Blender bridge
- `examples/`: configuration, reverse-proxy and FreeBSD service templates
- `skill/`: coding-agent skills for design, rendering and animation
- `tools/`: skill installer and representative building/GUI workflow fixtures
- `tests/`: contract, transport, retention and real-Blender integration tests
- `USER_MANUAL.md`: detailed operator and MCP-client workflows
- `docs/DEPLOYMENT.md`: persistent installation and client setup
- `VALIDATION.md`: test commands, evidence and qualification limits

For development checks, install `.[test]` and run `python3 -m pytest -q`. Set
`MCPBLENDER_TEST_BLENDER=1` to include disposable real-Blender integration tests.
