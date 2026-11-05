#!/bin/bash
# Sync the working tree into the local launchd runtime and restart the service.
# launchd agents on macOS cannot reliably read project files on an external
# volume without Full Disk Access, so the service runs a copy under
# ~/Library/Application Support/HomeKitOne/runtime.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="${HOMEKIT_ONE_RUNTIME:-$HOME/Library/Application Support/HomeKitOne/runtime}"
PYTHON="$RUNTIME/.venv/bin/python"
LABEL="com.homekitone.gateway"
DOMAIN="gui/$(id -u)"

if [[ ! -x "$PYTHON" ]]; then
  echo "runtime venv not found: $PYTHON" >&2
  echo "run ./scripts/install.sh first" >&2
  exit 1
fi

SITE="$("$PYTHON" -c 'import site; print(site.getsitepackages()[0])')"
rm -f "$SITE"/_editable_impl_homekit_one.pth "$SITE"/_editable_impl_homeone.pth
rm -rf "$SITE/homekit_one"
cp -R "$REPO/src/homekit_one" "$SITE/homekit_one"
find "$SITE/homekit_one" -name '__pycache__' -type d -prune -exec rm -rf {} +

launchctl kickstart -k "$DOMAIN/$LABEL"
echo "synced $REPO/src/homekit_one -> $SITE/homekit_one"
echo "restarted $LABEL"
