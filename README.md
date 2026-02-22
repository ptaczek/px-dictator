# PX-Dictator

Lightweight Linux voice-to-text with GPU acceleration. Press a hotkey, speak, release — your words appear in the focused application.

~1600 lines of Python. No Electron, no cloud, no accounts. Just a microphone, a GPU, and a hotkey.

## What It Does

```
Hold hotkey → speak → release → text appears in focused app
```

- **Speech-to-text**: NVIDIA Parakeet TDT 0.6B via sherpa-onnx (GPU, ~340 MiB VRAM)
- **Text enhancement** (optional): Qwen3 LLM via llama-server (GPU, 2.5–10 GB VRAM) — fixes grammar, punctuation, removes filler words
- **Paste**: Clipboard + simulated keystroke via uinput (auto-detects terminals on X11; Ctrl+Shift+V on Wayland)
- **GNOME panel indicator**: Shows state (ready/recording/processing), menu for enable/disable, mode switch, preferences

## Requirements

- **Linux** with X11 or Wayland (GNOME tested)
- **NVIDIA GPU** with CUDA 12.x and cuDNN 9.x
- **Python 3.10+**
- **System packages**:
  ```bash
  # X11
  sudo apt install xclip xdotool python3-gi gir1.2-ayatanaappindicator3-0.1
  # Wayland (additionally)
  sudo apt install wl-clipboard
  ```
- **User in `input` group** (for global hotkey via evdev):
  ```bash
  sudo usermod -aG input $USER
  # Logout and login for group change to take effect
  ```

## Installation

### 1. Clone

```bash
git clone https://github.com/ptaczek/px-dictator.git
cd px-dictator
pip3 install -r requirements.txt
```

### 2. Download sherpa-onnx GPU binaries

Version 1.12.23 is the last release with pre-built Linux CUDA binaries.

```bash
cd bin/sherpa-onnx
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.12.23/sherpa-onnx-v1.12.23-cuda-12.x-cudnn-9.x-linux-x64-gpu.tar.bz2
tar xf sherpa-onnx-v1.12.23-cuda-12.x-cudnn-9.x-linux-x64-gpu.tar.bz2 --strip-components=1 \
    --wildcards '*/bin/sherpa-onnx-offline-websocket-server' '*/lib/*.so'
mv lib/*.so . && rmdir lib
mv bin/sherpa-onnx-offline-websocket-server . && rmdir bin
rm sherpa-onnx-v1.12.23-cuda-12.x-cudnn-9.x-linux-x64-gpu.tar.bz2
cd ../..
```

### 3. Build llama-server with CUDA

```bash
git clone https://github.com/ggml-org/llama.cpp.git /tmp/llama-build
cd /tmp/llama-build
cmake -B build -DGGML_CUDA=ON -DGGML_CUDA_FA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc) --target llama-server

# Copy binary and libs
cp build/bin/llama-server ~/path-to/px-dictator/bin/llama/
cp -a build/bin/libggml*.so* ~/path-to/px-dictator/bin/llama/
cp -a build/bin/libllama.so* ~/path-to/px-dictator/bin/llama/
cp -a build/bin/libmtmd.so* ~/path-to/px-dictator/bin/llama/
cd ~ && rm -rf /tmp/llama-build
```

### 4. Download STT model (Parakeet)

```bash
mkdir -p ~/.local/share/px-dictator/models/stt
cd ~/.local/share/px-dictator/models/stt
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
tar xf sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
mv sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 parakeet-tdt-0.6b-v3
rm sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
```

### 5. Download LLM model (optional, for text enhancement)

Download from Preferences dialog, or manually:

```bash
mkdir -p ~/.local/share/px-dictator/models/llm
cd ~/.local/share/px-dictator/models/llm

# Pick one:
wget https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf   # 1.1 GB, ~2.5 GB VRAM
wget https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf       # 2.7 GB, ~5.5 GB VRAM
wget https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf       # 5.0 GB, ~10.2 GB VRAM
```

## Usage

```bash
cd /path/to/px-dictator
python3 -m px_dictator.app        # normal
python3 -m px_dictator.app -v     # verbose logging
```

