#!/bin/bash
# deepcode-auto — wrapper that auto-applies Context Compact patch, then runs deepcode
PATCH_SCRIPT="F:/DEEPCODE/core/patch_context_compact.py"
CLI_JS="$APPDATA/npm/node_modules/@vegamo/deepcode-cli/dist/cli.js"

# Check if patch is already applied
if ! grep -q "__compactToolOutput" "$CLI_JS" 2>/dev/null; then
  echo "[deepcode-auto] Applying Context Compact patch..."
  python3 "$PATCH_SCRIPT" 2>&1
fi

# Preload readline line-guard to prevent RangeError from oversized MCP responses
# (e.g. directory_tree returning 100MB+ single-line JSON)
LINEGUARD="F:/DEEPCODE/.deepcode/patches/deepcode-lineguard.js"
if [ -f "$LINEGUARD" ]; then
  export NODE_OPTIONS="--require \"$LINEGUARD\"${NODE_OPTIONS:+ $NODE_OPTIONS}"
fi

exec deepcode "$@"
