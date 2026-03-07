"""Speech-to-text clients for sherpa-onnx (WebSocket) and whisper.cpp (HTTP)."""

import io
import logging
import struct
import wave

import httpx
import numpy as np
from websockets.sync.client import connect

log = logging.getLogger(__name__)


def transcribe(samples: np.ndarray, cfg: dict) -> str:
    """Dispatch to the configured STT engine."""
    tc = cfg["transcription"]
    engine = tc.get("engine", "sherpa-onnx")
    sample_rate = cfg["audio"]["sample_rate"]

    if engine == "whisper":
        language = tc.get("whisper_language", "auto")
        translate = tc.get("whisper_translate", False)
        return transcribe_whisper(samples, sample_rate, tc["host"], tc["port"],
                                  language, translate)
    return transcribe_sherpa(samples, sample_rate, tc["host"], tc["port"])


def transcribe_sherpa(samples: np.ndarray, sample_rate: int,
                      host: str, port: int) -> str:
    """Send audio samples to sherpa-onnx WS server and return transcribed text.

    Protocol:
        Send binary: [int32LE sample_rate][int32LE num_audio_bytes][float32LE samples...]
        Recv text:   JSON {"text": "..."}  (may also be plain text)
        Send text:   "Done"
    """
    if len(samples) == 0:
        return ""

    samples_bytes = samples.astype(np.float32).tobytes()
    header = struct.pack("<ii", sample_rate, len(samples_bytes))
    payload = header + samples_bytes

    uri = f"ws://{host}:{port}"
    log.debug("Connecting to sherpa-onnx at %s (%d samples)", uri, len(samples))

    with connect(uri) as ws:
        ws.send(payload)

        result = ""
        for msg in ws:
            result += str(msg)
            ws.send("Done")
            break  # single response expected for offline mode

    # Parse JSON or use raw text
    text = result.strip()
    if text.startswith("{"):
        import json
        try:
            text = json.loads(text).get("text", text).strip()
        except json.JSONDecodeError:
            pass

    log.info("Transcribed (sherpa): %s", text[:80])
    return text


def transcribe_whisper(samples: np.ndarray, sample_rate: int,
                       host: str, port: int, language: str = "auto",
                       translate: bool = False) -> str:
    """Send audio to whisper.cpp server and return transcribed text."""
    if len(samples) == 0:
        return ""

    wav_data = _samples_to_wav(samples, sample_rate)
    url = f"http://{host}:{port}/inference"

    log.debug("Sending %d samples to whisper at %s (lang=%s, translate=%s)",
              len(samples), url, language, translate)

    files = {"file": ("audio.wav", wav_data, "audio/wav")}
    data = {"response_format": "json", "temperature": "0.0", "language": language,
            "translate": "true" if translate else "false"}

    r = httpx.post(url, files=files, data=data, timeout=60)
    r.raise_for_status()

    result = r.json()
    text = result.get("text", "").strip()

    log.info("Transcribed (whisper): %s", text[:80])
    return text


def _samples_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """Convert float32 numpy array to WAV bytes (16-bit PCM)."""
    buf = io.BytesIO()
    int_samples = (samples * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int_samples.tobytes())
    return buf.getvalue()