- **F8** (default): Toggle recording on/off
- **Panel icon**: Right-click for menu — enable/disable, mode, preferences, quit
- **Preferences**: Change hotkey, audio device, activation mode, enhancement prompt, download models
- **Ctrl+C** or **Quit** from menu: Clean shutdown (kills servers, frees VRAM)

### Activation Modes

- **Push-to-talk**: Hold the hotkey to record, release to process
- **Toggle**: Press once to start recording, press again to stop and process

### Hotkey

Any single key or combo works: `KEY_F8`, `KEY_PAUSE`, `KEY_SCROLLLOCK`, `KEY_LEFTCTRL+KEY_F8`, etc.
The hotkey is grabbed at the evdev level — it never reaches applications (no stray characters).

## Configuration

Config file: `~/.config/px-dictator/config.toml` (created on first settings save)

See [config.example.toml](config.example.toml) for all options.

Key settings:
- `[hotkey] key` — evdev key name(s), e.g. `"KEY_F8"` or `"KEY_LEFTCTRL+KEY_SPACE"`
- `[enhancement] enabled` — `true`/`false` to toggle LLM text cleanup
- `[enhancement] system_prompt` — instructions for the LLM (grammar fix, filler removal, etc.)
- `[enhancement] model` — which GGUF file to use
- `[audio] device` — substring match for input device name, or empty for default

## Architecture

```
┌────────────────────────────────────────────────┐
│            GLib Main Loop (Python)             │
│                                                │
│  Indicator ←──── Hotkey (evdev grab+uinput)    │
│  (AyatanaAppIndicator3)     │                  │
│                             ▼                  │
│  sounddevice → sherpa-onnx WS → llama HTTP     │
│  (PCM 16kHz)   (Parakeet GPU)  (Qwen3 GPU)    │
│                                    │           │
│                              clipboard+paste   │
│                        (xclip/wl-copy + uinput) │
│                                                │
│  Server Manager (subprocess lifecycle)         │
│  sherpa-onnx :6006        llama-server :8200   │
└────────────────────────────────────────────────┘
```

## Project Structure

```
px-dictator/
├── px_dictator/
│   ├── app.py           # Entry point, GLib main loop, orchestration
│   ├── config.py        # TOML config with XDG paths
│   ├── enhancer.py      # llama-server HTTP client
│   ├── hotkey.py        # evdev grab + uinput forwarding
│   ├── indicator.py     # GNOME panel icon + menu
│   ├── models.py        # Model registry + HF download
│   ├── paster.py        # Clipboard + paste (X11/Wayland)
│   ├── preferences.py   # GTK3 settings dialog
│   ├── recorder.py      # sounddevice audio capture
│   ├── servers.py       # Server process lifecycle
│   └── transcriber.py   # sherpa-onnx WebSocket client
├── bin/
│   ├── sherpa-onnx/     # GPU STT binary + libs (not in git)
│   └── llama/           # GPU LLM binary + libs (not in git)
├── icons/               # SVG indicator icons
├── config.example.toml
├── install-desktop.sh   # Register in app launcher
├── uninstall-desktop.sh # Remove from app launcher
├── requirements.txt
└── px-dictator.desktop  # Desktop entry / autostart
```

## VRAM Usage

| Component | VRAM |
|-----------|------|
| sherpa-onnx (Parakeet 0.6B INT8) | ~340 MiB |
| llama-server (Qwen3 1.7B Q4_K_M) | ~2.5 GB |
| llama-server (Qwen3 4B Q4_K_M) | ~5.5 GB |
| llama-server (Qwen3 8B Q4_K_M) | ~10.2 GB |

With enhancement disabled, only ~340 MiB is used. VRAM is fully released on disable/quit.

## Application Launcher & Autostart

Run the install script to register PX-Dictator in your desktop application launcher (Super key search). It patches the `.desktop` file with the correct paths and symlinks it into `~/.local/share/applications/`:

```bash
./install-desktop.sh              # launcher only
./install-desktop.sh --autostart  # launcher + autostart on login
```

To remove:

```bash
./uninstall-desktop.sh              # launcher only
./uninstall-desktop.sh --autostart  # launcher + autostart
```

## License

MIT
