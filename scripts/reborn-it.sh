#!/bin/sh
set -eu
runtime="$(dirname "$(readlink -f "$0")")"
export REBORN_DATA_DIR="${REBORN_DATA_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/reborn-it-operations}"
systemctl --user start reborn-it-tunnel.service 2>/dev/null || true
# Use the existing system-owned sandbox helper when Ubuntu restricts user namespaces.
if [ -u /opt/google/chrome/chrome-sandbox ] && [ "$(stat -c %u /opt/google/chrome/chrome-sandbox)" = 0 ]; then
  export CHROME_DEVEL_SANDBOX=/opt/google/chrome/chrome-sandbox
  # Chromium also checks its adjacent helper. The signed ZIP contains regular
  # files; configure this trusted system helper in the user-owned install only.
  if ! [ "$runtime/chrome-sandbox" -ef /opt/google/chrome/chrome-sandbox ]; then
    if [ -f "$runtime/chrome-sandbox" ] && [ ! -e "$runtime/chrome-sandbox.bundled" ]; then
      mv "$runtime/chrome-sandbox" "$runtime/chrome-sandbox.bundled"
    fi
    ln -sfn /opt/google/chrome/chrome-sandbox "$runtime/chrome-sandbox"
  fi
fi
exec "$runtime/electron" "$@"
