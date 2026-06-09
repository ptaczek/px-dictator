"""Global hotkey via evdev (runs in daemon thread).

Supports single keys (KEY_F8) and combos (KEY_LEFTCTRL+KEY_F8).
For combos: all keys must be held to activate, any key released to deactivate.

The keyboard is grabbed exclusively and non-hotkey events are forwarded
through a uinput virtual device, so the hotkey key never reaches applications.
Modifier keys in combos are always forwarded (Ctrl still works for Ctrl+C etc).
"""

import logging
import select
import threading
import time

import evdev
from evdev import ecodes

log = logging.getLogger(__name__)

# Modifier keys — used by learn_key to wait for a non-modifier "main" key
_MODIFIERS = {
    ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
    ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT,
    ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
    ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA,
}

_VIRTUAL_NAMES = {"ydotoold virtual device", "px-dictator-kbd"}

# Lock keys whose LEDs must be synced back to the physical keyboard after grab,
# because the kernel only toggles the LED on the device that produced the event
# (the uinput device), not the grabbed physical one.  CapsLock/NumLock are
# handled by XKB across all devices; ScrollLock is not on modern desktops.
_LED_SYNC = {
    ecodes.KEY_SCROLLLOCK: ecodes.LED_SCROLLL,
}


def _key_name(code):
    """Get evdev key name for a code."""
    name = ecodes.KEY.get(code)
    if name is None:
        return f"KEY_{code}"
    if isinstance(name, list):
        return name[0]
    return name


def _parse_combo(spec):
    """Parse 'KEY_LEFTCTRL+KEY_F8' into a set of key codes."""
    codes = set()
    for part in spec.split("+"):
        part = part.strip()
        code = getattr(ecodes, part, None)
        if code is None:
            raise ValueError(f"Unknown evdev key: {part}")
        codes.add(code)
    return frozenset(codes)


def _combo_name(codes):
    """Format a set of key codes as 'KEY_LEFTCTRL+KEY_F8' (modifiers first)."""
    mods = sorted(c for c in codes if c in _MODIFIERS)
    keys = sorted(c for c in codes if c not in _MODIFIERS)
    return "+".join(_key_name(c) for c in mods + keys)


def _find_keyboard(device_hint=""):
    """Find a keyboard device in /dev/input/event*.

    Prefers real hardware keyboards over virtual devices (ydotool etc).
    Falls back to virtual if no real keyboard is accessible.
    """
    devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
    candidates = []
    for dev in devices:
        caps = dev.capabilities(verbose=False)
        if ecodes.EV_KEY not in caps:
            continue
        key_caps = caps[ecodes.EV_KEY]
        has_letters = any(ecodes.KEY_A <= k <= ecodes.KEY_Z for k in key_caps)
        if not has_letters:
            continue
        if device_hint and device_hint.lower() in dev.name.lower():
            log.info("Hotkey device (matched hint '%s'): %s", device_hint, dev.name)
            return dev
        is_virtual = dev.name in _VIRTUAL_NAMES or not dev.phys
        candidates.append((is_virtual, dev))

    # Prefer real devices over virtual
    candidates.sort(key=lambda x: x[0])
    for _, dev in candidates:
        log.info("Hotkey device (auto): %s (%s)", dev.name, dev.path)
        return dev
    raise RuntimeError(
        "No keyboard device found. Run: sudo usermod -aG input $USER\n"
        "Then logout and login for the group change to take effect."
    )


def list_keyboards():
    """List candidate keyboard devices as (name, path), deduped by name.

    Includes any device exposing letter keys, minus our own helper virtual
    devices.  Used by the preferences UI to populate a device picker so the
    user can point the hotkey at the right device (e.g. 'keyd virtual
    keyboard' when a remapper grabs the physical one) without editing TOML.
    """
    seen = set()
    result = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        try:
            caps = dev.capabilities(verbose=False)
            if ecodes.EV_KEY not in caps:
                continue
            key_caps = caps[ecodes.EV_KEY]
            if not any(ecodes.KEY_A <= k <= ecodes.KEY_Z for k in key_caps):
                continue
            if dev.name in _VIRTUAL_NAMES or dev.name in seen:
                continue
            seen.add(dev.name)
            result.append((dev.name, dev.path))
        finally:
            dev.close()
    return result


