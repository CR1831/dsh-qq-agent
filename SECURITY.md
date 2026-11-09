# Security policy

This is a local tool. It connects a QQ account through NapCat/OneBot 11, stores
messages in a local SQLite database, and can send replies from that account.

## Default posture

- `dry_run = true`: replies are generated but not sent.
- Empty `friend_whitelist` / `group_whitelist`: no private or group replies.
- `proactive = false`: no unsolicited messages.
- Vision is off by default.
- The optional desktop-control package is not installed by default and is
  owner-only when enabled.

## Do not expose

- Do not bind the DSH web server to a LAN or the public internet. The panel
  route and the optional `/desk/*` routes are local-only by design.
- Do not commit `config.toml`, `persona.md`, `data/`, `logs/` or `exports/`.
- Do not use your primary QQ account for an unofficial OneBot client.

## Reporting

Open a GitHub issue with reproduction steps. Do not include tokens, QQ numbers,
chat logs or persona-card contents.

If a token was pushed publicly, rotate it first (LLM key, NapCat token, SMTP
password), then delete and recreate the repository or rewrite history. Deleting
a file in a later commit does not remove it from git history.
