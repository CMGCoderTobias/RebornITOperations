#!/bin/sh
set -eu
export REBORN_DATA_DIR="${REBORN_DATA_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/reborn-it-operations}"
systemctl --user start reborn-it-tunnel.service 2>/dev/null || true
# Use the existing system-owned sandbox helper when Ubuntu restricts user namespaces.
if [ -u /opt/google/chrome/chrome-sandbox ]; then
  export CHROME_DEVEL_SANDBOX=/opt/google/chrome/chrome-sandbox
fi
exec "$(dirname "$(readlink -f "$0")")/electron" "$@"