class HotkeyListener:
    def __init__(self, cfg, on_press=None, on_release=None):
        hc = cfg["hotkey"]
        self._key_spec = hc.get("key", "KEY_RIGHTALT")
        self._combo = _parse_combo(self._key_spec)
        # Non-modifier keys in the combo are consumed (not forwarded to apps)
        self._consume_codes = self._combo - _MODIFIERS
        self._device_hint = hc.get("device", "")
        self.on_press = on_press
        self.on_release = on_release
        self._thread = None
        self._stop_event = threading.Event()
        self._device = None
        self._uinput = None
        self._paused = False

    @property
    def key_name(self):
        return self._key_spec

    def type_paste(self, is_terminal=False):
        """Send Ctrl+V or Ctrl+Shift+V through uinput. Returns True on success."""
        if self._uinput is None:
            return False
        try:
            ui = self._uinput
            ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1)
            if is_terminal:
                ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)
            ui.syn()
            time.sleep(0.012)
            ui.write(ecodes.EV_KEY, ecodes.KEY_V, 1)
            ui.syn()
            time.sleep(0.012)
            ui.write(ecodes.EV_KEY, ecodes.KEY_V, 0)
            ui.syn()
            time.sleep(0.012)
            if is_terminal:
                ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
            ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0)
            ui.syn()
            return True
        except Exception as e:
            log.error("uinput paste failed: %s", e)
            return False

    def pause_grab(self):
        """Temporarily release keyboard grab (for paste operations)."""
        self._paused = True  # suppress uinput forwarding first
        if self._device is not None:
            try:
                self._device.ungrab()
            except Exception:
                pass

    def resume_grab(self):
        """Re-acquire keyboard grab after pause."""
        if self._device is not None:
            try:
                self._device.grab()
            except Exception:
                pass
        self._paused = False

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._device is not None:
            try:
                self._device.ungrab()
            except Exception:
                pass
            try:
                self._device.close()
            except Exception:
                pass
        if self._uinput is not None:
            try:
                self._uinput.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self):
        try:
            self._device = _find_keyboard(self._device_hint)
        except RuntimeError as e:
            log.error("Hotkey listener failed: %s", e)
            return

        combo_name = _combo_name(self._combo)

        # Save LED state before grab (grab may reset LEDs)
        pre_grab_leds = set(self._device.leds())

        # Create virtual keyboard to forward non-hotkey events
        try:
            self._uinput = evdev.UInput.from_device(
                self._device, name="px-dictator-kbd"
            )
            self._device.grab()
            log.info("Keyboard grabbed, forwarding via uinput (consuming %s)",
                     ", ".join(_key_name(c) for c in self._consume_codes))
        except Exception as e:
            log.error("Failed to grab keyboard: %s", e)
            log.info("Falling back to non-grabbing mode (hotkey may leak to apps)")
            if self._uinput:
                self._uinput.close()
                self._uinput = None

        # Restore LEDs that were on before the grab (best-effort, never fatal)
        if self._uinput is not None:
            try:
                for led_code in _LED_SYNC.values():
                    if led_code in pre_grab_leds:
                        self._device.write(ecodes.EV_LED, led_code, 1)
                self._device.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
            except Exception:
                log.warning("Failed to restore LED state after grab")

        # Initialize LED tracking from pre-grab state
        led_state = {led: (led in pre_grab_leds) for led in _LED_SYNC.values()}

        log.info("Listening for %s (codes %s)", combo_name, self._combo)

        held = set()       # currently held keys that are part of our combo
        active = False     # combo is fully engaged

        try:
            for event in self._device.read_loop():
                if self._stop_event.is_set():
                    break

                # Determine if this event should be consumed or forwarded
                is_consumed = (
                    event.type == ecodes.EV_KEY and
                    event.code in self._consume_codes
                )

                # Forward non-consumed events through uinput
                # (skip during pause — keyboard is ungrabbed, events go to X11 directly)
                if not is_consumed and self._uinput is not None and not self._paused:
                    self._uinput.write_event(event)

                    # Sync lock-key LEDs back to the physical keyboard
                    if event.type == ecodes.EV_KEY and event.value == 0:
                        led_code = _LED_SYNC.get(event.code)
                        if led_code is not None:
                            led_state[led_code] = not led_state[led_code]
                            self._device.write(ecodes.EV_LED, led_code,
                                               int(led_state[led_code]))
                            self._device.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

                # Only track combo state for relevant key events
                if event.type != ecodes.EV_KEY or event.code not in self._combo:
                    continue

                if event.value == 1:  # key down
                    held.add(event.code)
                    if held == self._combo and not active:
                        active = True
                        log.debug("%s activated", combo_name)
                        if self.on_press:
                            try:
                                self.on_press()
                            except Exception:
                                log.exception("Hotkey on_press callback failed")

                elif event.value == 0:  # key up
                    held.discard(event.code)
                    if active:
                        active = False
                        log.debug("%s deactivated", combo_name)
                        if self.on_release:
                            try:
                                self.on_release()
                            except Exception:
                                log.exception("Hotkey on_release callback failed")

        except OSError:
            if not self._stop_event.is_set():
                log.error("Hotkey device disconnected")
        finally:
            try:
                self._device.ungrab()
            except Exception:
                pass
            if self._uinput is not None:
                self._uinput.close()
                self._uinput = None


def learn_key(device_hint="", timeout=10, on_update=None):
    """Capture a key or combo and return its spec string.

    Press any combination of keys, release all — the full set is captured.
    E.g. 'KEY_F8', 'KEY_LEFTALT', 'KEY_LEFTCTRL+KEY_F8'.
    Calls on_update(name_str) whenever the held set changes.
    """
    dev = _find_keyboard(device_hint)
    deadline = time.monotonic() + timeout
    held = set()
    peak = set()

    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select([dev.fd], [], [], 0.5)
            if not r:
                continue
            for event in dev.read():
                if event.type != ecodes.EV_KEY:
                    continue
                if event.value == 1:  # key down
                    held.add(event.code)
                    peak.update(held)
                    if on_update:
                        on_update(_combo_name(peak))
                elif event.value == 0:  # key up
                    held.discard(event.code)
                    if not held and peak:
                        name = _combo_name(peak)
                        log.info("Learned hotkey: %s", name)
                        return name
    finally:
        dev.close()
    return None
