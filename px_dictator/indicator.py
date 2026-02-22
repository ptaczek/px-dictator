"""GNOME panel indicator — tries AyatanaAppIndicator3, falls back to Gtk.StatusIcon."""

import logging
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf, GLib

from . import config

log = logging.getLogger(__name__)

# Try to load AppIndicator (Ayatana or legacy)
_AppIndicator = None
for _ns in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(_ns, "0.1")
        _AppIndicator = getattr(__import__("gi.repository", fromlist=[_ns]), _ns)
        log.debug("Using %s for panel indicator", _ns)
        break
    except (ValueError, ImportError):
        continue

ICON_DIR = config.PROJECT_DIR / "icons"

STATES = {
    "disabled":   ("pxd-disabled",   "PX-Dictator: Disabled"),
    "ready":      ("pxd-ready",      "PX-Dictator: Ready"),
    "recording":  ("pxd-recording",  "PX-Dictator: Recording..."),
    "processing": ("pxd-processing", "PX-Dictator: Processing..."),
}


class Indicator:
    def __init__(self, on_enable_toggle=None, on_mode_change=None,
                 on_preferences=None, on_quit=None):
        self._on_enable_toggle = on_enable_toggle
        self._on_mode_change = on_mode_change
        self._on_preferences = on_preferences
        self._on_quit = on_quit
        self._state = "disabled"

        self._menu = self._build_menu()

        if _AppIndicator is not None:
            self._ind = _AppIndicator.Indicator.new(
                "px-dictator",
                str(ICON_DIR / "pxd-disabled.svg"),
                _AppIndicator.IndicatorCategory.APPLICATION_STATUS,
            )
            self._ind.set_icon_theme_path(str(ICON_DIR))
            self._ind.set_status(_AppIndicator.IndicatorStatus.ACTIVE)
            self._ind.set_menu(self._menu)
            self._status_icon = None
        else:
            log.info("AppIndicator not available, using Gtk.StatusIcon fallback")
            self._ind = None
            self._status_icon = Gtk.StatusIcon()
            self._status_icon.set_from_file(str(ICON_DIR / "pxd-disabled.svg"))
            self._status_icon.set_tooltip_text("PX-Dictator")
            self._status_icon.set_visible(True)
            self._status_icon.connect("popup-menu", self._on_popup)
            self._status_icon.connect("activate", self._on_activate)

    def _build_menu(self):
        menu = Gtk.Menu()

        # Enabled checkbox
        self._enabled_item = Gtk.CheckMenuItem(label="Enabled")
        self._enabled_item.set_active(False)
        self._enabled_item.connect("toggled", self._on_enabled_toggled)
        menu.append(self._enabled_item)

        # Mode submenu
        mode_item = Gtk.MenuItem(label="Mode")
        mode_menu = Gtk.Menu()
        self._mode_ptt = Gtk.RadioMenuItem(label="Push-to-talk")
        self._mode_toggle = Gtk.RadioMenuItem.new_with_label_from_widget(
            self._mode_ptt, "Toggle"
        )
        self._mode_ptt.set_active(True)
        self._mode_ptt.connect("toggled", self._on_mode_toggled)
        self._mode_toggle.connect("toggled", self._on_mode_toggled)
        mode_menu.append(self._mode_ptt)
        mode_menu.append(self._mode_toggle)
        mode_item.set_submenu(mode_menu)
        menu.append(mode_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Preferences
        prefs_item = Gtk.MenuItem(label="Preferences...")
        prefs_item.connect("activate", lambda _: self._on_preferences and self._on_preferences())
        menu.append(prefs_item)

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: self._on_quit and self._on_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_popup(self, icon, button, activate_time):
        self._menu.popup(None, None, None, None, button, activate_time)

    def _on_activate(self, icon):
        # Left-click toggles enabled
        self._enabled_item.set_active(not self._enabled_item.get_active())

    def _on_enabled_toggled(self, widget):
        if self._on_enable_toggle:
            self._on_enable_toggle(widget.get_active())

    def _on_mode_toggled(self, widget):
        if widget.get_active() and self._on_mode_change:
            mode = "push-to-talk" if widget is self._mode_ptt else "toggle"
            self._on_mode_change(mode)

    def set_enabled(self, enabled):
        """Update the checkbox without firing the callback."""
        self._enabled_item.handler_block_by_func(self._on_enabled_toggled)
        self._enabled_item.set_active(enabled)
        self._enabled_item.handler_unblock_by_func(self._on_enabled_toggled)

    def set_mode(self, mode):
        if mode == "push-to-talk":
            self._mode_ptt.set_active(True)
        else:
            self._mode_toggle.set_active(True)

    def set_state(self, state):
        """Set indicator state: disabled, ready, recording, processing."""
        if state not in STATES:
            return
        self._state = state
        icon_name, description = STATES[state]
        icon_path = str(ICON_DIR / f"{icon_name}.svg")

        if self._ind is not None:
            self._ind.set_icon_full(icon_path, description)
            self._ind.set_title(description)
        elif self._status_icon is not None:
            self._status_icon.set_from_file(icon_path)
            self._status_icon.set_tooltip_text(description)

        log.debug("Indicator state: %s", state)

    @property
    def state(self):
        return self._state
