#!/usr/bin/env bash
# Install the meeting recorder independently of Gemma Translator.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODEL=base
INSTALL_PACKAGES=1
BUILD_JOBS="${MEETING_BUILD_JOBS:-2}"
WHISPER_REF=b4938

usage() {
    echo "Usage: bash setup-meeting.sh [--model base|small] [--skip-system-packages]"
    echo "Run as your normal desktop user on 64-bit Raspberry Pi OS; sudo is used only for apt."
}
while (($#)); do
    case "$1" in
        --model) MODEL="${2:?--model needs base or small}"; shift 2 ;;
        --skip-system-packages) INSTALL_PACKAGES=0; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
case "$MODEL" in base|small) ;; *) echo "Choose multilingual base or small." >&2; exit 1 ;; esac
if [[ "$(uname -s)" != Linux || "$(getconf LONG_BIT)" != 64 ]]; then
    echo "64-bit Linux (Raspberry Pi OS) is required." >&2; exit 1
fi
if [[ "$EUID" == 0 ]]; then
    echo "Run without sudo; the installer invokes sudo for OS packages." >&2; exit 1
fi
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid MEETING_BUILD_JOBS" >&2; exit 1; }
if ((INSTALL_PACKAGES)); then
    sudo apt-get update
    sudo apt-get install -y build-essential cmake git curl ca-certificates alsa-utils python3 nodejs npm
fi
for tool in python3 cmake git curl arecord node npm; do
    command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required")'
node -e 'if (Number(process.versions.node.split(".")[0]) < 18) { throw new Error("Node.js 18+ is required") }'

LOCAL_DIR="$PROJECT_DIR/.local"
SOURCE_DIR="$LOCAL_DIR/whisper.cpp"
MODEL_DIR="$LOCAL_DIR/models"
mkdir -p "$LOCAL_DIR" "$MODEL_DIR" "$LOCAL_DIR/meetings"
if [[ ! -e "$SOURCE_DIR" ]]; then
    git clone --depth 1 --branch "$WHISPER_REF" https://github.com/ggml-org/whisper.cpp.git "$SOURCE_DIR"
fi
if [[ -n "$(git -C "$SOURCE_DIR" status --porcelain)" ]] || \
   [[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" != "$(git -C "$SOURCE_DIR" rev-parse "$WHISPER_REF^{commit}")" ]]; then
    echo "Existing whisper.cpp checkout differs from $WHISPER_REF; it was not overwritten." >&2
    exit 1
fi
cmake -S "$SOURCE_DIR" -B "$SOURCE_DIR/build" -DCMAKE_BUILD_TYPE=Release \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build "$SOURCE_DIR/build" --config Release --target whisper-cli -j "$BUILD_JOBS"

# Download to a temporary directory: an interrupted download must never look installed.
if [[ ! -s "$MODEL_DIR/ggml-$MODEL.bin" ]]; then
    DOWNLOAD_DIR="$(mktemp -d "$MODEL_DIR/download.XXXXXX")"
    trap 'rm -f -- "$DOWNLOAD_DIR/ggml-$MODEL.bin"; rmdir -- "$DOWNLOAD_DIR" 2>/dev/null || true' EXIT
    bash "$SOURCE_DIR/models/download-ggml-model.sh" "$MODEL" "$DOWNLOAD_DIR"
    test -s "$DOWNLOAD_DIR/ggml-$MODEL.bin"
    mv -- "$DOWNLOAD_DIR/ggml-$MODEL.bin" "$MODEL_DIR/ggml-$MODEL.bin"
    rmdir -- "$DOWNLOAD_DIR"
    trap - EXIT
fi
sha256sum "$MODEL_DIR/ggml-$MODEL.bin" > "$MODEL_DIR/ggml-$MODEL.sha256"
npm --prefix "$PROJECT_DIR/frontend" ci --no-audit --no-fund
npm --prefix "$PROJECT_DIR/frontend" run build

CONFIG_FILE="$LOCAL_DIR/meeting.env"
if [[ ! -e "$CONFIG_FILE" ]]; then
    umask 077
    {
        printf 'export WHISPER_BIN=%q\n' "$SOURCE_DIR/build/bin/whisper-cli"
        printf 'export WHISPER_MODEL=%q\n' "$MODEL_DIR/ggml-$MODEL.bin"
        printf 'export MEETING_DATA_DIR=%q\n' "$LOCAL_DIR/meetings"
        printf 'export MEETING_AUDIO_DEVICE=%q\n' default
        printf 'export WHISPER_THREADS=3\nexport MEETING_CHUNK_SECONDS=300\n'
        printf 'export MEETING_MAX_SECONDS=7200\nexport WHISPER_TIMEOUT_SECONDS=1800\n'
        printf 'export MEETING_HOST=127.0.0.1\nexport MEETING_PORT=3001\n'
    } > "$CONFIG_FILE"
else
    echo "Preserved existing $CONFIG_FILE (change WHISPER_MODEL there to select $MODEL)."
fi
"$SOURCE_DIR/build/bin/whisper-cli" --version
echo "Meeting setup complete. No translator service or kiosk settings were changed."
echo "1. bash start-meeting.sh --list-devices"
echo "2. Edit .local/meeting.env if the default microphone is not correct."
echo "3. bash start-meeting.sh --check"
echo "4. bash start-meeting.sh   -> open http://localhost:3001 on the Pi"
