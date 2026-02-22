"""Sherpa-ONNX offline WebSocket client."""

import logging
import struct

import numpy as np
from websockets.sync.client import connect

log = logging.getLogger(__name__)


def transcribe(samples: np.ndarray, sample_rate: int, host: str, port: int) -> str:
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

    log.info("Transcribed: %s", text[:80])
    return text
