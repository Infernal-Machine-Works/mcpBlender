# Persistent installation

Install a built wheel into a private Python 3.11+ environment. Keep runtime
configuration, keys, client backups, logs and model libraries outside the checkout.
Blender itself needs no rebuild or package installation into its Python runtime.

```sh
python3 -m venv "$HOME/.local/share/mcpblender/runtime"
"$HOME/.local/share/mcpblender/runtime/bin/python" -m pip install 'setuptools>=68' build
"$HOME/.local/share/mcpblender/runtime/bin/python" -m build
"$HOME/.local/share/mcpblender/runtime/bin/python" -m pip install 'dist/'*.whl
"$HOME/.local/share/mcpblender/runtime/bin/python" -m pip install fastapi uvicorn
"$HOME/.local/share/mcpblender/runtime/bin/mcpblender" --init-config
```

On a managed machine with qualified dependencies already installed, creating the
environment with `--system-site-packages` and installing the wheel with `--no-deps`
reuses those dependencies. This is a shared dependency environment, so future host
package changes can affect it. Run the dependency and connection checks again
after upgrades. A standard wheel/sdist build must pass its dependency checks;
`--skip-dependency-check` is not installation qualification.

Set `mode` to `remotehttp`, `remote_server.host` to `127.0.0.1` and an unused port
(default 18081). Set the Blender executable to its absolute path. The private Unix
socket connects the server to Blender. Each client gets its own API key and policy,
with the same library root when editing a shared scene. Generate keys with
`--config PATH --genkey CLIENT_ID` and store the printed key privately. Server
configuration contains Argon2id hashes. Never commit actual configurations or keys.

## FreeBSD service

Install [the rc.d template](../examples/rc.d/mcpblender) as
`/usr/local/etc/rc.d/mcpblender` with mode 0555. Create a root-owned mode-0600
`/etc/rc.conf.d/mcpblender`, substituting your desktop account and absolute paths:

```sh
mcpblender_enable="YES"
mcpblender_daemon_user="artist"
mcpblender_command="/home/artist/.local/share/mcpblender/runtime/bin/mcpblender"
mcpblender_config="/home/artist/.config/mcpblender.conf"
mcpblender_log="/var/log/mcpblender.log"
mcpblender_display=":0"
mcpblender_xauthority="/home/artist/.Xauthority"
```

Keep the supervisor log in a root-controlled directory: the root supervisor opens
it before dropping child privileges. It creates the log with mode 0600. Blender's
separate application log lives in the desktop account's private state directory.
Configure log rotation separately if needed. Use
`service mcpblender start`, `status`, `restart` and `stop`. The supervisor restarts
the HTTP frontend after exit, with a three-second delay. Stopping the supervisor
also stops the frontend. Blender and its broker survive frontend restarts by
default; use `shutdown_blender` to stop the application separately. Graceful
application shutdown disables automatic recovery until the next explicit start.

The service starts at boot without requiring Blender or a graphical session.
Lazy GUI launch requires the configured display and X authority to be available
after desktop login. Use `launch.background=true` for headless use. A service
start test does not prove post-reboot desktop access; verify that after reboot.

## Clients and skills

For Codex, add an `mcp_servers.mcpblender` table to its private `config.toml` with
`url = "http://127.0.0.1:18081/mcp/?api_key=ISSUED_KEY"`,
`startup_timeout_sec = 60` and `tool_timeout_sec = 120`. These are the documented
[Codex MCP settings](https://learn.chatgpt.com/docs/extend/mcp). Keep the file
mode 0600. Query keys are supported for compatibility with existing deployments;
avoid printing complete URLs or logging query strings. Long operations return
operation IDs: poll them instead of increasing synchronous timeouts indefinitely.

PQC supports HTTP MCP connections with query-parameter authentication. Add a server
named `mcpblender` to its private instance configuration, using the base MCP URL
and its own key through the client's auth settings. Preserve other server, model
and approval settings. Do not invoke a model merely to test MCP connectivity.

Install the three bundled skills without overwriting existing directories:

```sh
python3 tools/install_skills.py --client codex --dest "$HOME/.codex/skills"
python3 tools/install_skills.py --client pqc --dest "$HOME/.local/share/mcpblender/skills"
```

The PQC adapter adds its required `when_to_use` and server-discovery metadata;
the skill bodies are shared. Override `--server-name` if the connection has a
different name. For PQC, enable skill discovery and add that dedicated root.
Reload/restart clients to discover the new connection; Codex skills become
available on the next turn. Existing agent sessions need not be interrupted.

Verify from outside the source checkout: package import path, missing/invalid-key
rejection, both authenticated tool catalogs, lazy Blender startup, structured
model/material/preview creation, save/export/render, and reconnection after an HTTP
frontend restart. Store private evidence outside Git. Public remote access needs
TLS and a separately configured reverse proxy; a loopback install does not provide
public ingress.
