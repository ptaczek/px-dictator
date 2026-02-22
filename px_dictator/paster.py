"""Clipboard + paste with Xorg/Wayland autodetect and terminal detection."""

import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)

# Known terminal window classes (matched case-insensitively)
DEFAULT_TERMINALS = {
    "kitty", "alacritty", "gnome-terminal", "gnome-terminal-server",
    "tilix", "konsole", "xfce4-terminal", "terminator", "st",
    "urxvt", "xterm", "foot", "wezterm",
}


def _detect_session(cfg_session="auto"):
    if cfg_session in ("xorg", "x11"):
        return "x11"
    if cfg_session == "wayland":
        return "wayland"
    # Auto-detect
    st = os.environ.get("XDG_SESSION_TYPE", "")
    if st == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11"


def _get_clipboard(session):
    """Read current clipboard content."""
    try:
        if session == "wayland":
            r = subprocess.run(["wl-paste", "--no-newline"],
                               capture_output=True, timeout=3)
        else:
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                               capture_output=True, timeout=3)
        if r.returncode == 0:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _set_clipboard(content, session, is_bytes=False):
    """Set clipboard content (str or bytes)."""
    if session == "wayland":
        if is_bytes:
            subprocess.run(["wl-copy"], input=content, check=True, timeout=5)
        else:
            subprocess.run(["wl-copy", "--", content], check=True, timeout=5)
    else:
        data = content if is_bytes else content.encode()
        subprocess.run(["xclip", "-selection", "clipboard"],
                       input=data, check=True, timeout=5)


def _get_focused_wm_classes_x11():
    """Get all WM_CLASS values of focused window via xdotool + xprop."""
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=3,
        )
        if wid.returncode != 0:
            return []

        result = subprocess.run(
            ["xprop", "-id", wid.stdout.strip(), "WM_CLASS"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and "=" in result.stdout:
            parts = result.stdout.split("=", 1)[1].strip()
            return [s.strip(' "') for s in parts.split(",")]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return []


def _is_terminal(wm_classes, terminal_classes):
    return any(c.lower() in terminal_classes for c in wm_classes)


def paste(text: str, cfg: dict, hotkey=None):
    """Copy text to clipboard and simulate paste keystroke.

    Uses the hotkey's uinput device for the keystroke (works on both
    X11 and Wayland). Falls back to xdotool/ydotool if unavailable.
    """
    paste_cfg = cfg.get("paste", {})
    session = _detect_session(paste_cfg.get("session", "auto"))
    terminal_classes = {c.lower() for c in paste_cfg.get("terminal_classes", DEFAULT_TERMINALS)}
    delay_ms = paste_cfg.get("delay_ms", 100)

    log.debug("Pasting %d chars (session=%s)", len(text), session)

    # Save current clipboard content
    previous = _get_clipboard(session)

    _set_clipboard(text, session)
    time.sleep(delay_ms / 1000.0)

    # Detect if focused window is a terminal
    if session == "wayland":
        # No reliable window detection on Wayland — default to terminal-style
        # paste (Ctrl+Shift+V). Works in terminals and acts as "paste without
        # formatting" in most modern apps (VS Code, Chrome, Firefox, etc).
        is_term = True
        log.debug("Wayland session — using Ctrl+Shift+V")
    else:
        wm_classes = _get_focused_wm_classes_x11()
        is_term = _is_terminal(wm_classes, terminal_classes)
        log.debug("Focused window: %s (terminal=%s)", wm_classes, is_term)

    # Send paste keystroke via uinput (works on both X11 and Wayland)
    if hotkey and hotkey.type_paste(is_term):
        log.debug("Paste keystroke sent via uinput")
    else:
        # Fallback: external tools (need grab release on X11)
        log.debug("Falling back to external paste tool")
        if hotkey:
            hotkey.pause_grab()
        try:
            keys = "ctrl+shift+v" if is_term else "ctrl+v"
            if session == "wayland":
                ydotool_keys = "29:1 42:1 47:1 47:0 42:0 29:0" if is_term else "29:1 47:1 47:0 29:0"
                subprocess.run(["ydotool", "key", ydotool_keys], check=True, timeout=5)
            else:
                subprocess.run(["xdotool", "key", "--delay", "12", keys], check=True, timeout=5)
        finally:
            if hotkey:
                hotkey.resume_grab()

    # Let the target app finish reading clipboard before restoring
    time.sleep(delay_ms / 1000.0)

    # Restore previous clipboard content
    if previous is not None:
        _set_clipboard(previous, session, is_bytes=True)
        log.debug("Clipboard restored")

    log.info("Pasted %d chars", len(text))
