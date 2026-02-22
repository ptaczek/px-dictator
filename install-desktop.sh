#!/usr/bin/env bash
#
# Install PX-Dictator into the desktop application launcher.
#
# Patches the .desktop file with the correct project path,
# then symlinks it into ~/.local/share/applications/.
#
# Usage:
#   ./install-desktop.sh              # launcher only
#   ./install-desktop.sh --autostart  # launcher + autostart on login
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_SRC="$PROJECT_DIR/px-dictator.desktop"
DESKTOP_DST="$HOME/.local/share/applications/px-dictator.desktop"
AUTOSTART_DST="$HOME/.config/autostart/px-dictator.desktop"

if [ ! -f "$DESKTOP_SRC" ]; then
    echo "Error: $DESKTOP_SRC not found." >&2
    exit 1
fi

# Patch paths in the .desktop file
sed -i \
    -e "s|^Exec=.*|Exec=python3 -m px_dictator.app|" \
    -e "s|^Path=.*|Path=$PROJECT_DIR|" \
    -e "s|^Icon=.*|Icon=$PROJECT_DIR/icons/pxd-ready.svg|" \
    "$DESKTOP_SRC"

# Create launcher symlink
mkdir -p "$(dirname "$DESKTOP_DST")"
ln -sf "$DESKTOP_SRC" "$DESKTOP_DST"
echo "Installed: $DESKTOP_DST -> $DESKTOP_SRC"

# Optionally create autostart symlink
if [ "${1:-}" = "--autostart" ]; then
    mkdir -p "$(dirname "$AUTOSTART_DST")"
    ln -sf "$DESKTOP_SRC" "$AUTOSTART_DST"
    echo "Autostart: $AUTOSTART_DST -> $DESKTOP_SRC"
fi
