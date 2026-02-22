"""Model registry, download, and deletion."""

import logging
import os
import shutil
from pathlib import Path

import httpx

from . import config

log = logging.getLogger(__name__)

STT_DIR = config.DATA_DIR / "models" / "stt"
LLM_DIR = config.DATA_DIR / "models" / "llm"

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
