#!/usr/bin/env bash
#
# Remove PX-Dictator from the desktop application launcher and autostart.
#
# Usage:
#   ./uninstall-desktop.sh              # remove launcher only
#   ./uninstall-desktop.sh --autostart  # remove both launcher and autostart
#
set -euo pipefail

DESKTOP_APP="$HOME/.local/share/applications/px-dictator.desktop"
DESKTOP_AUTO="$HOME/.config/autostart/px-dictator.desktop"

removed=0

# Always remove launcher
if [ -e "$DESKTOP_APP" ] || [ -L "$DESKTOP_APP" ]; then
    rm "$DESKTOP_APP"
    echo "Removed: $DESKTOP_APP"
    removed=$((removed + 1))
fi

# Remove autostart if --autostart flag given
if [ "${1:-}" = "--autostart" ]; then
    if [ -e "$DESKTOP_AUTO" ] || [ -L "$DESKTOP_AUTO" ]; then
        rm "$DESKTOP_AUTO"
        echo "Removed: $DESKTOP_AUTO"
        removed=$((removed + 1))
    fi
fi

if [ "$removed" -eq 0 ]; then
    echo "Nothing to remove — PX-Dictator is not installed in the launcher."
fi
