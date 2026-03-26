"""GTK3 preferences dialog with model management."""

import logging
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

import sounddevice as sd

from gi.repository import GdkPixbuf

from . import config, models, hotkey

ICON_PATH = str(config.PROJECT_DIR / "icons" / "pxd-ready.svg")

log = logging.getLogger(__name__)


def _format_size(size_bytes):
    """Format byte count as human-readable string (MB or GB)."""
    if size_bytes >= 1e9:
        return f"{size_bytes / 1e9:.1f} GB"
    return f"{size_bytes / 1e6:.0f} MB"


def show_preferences(cfg, on_save=None, hotkey=None):
    """Show modal preferences dialog. Calls on_save(new_cfg) on OK."""
    dialog = PreferencesDialog(cfg, on_save, hotkey)
    dialog.show_all()
    dialog.present_with_time(Gdk.CURRENT_TIME)


class PreferencesDialog(Gtk.Window):
    def __init__(self, cfg, on_save=None, hotkey=None):
        super().__init__(title="PX-Dictator Preferences")
        self.set_default_size(520, 480)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_from_file(ICON_PATH)
        self.cfg = {s: dict(v) if isinstance(v, dict) else v for s, v in cfg.items()}
        self._on_save = on_save
        self._hotkey = hotkey
        self._downloading = False

        notebook = Gtk.Notebook()
        notebook.set_margin_top(8)
        notebook.set_margin_bottom(8)
        notebook.set_margin_start(8)
        notebook.set_margin_end(8)

        notebook.append_page(self._build_general_tab(), Gtk.Label(label="General"))
        notebook.append_page(self._build_audio_tab(), Gtk.Label(label="Audio"))
        notebook.append_page(self._build_hotkey_tab(), Gtk.Label(label="Hotkey"))
        notebook.append_page(self._build_transcription_tab(), Gtk.Label(label="Transcription"))
        notebook.append_page(self._build_enhancement_tab(), Gtk.Label(label="Enhancement"))

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.pack_start(notebook, True, True, 0)

        # Bottom buttons
        hbox = Gtk.Box(spacing=8)
        hbox.set_margin_bottom(8)
        hbox.set_margin_end(8)
        hbox.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.destroy())
        ok_btn = Gtk.Button(label="OK")
        ok_btn.connect("clicked", self._on_ok)
        hbox.pack_start(cancel_btn, False, False, 0)
        hbox.pack_start(ok_btn, False, False, 0)
        vbox.pack_start(hbox, False, False, 0)

        self.add(vbox)

    def _build_general_tab(self):
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_top(12)
        grid.set_margin_start(12)

        self._enable_switch = Gtk.Switch()
        self._enable_switch.set_active(self.cfg["general"]["enabled"])
        grid.attach(Gtk.Label(label="Enabled:", xalign=0), 0, 0, 1, 1)
        grid.attach(self._enable_switch, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Activation mode:", xalign=0), 0, 1, 1, 1)
        self._mode_combo = Gtk.ComboBoxText()
        self._mode_combo.append_text("push-to-talk")
        self._mode_combo.append_text("toggle")
        mode = self.cfg["general"]["activation_mode"]
        self._mode_combo.set_active(0 if mode == "push-to-talk" else 1)
        grid.attach(self._mode_combo, 1, 1, 1, 1)

        return grid

    def _build_audio_tab(self):
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_top(12)
        grid.set_margin_start(12)

        grid.attach(Gtk.Label(label="Input device:", xalign=0), 0, 0, 1, 1)
        self._device_combo = Gtk.ComboBoxText()
        self._device_combo.append_text("(Default)")
        current = self.cfg["audio"].get("device", "")
        active_idx = 0
        # ALSA backend aliases that duplicate real PipeWire devices
        _ALSA_ALIASES = {"pipewire", "pulse", "default", "sysdefault", "hw", "plughw",
                         "dmix", "dsnoop", "surround21", "surround40", "surround41",
                         "surround50", "surround51", "surround71"}
        combo_idx = 0
        for info in sd.query_devices():
            if info["max_input_channels"] > 0:
                if info["name"].lower() in _ALSA_ALIASES:
                    continue
                combo_idx += 1
                name = f"{info['name']} (#{info['index']})"
                self._device_combo.append_text(name)
                if current and (str(info["index"]) == current or
                                current.lower() in info["name"].lower()):
                    active_idx = combo_idx
        self._device_combo.set_active(active_idx)
        grid.attach(self._device_combo, 1, 0, 1, 1)

        return grid

    def _build_hotkey_tab(self):
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_top(12)
        grid.set_margin_start(12)

        grid.attach(Gtk.Label(label="Current hotkey:", xalign=0), 0, 0, 1, 1)
        self._hotkey_label = Gtk.Label(label=self.cfg["hotkey"]["key"])
        grid.attach(self._hotkey_label, 1, 0, 1, 1)

        learn_btn = Gtk.Button(label="Set Hotkey...")
        learn_btn.connect("clicked", self._on_learn_hotkey)
        grid.attach(learn_btn, 2, 0, 1, 1)

        return grid

    # --- Language lists per engine ---

    _COMMON_LANGUAGES = [
        ("en", "English"),
        ("cs", "Czech"),
        ("sk", "Slovak"),
        ("de", "German"),
        ("fr", "French"),
        ("es", "Spanish"),
        ("it", "Italian"),
        ("pt", "Portuguese"),
        ("pl", "Polish"),
        ("nl", "Dutch"),
        ("ru", "Russian"),
        ("uk", "Ukrainian"),
        ("ja", "Japanese"),
        ("zh", "Chinese"),
        ("ko", "Korean"),
        ("tr", "Turkish"),
        ("ar", "Arabic"),
        ("hi", "Hindi"),
        ("sv", "Swedish"),
        ("da", "Danish"),
        ("fi", "Finnish"),
        ("he", "Hebrew"),
    ]

    _WHISPER_LANGUAGES = [("auto", "Auto-detect")] + _COMMON_LANGUAGES
    _SHERPA_LANGUAGES = [("auto", "Auto-detect")] + _COMMON_LANGUAGES

    def _build_transcription_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)

        # Engine selector
        hbox = Gtk.Box(spacing=12)
        hbox.pack_start(Gtk.Label(label="Engine:", xalign=0), False, False, 0)
        self._engine_combo = Gtk.ComboBoxText()
        self._engine_combo.append_text("sherpa-onnx")
        self._engine_combo.append_text("whisper")
        engine = self.cfg["transcription"].get("engine", "sherpa-onnx")
        self._engine_combo.set_active(0 if engine == "sherpa-onnx" else 1)
        self._engine_combo.connect("changed", self._on_engine_changed)
        hbox.pack_start(self._engine_combo, False, False, 0)
        vbox.pack_start(hbox, False, False, 0)

        # Language selector
        lang_hbox = Gtk.Box(spacing=12)
        lang_hbox.pack_start(Gtk.Label(label="Language:", xalign=0), False, False, 0)
        self._stt_lang_combo = Gtk.ComboBoxText()
        self._stt_lang_codes = []
        lang_hbox.pack_start(self._stt_lang_combo, False, False, 0)
        vbox.pack_start(lang_hbox, False, False, 0)

        # Translate to English toggle (Whisper only)
        self._translate_hbox = Gtk.Box(spacing=12)
        self._translate_hbox.pack_start(
            Gtk.Label(label="Translate to English:", xalign=0), False, False, 0)
        self._translate_switch = Gtk.Switch()
        self._translate_switch.set_active(
            self.cfg["transcription"].get("whisper_translate", False))
        self._translate_hbox.pack_start(self._translate_switch, False, False, 0)
        vbox.pack_start(self._translate_hbox, False, False, 0)

        # Model list
        vbox.pack_start(Gtk.Label(label="Models:", xalign=0), False, False, 4)
        self._stt_model_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.pack_start(self._stt_model_box, True, True, 0)

        self._refresh_stt_languages()
        self._refresh_stt_models()
        self._update_translate_visibility()

        return vbox

    def _on_engine_changed(self, combo):
        self.cfg["transcription"]["engine"] = combo.get_active_text()
        self._refresh_stt_languages()
        self._refresh_stt_models()
        self._update_translate_visibility()

    def _update_translate_visibility(self):
        is_whisper = self._engine_combo.get_active_text() == "whisper"
        self._translate_hbox.set_visible(is_whisper)
        self._translate_hbox.set_no_show_all(not is_whisper)

    def _refresh_stt_languages(self):
        engine = self._engine_combo.get_active_text()
        if engine == "whisper":
            languages = self._WHISPER_LANGUAGES
            current = self.cfg["transcription"].get("whisper_language", "auto")
        else:
            languages = self._SHERPA_LANGUAGES
            current = self.cfg["transcription"].get("sherpa_language", "en")

        self._stt_lang_combo.remove_all()
        self._stt_lang_codes = [code for code, _ in languages]
        for code, name in languages:
            self._stt_lang_combo.append_text(f"{name} ({code})")

        try:
            self._stt_lang_combo.set_active(self._stt_lang_codes.index(current))
        except ValueError:
            self._stt_lang_combo.set_active(0)

    def _refresh_stt_models(self):
        for child in self._stt_model_box.get_children():
            child.destroy()

        engine = self._engine_combo.get_active_text()
        if engine == "whisper":
            entries = models.list_whisper()
            active = self.cfg["transcription"].get("whisper_model", "")
        else:
            entries = models.list_sherpa()
            active = self.cfg["transcription"].get("model", "")

        for entry in entries:
            row = Gtk.Box(spacing=8)
            row.set_margin_start(4)

            size_str = _format_size(entry["size_bytes"])
            label = Gtk.Label(label=f"{entry['name']}  ({size_str})", xalign=0)
            label.set_hexpand(True)
            row.pack_start(label, True, True, 0)

            # Model key: filename for whisper, dirname for sherpa
            key = entry.get("filename") or entry.get("dirname", "")

            if entry["installed"]:
                if key == active:
                    status = Gtk.Label(label="(Active)")
                    row.pack_start(status, False, False, 0)
                    unload_btn = Gtk.Button(label="Unload")
                    unload_btn.connect("clicked", self._on_unload_stt_model)
                    row.pack_start(unload_btn, False, False, 0)
                else:
                    use_btn = Gtk.Button(label="Use")
                    use_btn.connect("clicked", self._on_use_stt_model, key)
                    row.pack_start(use_btn, False, False, 0)

                del_btn = Gtk.Button(label="Delete")
                del_btn.connect("clicked", self._on_delete_stt_model, key)
                row.pack_start(del_btn, False, False, 0)
            else:
                dl_btn = Gtk.Button(label="Download")
                dl_btn.connect("clicked", self._on_download_stt_model, entry)
                row.pack_start(dl_btn, False, False, 0)

            self._stt_model_box.pack_start(row, False, False, 0)

        self._stt_model_box.show_all()

    def _on_unload_stt_model(self, btn):
        engine = self._engine_combo.get_active_text()
        if engine == "whisper":
            self.cfg["transcription"]["whisper_model"] = ""
        else:
            self.cfg["transcription"]["model"] = ""
        self._refresh_stt_models()

    def _on_use_stt_model(self, btn, key):
        engine = self._engine_combo.get_active_text()
        if engine == "whisper":
            self.cfg["transcription"]["whisper_model"] = key
        else:
            self.cfg["transcription"]["model"] = key
        self._refresh_stt_models()

    def _on_delete_stt_model(self, btn, key):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete {key}?",
        )
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.YES:
            return

        engine = self._engine_combo.get_active_text()
        if engine == "whisper":
            models.delete_whisper(key)
            if self.cfg["transcription"]["whisper_model"] == key:
                installed = [e for e in models.list_whisper() if e["installed"]]
                self.cfg["transcription"]["whisper_model"] = (
                    installed[0]["filename"] if installed else ""
                )
        else:
            models.delete_sherpa(key)
            if self.cfg["transcription"]["model"] == key:
                installed = [e for e in models.list_sherpa() if e["installed"]]
                self.cfg["transcription"]["model"] = (
                    installed[0]["dirname"] if installed else ""
                )
        self._refresh_stt_models()

    def _on_download_stt_model(self, btn, entry):
        if self._downloading:
            return
        self._downloading = True
        btn.set_sensitive(False)
        btn.set_label("0%")

        engine = self._engine_combo.get_active_text()
        download_fn = models.download_whisper if engine == "whisper" else models.download_sherpa

        def progress_cb(downloaded, total):
            pct = int(downloaded * 100 / total) if total else 0
            GLib.idle_add(btn.set_label, f"{pct}%")

        def do_download():
            try:
                download_fn(entry["id"], progress_cb=progress_cb)
                GLib.idle_add(self._refresh_stt_models)
            except Exception as e:
                log.error("STT model download failed: %s", e)
                GLib.idle_add(btn.set_label, "Failed")
                GLib.idle_add(btn.set_sensitive, True)
            finally:
                self._downloading = False

        threading.Thread(target=do_download, daemon=True).start()

    def _build_enhancement_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)

        # Enable switch
        hbox = Gtk.Box(spacing=12)
        hbox.pack_start(Gtk.Label(label="Enable text enhancement:", xalign=0), False, False, 0)
        self._enh_switch = Gtk.Switch()
        self._enh_switch.set_active(self.cfg["enhancement"]["enabled"])
        hbox.pack_start(self._enh_switch, False, False, 0)
        vbox.pack_start(hbox, False, False, 0)

        # System prompt
        vbox.pack_start(Gtk.Label(label="System prompt:", xalign=0), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(80)
        self._prompt_view = Gtk.TextView()
        self._prompt_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self._prompt_view.get_buffer().set_text(self.cfg["enhancement"]["system_prompt"])
        scroll.add(self._prompt_view)
        vbox.pack_start(scroll, False, True, 0)

        # Model list
        vbox.pack_start(Gtk.Label(label="Models:", xalign=0), False, False, 4)
        self._model_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._refresh_model_list()
        vbox.pack_start(self._model_box, True, True, 0)

        return vbox

    def _refresh_model_list(self):
        for child in self._model_box.get_children():
            child.destroy()

        active_model = self.cfg["enhancement"]["model"]
        for entry in models.list_installed():
            row = Gtk.Box(spacing=8)
            row.set_margin_start(4)

            label_text = f"{entry['name']}  ({entry['size_bytes'] / 1e9:.1f} GB)"
            label = Gtk.Label(label=label_text, xalign=0)
            label.set_hexpand(True)
            row.pack_start(label, True, True, 0)

            if entry["installed"]:
                if entry["filename"] == active_model:
                    status = Gtk.Label(label="(Active)")
                    status.modify_fg(Gtk.StateFlags.NORMAL,
                                     label.get_style_context()
                                     .get_color(Gtk.StateFlags.NORMAL).to_color()
                                     if hasattr(label.get_style_context()
                                                .get_color(Gtk.StateFlags.NORMAL), 'to_color')
                                     else None)
                    row.pack_start(status, False, False, 0)
                else:
                    use_btn = Gtk.Button(label="Use")
                    use_btn.connect("clicked", self._on_use_model, entry["filename"])
                    row.pack_start(use_btn, False, False, 0)

                del_btn = Gtk.Button(label="Delete")
                del_btn.connect("clicked", self._on_delete_model, entry["filename"])
                row.pack_start(del_btn, False, False, 0)
            else:
                dl_btn = Gtk.Button(label="Download")
                dl_btn.connect("clicked", self._on_download_model, entry)
                row.pack_start(dl_btn, False, False, 0)

            self._model_box.pack_start(row, False, False, 0)

        self._model_box.show_all()

    def _on_use_model(self, btn, filename):
        self.cfg["enhancement"]["model"] = filename
        self._refresh_model_list()

    def _on_delete_model(self, btn, filename):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete {filename}?",
        )
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            models.delete(filename)
            if self.cfg["enhancement"]["model"] == filename:
                # Switch to first available
                installed = [e for e in models.list_installed() if e["installed"]]
                self.cfg["enhancement"]["model"] = installed[0]["filename"] if installed else ""
            self._refresh_model_list()

    def _on_download_model(self, btn, entry):
        if self._downloading:
            return
        self._downloading = True
        btn.set_sensitive(False)
        btn.set_label("0%")

        def progress_cb(downloaded, total):
            pct = int(downloaded * 100 / total) if total else 0
            GLib.idle_add(btn.set_label, f"{pct}%")

        def do_download():
            try:
                models.download(entry["id"], progress_cb=progress_cb)
                GLib.idle_add(self._refresh_model_list)
            except Exception as e:
                log.error("Download failed: %s", e)
                GLib.idle_add(btn.set_label, "Failed")
                GLib.idle_add(btn.set_sensitive, True)
            finally:
                self._downloading = False

        threading.Thread(target=do_download, daemon=True).start()

    def _on_learn_hotkey(self, btn):
        self._hotkey_label.set_text("Press a key...")
        btn.set_sensitive(False)

        def do_learn():
            # Release keyboard grab so learn_key can receive events
            if self._hotkey:
                self._hotkey.pause_grab()
            try:
                key = hotkey.learn_key(
                    self.cfg["hotkey"].get("device", ""),
                    on_update=lambda name: GLib.idle_add(
                        self._hotkey_label.set_text, name),
                )
            finally:
                if self._hotkey:
                    self._hotkey.resume_grab()
            if key:
                self.cfg["hotkey"]["key"] = key
                GLib.idle_add(self._hotkey_label.set_text, key)
            else:
                GLib.idle_add(self._hotkey_label.set_text, self.cfg["hotkey"]["key"])
            GLib.idle_add(btn.set_sensitive, True)

        threading.Thread(target=do_learn, daemon=True).start()

    def _on_ok(self, btn):
        # Gather values
        self.cfg["general"]["enabled"] = self._enable_switch.get_active()
        self.cfg["general"]["activation_mode"] = self._mode_combo.get_active_text()

        device_text = self._device_combo.get_active_text()
        if device_text == "(Default)":
            self.cfg["audio"]["device"] = ""
        else:
            # Extract name before " (#N)"
            self.cfg["audio"]["device"] = device_text.rsplit(" (#", 1)[0]

        engine = self._engine_combo.get_active_text()
        self.cfg["transcription"]["engine"] = engine
        lang_idx = self._stt_lang_combo.get_active()
        lang_code = self._stt_lang_codes[lang_idx] if lang_idx >= 0 else ""
        if engine == "whisper":
            self.cfg["transcription"]["whisper_language"] = lang_code or "auto"
            self.cfg["transcription"]["whisper_translate"] = self._translate_switch.get_active()
        else:
            self.cfg["transcription"]["sherpa_language"] = lang_code or "en"

        self.cfg["enhancement"]["enabled"] = self._enh_switch.get_active()
        buf = self._prompt_view.get_buffer()
        self.cfg["enhancement"]["system_prompt"] = buf.get_text(
            buf.get_start_iter(), buf.get_end_iter(), True
        )

        if self._on_save:
            self._on_save(self.cfg)
        self.destroy()
