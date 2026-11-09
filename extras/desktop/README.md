# dsh-qq-agent-desktop (optional, high risk)

> **High risk / 高风险.** This is **not** a read-only panel. Once enabled, the
> QQ owner can restart or stop DSH, and can hand arbitrary text to a DSH
> session for execution. It is not installed by the core plugin and is off by
> default. Do not install it if you do not need it.

The core `dsh-qq-agent` panel only reads status. This separate bundle gives the
owner four tools:

| Tool | What it does | Needs `/desk/*`? |
|---|---|---|
| `dsh_status` | Read DSH process count, memory, GUI port, recent sessions | No; local PowerShell |
| `dsh_control` | `restart` / `start` / `stop` DSH; requires `confirm=true` | No; local PowerShell |
| `dsh_run` | Submit a message to a DSH session; optional `background=true` | Yes; `/desk/run` |
| `dsh_result` | Poll a background task via `/desk/result` | Yes; `/desk/result` |

It is **not** a remote control for the DSH GUI, and it is not a direct shell
from QQ. `dsh_run` submits a message to a DSH session; what that session can do
depends on the DSH agent's own tools and permissions.

## Install

1. Install and verify the core `dsh-qq-agent` first.
2. Install this bundle pointing at `extras/desktop` (not the repository root).
3. In `%USERPROFILE%\.dsh\qq-agent\config.toml` (Linux/macOS:
   `~/.dsh/qq-agent/config.toml`), set:

   ```toml
   [desktop]
   enabled = true
   dsh_exe = ""          # optional; auto-detected on Windows
   dsh_home = ""         # optional; default ~/.dsh
   session_id = "qq-agent"
   cwd = "C:\your\workspace"   # set this intentionally; empty = process cwd
   timeout_ms = 90000
   ```

   `cwd` is where the DSH session works. Do not leave it empty unless you know
   what the service's process cwd is.

4. Restart the QQ service. The tools are only registered when
   `[desktop].enabled = true`.

## Verify

From the owner's private chat, start with the read-only tool:

```text
dsh_status {}
```

Only then try `dsh_control` (it interrupts DSH sessions) and a tiny `dsh_run`.
`dsh_run` with `background=true` returns a receipt; use `dsh_result` to poll it.

## Security notes

- The Host routes reject non-loopback callers, but they are still local HTTP
  with no extra authentication. Any local process can call them. The Python
  service's owner check is the real gate.
- The QQ service enforces `private:<owner_uid>`. A wrong `owner_uid` grants
  these tools to the wrong person.
- `confirm=true` is a model-supplied boolean, not a password. It reduces
  accidents, not a determined prompt injection.
- `dsh_run` is effectively remote task execution. Treat it like giving the
  owner a shell through DSH, with whatever permissions that DSH agent has.
- The DSH session APIs are version-sensitive. Re-check `/desk/ping` after
  upgrading DSH.
- Tested on Windows with DSH 0.2.2-rc.1. `dsh_status` / `dsh_control` use
  PowerShell; `dsh_run` / `dsh_result` need this bundle's `/desk/*` routes.

## Disable / remove

Set `[desktop].enabled = false` and restart the QQ service. To remove it
completely, uninstall `dsh-qq-agent-desktop` from the DSH profile and delete
this directory if you do not need the source.

Full Chinese threat model and setup:
[`docs/desktop-control.md`](../../docs/desktop-control.md).
