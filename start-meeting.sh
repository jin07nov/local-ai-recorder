#!/usr/bin/env bash
# Start only the local recording/transcription service. No LLM, network or port killing.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${MEETING_ENV_FILE:-$PROJECT_DIR/.local/meeting.env}"
if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export PYTHONUNBUFFERED=1
if [[ "${1:-}" == --list-devices ]]; then
    exec arecord -L
fi
cd "$PROJECT_DIR"
exec python3 "$PROJECT_DIR/backend/meeting_server.py" "$@"
