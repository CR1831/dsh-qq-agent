# Changelog

## 0.2.0

### Desktop extra

- Added a background task queue (`dsh_bg.py`): `dsh_run(background=true)` stores
  the task in `<data>/dsh_tasks.json` and the QQ service polls it every 15
  seconds. Finished or timed-out tasks are reported to the owner.
- Added `dsh_result` for manual polling of a background task.
- A synchronous `dsh_run` that does not finish is now adopted into the queue
  instead of losing the result.
- `dsh_run` now accepts `session`, `preset` and `isolate`:
  - `session` opens an isolated DSH context;
  - `preset` selects a DSH capability profile;
  - `isolate=true` creates `<cwd>/_tasks/<stamp-slug>/` and keeps the latest 30.
- The optional DSH desk bridge now accepts `agentPreset` and per-request `cwd`.
- `preset` is documented as a capability profile, not a security sandbox.

### Documentation

- Added [docs/desktop-control.md](docs/desktop-control.md): high-risk warning,
  four tools, install/verify/disable, background queue, task directories, and
  threat model.
- README, security, install and the bilingual overview now state that desktop
  control is separate, off by default, owner-only and high risk.

### Version

- Bumped from 0.1.0 to 0.2.0.

## 0.1.0

Initial public shape: QQ-only OneBot 11 service, local SQLite index, guarded
replies, user-supplied persona cards, read-only DSH panel.
