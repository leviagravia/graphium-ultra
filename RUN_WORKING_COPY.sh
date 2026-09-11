#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE="$ROOT/.working-runtime"
mkdir -p "$PRIVATE/config" "$PRIVATE/data" "$PRIVATE/cache" "$PRIVATE/state"
export XDG_CONFIG_HOME="$PRIVATE/config"
export XDG_DATA_HOME="$PRIVATE/data"
export XDG_CACHE_HOME="$PRIVATE/cache"
export XDG_STATE_HOME="$PRIVATE/state"
export PYTHONDONTWRITEBYTECODE=1
exec "$ROOT/bin/graphium-ultra" "$@"
