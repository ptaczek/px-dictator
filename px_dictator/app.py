"""PX-Dictator main entry point — GLib main loop + orchestration."""

import argparse
import fcntl
import logging
import os
import signal
import sys
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from . import config
from .indicator import Indicator
from .hotkey import HotkeyListener
from .recorder import Recorder
from .servers import ServerManager
from .transcriber import transcribe
from .enhancer import enhance
from .paster import paste

log = logging.getLogger(__name__)


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.servers = ServerManager()
        self.recorder = Recorder(cfg)
        self.hotkey = None
        self._recording = False
        self._toggle_active = False

        self.indicator = Indicator(
            on_enable_toggle=self._on_enable_toggle,
            on_mode_change=self._on_mode_change,
            on_preferences=self._on_preferences,
            on_quit=self._on_quit,
        )

    def start(self):
        """Start the app — call from main thread."""
        mode = self.cfg["general"].get("activation_mode", "push-to-talk")
        self.indicator.set_mode(mode)

        if self.cfg["general"].get("enabled", True):
            self._enable()

        # SIGINT/SIGTERM → clean shutdown via GLib
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._on_quit)

        log.info("PX-Dictator started")
        Gtk.main()

    def _enable(self):
        """Start servers and hotkey listener."""
        self.indicator.set_state("processing")
        self.indicator.set_enabled(True)

        def do_start():
            try:
                self.servers.start(self.cfg)
                self._start_hotkey()
                GLib.idle_add(self.indicator.set_state, "ready")
            except Exception as e:
                log.error("Failed to start: %s", e)
                GLib.idle_add(self.indicator.set_state, "disabled")
                GLib.idle_add(self.indicator.set_enabled, False)

        threading.Thread(target=do_start, daemon=True).start()

    def _disable(self):
        """Stop hotkey and servers."""
        self._stop_hotkey()
        self.indicator.set_state("disabled")
        self.indicator.set_enabled(False)

        def do_stop():
            self.servers.stop()

        threading.Thread(target=do_stop, daemon=True).start()

    def _start_hotkey(self):
        mode = self.cfg["general"].get("activation_mode", "push-to-talk")
        if mode == "push-to-talk":
            self.hotkey = HotkeyListener(
                self.cfg,
                on_press=self._on_hotkey_press,
                on_release=self._on_hotkey_release,
            )
        else:
            self.hotkey = HotkeyListener(
                self.cfg,
                on_press=self._on_toggle_press,
            )
        self.hotkey.start()

    def _stop_hotkey(self):
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None

    # --- Push-to-talk callbacks ---

    def _on_hotkey_press(self):
        if self._recording:
            return
        self._recording = True
        GLib.idle_add(self.indicator.set_state, "recording")
        self.recorder.start()

    def _on_hotkey_release(self):
        if not self._recording:
            return
        self._recording = False
        samples = self.recorder.stop()
        GLib.idle_add(self.indicator.set_state, "processing")
        threading.Thread(target=self._process, args=(samples,), daemon=True).start()

    # --- Toggle mode callbacks ---

    def _on_toggle_press(self):
        if not self._toggle_active:
            self._toggle_active = True
            self._recording = True
            GLib.idle_add(self.indicator.set_state, "recording")
            self.recorder.start()
        else:
            self._toggle_active = False
            self._recording = False
            samples = self.recorder.stop()
            GLib.idle_add(self.indicator.set_state, "processing")
            threading.Thread(target=self._process, args=(samples,), daemon=True).start()

    # --- Processing pipeline ---

    def _process(self, samples):
        try:
            tc = self.cfg["transcription"]
            text = transcribe(samples, self.cfg["audio"]["sample_rate"],
                              tc["host"], tc["port"])
            if not text:
                log.warning("Empty transcription")
                GLib.idle_add(self.indicator.set_state, "ready")
                return

            text = enhance(text, self.cfg)
            paste(text, self.cfg, hotkey=self.hotkey)
        except Exception as e:
            log.error("Processing failed: %s", e)
        finally:
            GLib.idle_add(self.indicator.set_state, "ready")

    # --- Menu callbacks ---

    def _on_enable_toggle(self, enabled):
        if enabled:
            self._enable()
        else:
            self._disable()
        self.cfg["general"]["enabled"] = enabled
        config.save(self.cfg)

    def _on_mode_change(self, mode):
        self.cfg["general"]["activation_mode"] = mode
        config.save(self.cfg)
        if self.hotkey:
            self._stop_hotkey()
            self._start_hotkey()

    def _on_preferences(self):
        from .preferences import show_preferences
        show_preferences(self.cfg, on_save=self._on_prefs_saved, hotkey=self.hotkey)

    def _on_prefs_saved(self, new_cfg):
        old_cfg = self.cfg
        self.cfg = new_cfg
        config.save(self.cfg)

        # Rebuild recorder if audio settings changed
        if new_cfg["audio"] != old_cfg["audio"]:
            self.recorder = Recorder(self.cfg)

        # Restart hotkey if key or mode changed
        hotkey_changed = (
            new_cfg["hotkey"] != old_cfg["hotkey"] or
            new_cfg["general"]["activation_mode"] != old_cfg["general"]["activation_mode"]
        )
        if hotkey_changed and self.hotkey:
            self._stop_hotkey()
            self._start_hotkey()
            log.info("Hotkey restarted: %s (%s)", new_cfg["hotkey"]["key"],
                     new_cfg["general"]["activation_mode"])

        # Update indicator mode
        self.indicator.set_mode(new_cfg["general"]["activation_mode"])

        # Restart servers if model/transcription settings changed
        needs_server_restart = (
            new_cfg["transcription"] != old_cfg["transcription"] or
            new_cfg["enhancement"]["model"] != old_cfg["enhancement"]["model"] or
            new_cfg["enhancement"]["enabled"] != old_cfg["enhancement"]["enabled"] or
            new_cfg["enhancement"]["gpu_layers"] != old_cfg["enhancement"]["gpu_layers"]
        )
        if needs_server_restart and self.indicator.state != "disabled":
            self._disable()
            GLib.timeout_add(500, lambda: self._enable() or False)

    def _on_quit(self):
        log.info("Shutting down")
        self._stop_hotkey()
        self.servers.stop()
        Gtk.main_quit()
        return False


def _acquire_lock():
    """Acquire an exclusive lock to enforce single instance.

    Returns the open file object (must stay alive for the process lifetime).
    Exits with an error if another instance is already running.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/px-dictator-{os.getuid()}")
    lock_path = Path(runtime) / "px-dictator.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("PX-Dictator is already running.", file=sys.stderr)
        sys.exit(1)

    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def main():
    lock = _acquire_lock()  # noqa: F841  — must stay alive

    parser = argparse.ArgumentParser(description="PX-Dictator — Voice to Text")
    parser.add_argument("--config", help="Path to config TOML")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = config.load(args.config)
    app = App(cfg)
    app.start()


if __name__ == "__main__":
    main()
