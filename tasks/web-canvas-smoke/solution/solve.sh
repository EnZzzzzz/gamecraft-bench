#!/usr/bin/env bash
# Oracle reference implementation for web-canvas-smoke.
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/game"
GAME_DIR="/workspace/game"

mkdir -p "$GAME_DIR"
cp -a "$SOURCE_DIR/." "$GAME_DIR/"

echo "Oracle written to $GAME_DIR"
