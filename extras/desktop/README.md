# dsh-qq-agent-desktop (optional)

This is **not** installed with the core plugin. It exposes three local HTTP
routes (`/desk/ping`, `/desk/run`, `/desk/result`) that let the QQ owner hand a
task to a DSH session and read the reply back.

## Why it is separate

The core QQ agent only chats and retrieves. Desktop control can restart DSH or
run arbitrary instructions on the owner's machine; that is a much larger
security surface and is Windows-specific. Install it only if you want it.

## Install

1. Install the core `dsh-qq-agent` first.
2. Install this bundle pointing at `extras/desktop` (not the repository root).
3. In the core `config.toml`, set:
   ```toml
   [desktop]
   enabled = true
   dsh_exe = ""      # optional; auto-detected on Windows
   dsh_home = ""     # optional; default ~/.dsh
   session_id = "qq-agent"
   cwd = ""          # optional; default process.cwd()
   ```
4. Restart the QQ service.

The Python side only registers `dsh_status`, `dsh_control` and `dsh_run` when
`[desktop].enabled = true` and `extras/desktop` is installed. Every call is
owner-only; `dsh_control` requires `confirm=true`.

## Security notes

- The Host routes reject non-loopback callers, but they are still local HTTP.
  Do not expose the DSH web server to a LAN or the public internet.
- The QQ service enforces `private:<owner_uid>`; a misconfigured `owner_uid`
  would grant the wrong person these tools.
- `dsh_run` runs whatever the owner asks for. Treat it like giving someone a
  shell on the machine.
- Tested on Windows with DSH 0.2.2-rc.1. The DSH session APIs are version
  sensitive; re-check `/desk/ping` after upgrading DSH.
