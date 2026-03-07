"""Model registry, download, and deletion."""

import logging
import os
import shutil
import tarfile
from pathlib import Path

import httpx

from . import config

log = logging.getLogger(__name__)

STT_DIR = config.DATA_DIR / "models" / "stt"
WHISPER_DIR = config.DATA_DIR / "models" / "whisper"
LLM_DIR = config.DATA_DIR / "models" / "llm"

# Built-in Sherpa-ONNX STT model registry
SHERPA_REGISTRY = [
    {
        "id": "parakeet-tdt-0.6b-v3",
        "name": "Parakeet TDT 0.6B (int8)",
        "dirname": "parakeet-tdt-0.6b-v3",
        "archive_dirname": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
        "size_bytes": 671_247_192,
        "description": "English, ~340 MiB VRAM",
    },
]

# Built-in Whisper STT model registry
WHISPER_REGISTRY = [
    {
        "id": "whisper-small",
        "name": "Small",
        "filename": "ggml-small.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
        "size_bytes": 488_808_166,
        "description": "Fast, ~1 GB VRAM",
    },
    {
        "id": "whisper-medium",
        "name": "Medium",
        "filename": "ggml-medium.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
        "size_bytes": 1_533_774_781,
        "description": "Best balance, ~2.5 GB VRAM",
    },
    {
        "id": "whisper-large-v3",
        "name": "Large",
        "filename": "ggml-large-v3.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
        "size_bytes": 3_095_033_483,
        "description": "Best quality, ~5 GB VRAM",
    },
    {
        "id": "whisper-large-v3-turbo",
        "name": "Turbo",
        "filename": "ggml-large-v3-turbo.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
        "size_bytes": 1_649_865_339,
        "description": "Near-large quality, much faster, ~2.5 GB VRAM",
    },
]

# Built-in LLM model registry
LLM_REGISTRY = [
    {
        "id": "qwen3-8b-q4km",
        "name": "Qwen3 8B (Q4_K_M)",
        "filename": "Qwen3-8B-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
        "size_bytes": 5_012_462_592,
        "description": "Best quality, ~10.2 GB VRAM",
    },
    {
        "id": "qwen3-4b-q4km",
        "name": "Qwen3 4B (Q4_K_M)",
        "filename": "Qwen3-4B-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
        "size_bytes": 2_726_118_400,
        "description": "Good balance, ~5.5 GB VRAM",
    },
    {
        "id": "qwen3-1.7b-q4km",
        "name": "Qwen3 1.7B (Q4_K_M)",
        "filename": "Qwen3-1.7B-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
        "size_bytes": 1_117_798_400,
        "description": "Fastest, ~2.5 GB VRAM",
    },
]


def list_installed():
    """Return list of dicts: registry entries augmented with 'installed' bool."""
    installed_files = set()
    if LLM_DIR.exists():
        installed_files = {f.name for f in LLM_DIR.iterdir() if f.suffix == ".gguf"}

    result = []
    for entry in LLM_REGISTRY:
        e = dict(entry)
        e["installed"] = entry["filename"] in installed_files
        result.append(e)

    # Also list any installed models not in registry
    registry_files = {e["filename"] for e in LLM_REGISTRY}
    for fname in sorted(installed_files - registry_files):
        result.append({
            "id": fname,
            "name": fname,
            "filename": fname,
            "url": "",
            "size_bytes": (LLM_DIR / fname).stat().st_size,
            "description": "Custom model",
            "installed": True,
        })
    return result


