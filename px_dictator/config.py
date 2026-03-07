"""TOML config load/save with XDG paths and sensible defaults."""

import os
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path
from copy import deepcopy

try:
    import tomli_w
except ImportError:
    tomli_w = None


def _xdg(var, fallback):
    return Path(os.environ.get(var, Path.home() / fallback))


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "px-dictator"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "px-dictator"
PROJECT_DIR = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "general": {
        "enabled": True,
        "activation_mode": "push-to-talk",
    },
    "audio": {
        "device": "",
        "sample_rate": 16000,
    },
    "hotkey": {
        "key": "KEY_RIGHTALT",
        "device": "",
    },
    "transcription": {
        "engine": "sherpa-onnx",
        "host": "127.0.0.1",
        "port": 6006,
        "model": "parakeet-tdt-0.6b-v3",
        "sherpa_language": "en",
        "whisper_model": "ggml-medium.bin",
        "whisper_language": "auto",
        "whisper_translate": False,
    },
    "enhancement": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8200,
        "model": "Qwen3-1.7B-Q8_0.gguf",
        "gpu_layers": 99,
        "ctx_size": 4096,
        "system_prompt": (
            "Fix grammar, punctuation and casing. "
            "Remove filler words (um, uh, like, you know, sort of, kind of, I mean, basically, actually, right). "
            "Keep the original meaning. Output only the corrected text."
        ),
    },
    "paste": {
        "session": "auto",
        "delay_ms": 100,
        "terminal_classes": [
            "kitty", "Alacritty", "gnome-terminal-server", "tilix",
            "konsole", "xfce4-terminal", "terminator", "st",
            "urxvt", "xterm", "foot", "wezterm",
        ],
    },
}


def _merge(defaults, overrides):
    """Recursively merge overrides into defaults (defaults win for missing keys)."""
    result = deepcopy(defaults)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


def config_path():
    return CONFIG_DIR / "config.toml"


def load(path=None):
    """Load config from TOML, merged with defaults."""
    p = Path(path) if path else config_path()
    if p.exists():
        with open(p, "rb") as f:
            user = tomllib.load(f)
        return _merge(DEFAULTS, user)
    return deepcopy(DEFAULTS)


def save(cfg, path=None):
    """Save config to TOML. Requires tomli_w."""
    if tomli_w is None:
        # Fallback: write manually for simple TOML
        p = Path(path) if path else config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            _write_toml(f, cfg)
        return
    p = Path(path) if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        tomli_w.dump(cfg, f)


def _write_toml(f, cfg, prefix=""):
    """Minimal TOML writer for flat sections with simple values."""
    for section, values in cfg.items():
        if isinstance(values, dict):
            f.write(f"\n[{prefix}{section}]\n")
            for k, v in values.items():
                f.write(f"{k} = {_toml_val(v)}\n")
        else:
            f.write(f"{section} = {_toml_val(values)}\n")


def _toml_val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        if "\n" in v:
            return f'"""\n{v}"""'
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(_toml_val(i) for i in v)
        return f"[{items}]"
    return repr(v)
