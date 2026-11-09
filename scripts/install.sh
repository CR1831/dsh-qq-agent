#!/usr/bin/env bash
# Install the Python environment and runtime home on macOS/Linux.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="$ROOT/service"
HOME_DIR="${QQ_AGENT_HOME:-$HOME/.dsh/qq-agent}"

mkdir -p "$HOME_DIR/data/qq" "$HOME_DIR/logs"
if [ ! -x "$SERVICE/.venv/bin/python" ]; then
  python3 -m venv "$SERVICE/.venv"
fi
"$SERVICE/.venv/bin/python" -m pip install --upgrade pip
"$SERVICE/.venv/bin/python" -m pip install -e "$SERVICE"

if [ ! -f "$HOME_DIR/config.toml" ]; then
  cp "$SERVICE/config.example.toml" "$HOME_DIR/config.toml"
fi
cp "$ROOT/persona.example.md" "$HOME_DIR/persona.example.md"

echo "home: $HOME_DIR"
echo "config: $HOME_DIR/config.toml"
echo "next: edit config, put your card at $HOME_DIR/persona.md, then run:"
echo "  QQ_AGENT_HOME=$HOME_DIR $SERVICE/.venv/bin/python -m qq_agent qq --listen"