def download(model_id, progress_cb=None):
    """Download a model from HF. progress_cb(bytes_downloaded, total_bytes)."""
    entry = next((e for e in LLM_REGISTRY if e["id"] == model_id), None)
    if entry is None:
        raise ValueError(f"Unknown model: {model_id}")

    LLM_DIR.mkdir(parents=True, exist_ok=True)
    target = LLM_DIR / entry["filename"]
    tmp = target.with_suffix(".tmp")

    # Check disk space
    stat = shutil.disk_usage(LLM_DIR)
    if stat.free < entry["size_bytes"] * 1.1:
        raise RuntimeError(
            f"Not enough disk space: {stat.free / 1e9:.1f} GB free, "
            f"need {entry['size_bytes'] / 1e9:.1f} GB"
        )

    log.info("Downloading %s (%.1f GB)", entry["name"], entry["size_bytes"] / 1e9)
    try:
        with httpx.stream("GET", entry["url"], follow_redirects=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", entry["size_bytes"]))
            downloaded = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
        # Atomic rename
        tmp.rename(target)
        log.info("Downloaded %s", entry["filename"])
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def delete(filename):
    """Delete a model file."""
    target = LLM_DIR / filename
    if target.exists():
        target.unlink()
        log.info("Deleted model %s", filename)


def model_path(filename):
    return LLM_DIR / filename


# --- Whisper model management ---

def list_whisper():
    """Return list of dicts: whisper registry entries augmented with 'installed' bool."""
    installed_files = set()
    if WHISPER_DIR.exists():
        installed_files = {f.name for f in WHISPER_DIR.iterdir() if f.suffix == ".bin"}

    result = []
    for entry in WHISPER_REGISTRY:
        e = dict(entry)
        e["installed"] = entry["filename"] in installed_files
        result.append(e)

    # Also list any installed models not in registry
    registry_files = {e["filename"] for e in WHISPER_REGISTRY}
    for fname in sorted(installed_files - registry_files):
        result.append({
            "id": fname,
            "name": fname,
            "filename": fname,
            "url": "",
            "size_bytes": (WHISPER_DIR / fname).stat().st_size,
            "description": "Custom model",
            "installed": True,
        })
    return result


def download_whisper(model_id, progress_cb=None):
    """Download a Whisper model from HF. progress_cb(bytes_downloaded, total_bytes)."""
    entry = next((e for e in WHISPER_REGISTRY if e["id"] == model_id), None)
    if entry is None:
        raise ValueError(f"Unknown whisper model: {model_id}")

    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    target = WHISPER_DIR / entry["filename"]
    tmp = target.with_suffix(".tmp")

    stat = shutil.disk_usage(WHISPER_DIR)
    if stat.free < entry["size_bytes"] * 1.1:
        raise RuntimeError(
            f"Not enough disk space: {stat.free / 1e9:.1f} GB free, "
            f"need {entry['size_bytes'] / 1e9:.1f} GB"
        )

    log.info("Downloading %s (%.1f GB)", entry["name"], entry["size_bytes"] / 1e9)
    try:
        with httpx.stream("GET", entry["url"], follow_redirects=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", entry["size_bytes"]))
            downloaded = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
        tmp.rename(target)
        log.info("Downloaded %s", entry["filename"])
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def delete_whisper(filename):
    """Delete a Whisper model file."""
    target = WHISPER_DIR / filename
    if target.exists():
        target.unlink()
        log.info("Deleted whisper model %s", filename)


def whisper_model_path(filename):
    return WHISPER_DIR / filename


# --- Sherpa-ONNX model management ---

def list_sherpa():
    """Return list of dicts: sherpa registry entries augmented with 'installed' bool."""
    installed_dirs = set()
    if STT_DIR.exists():
        installed_dirs = {d.name for d in STT_DIR.iterdir() if d.is_dir()}

    result = []
    for entry in SHERPA_REGISTRY:
        e = dict(entry)
        e["installed"] = entry["dirname"] in installed_dirs
        result.append(e)

    registry_dirs = {e["dirname"] for e in SHERPA_REGISTRY}
    for dirname in sorted(installed_dirs - registry_dirs):
        dpath = STT_DIR / dirname
        size = sum(f.stat().st_size for f in dpath.rglob("*") if f.is_file())
        result.append({
            "id": dirname,
            "name": dirname,
            "dirname": dirname,
            "archive_dirname": "",
            "url": "",
            "size_bytes": size,
            "description": "Custom model",
            "installed": True,
        })
    return result


def download_sherpa(model_id, progress_cb=None):
    """Download a sherpa-onnx model archive, extract, and rename."""
    entry = next((e for e in SHERPA_REGISTRY if e["id"] == model_id), None)
    if entry is None:
        raise ValueError(f"Unknown sherpa model: {model_id}")

    STT_DIR.mkdir(parents=True, exist_ok=True)
    target_dir = STT_DIR / entry["dirname"]
    tmp_archive = STT_DIR / f"{entry['dirname']}.tar.bz2.tmp"

    stat = shutil.disk_usage(STT_DIR)
    if stat.free < entry["size_bytes"] * 2.5:
        raise RuntimeError(
            f"Not enough disk space: {stat.free / 1e9:.1f} GB free, "
            f"need ~{entry['size_bytes'] * 2.5 / 1e9:.1f} GB (archive + extracted)"
        )

    log.info("Downloading %s", entry["name"])
    try:
        with httpx.stream("GET", entry["url"], follow_redirects=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", entry["size_bytes"]))
            downloaded = 0
            with open(tmp_archive, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)

        log.info("Extracting %s", tmp_archive.name)
        with tarfile.open(tmp_archive, "r:bz2") as tar:
            tar.extractall(STT_DIR)

        # Rename extracted directory to expected name
        extracted = STT_DIR / entry["archive_dirname"]
        if extracted.exists() and extracted != target_dir:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            extracted.rename(target_dir)

        log.info("Installed sherpa model %s", entry["dirname"])
    except Exception:
        # Cleanup partial state
        extracted = STT_DIR / entry["archive_dirname"]
        if extracted.exists():
            shutil.rmtree(extracted)
        raise
    finally:
        if tmp_archive.exists():
            tmp_archive.unlink()


def delete_sherpa(dirname):
    """Delete a sherpa-onnx model directory."""
    target = STT_DIR / dirname
    if target.exists() and target.is_dir():
        shutil.rmtree(target)
        log.info("Deleted sherpa model %s", dirname)
