"""Audio capture using sounddevice."""

import logging

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, cfg):
        ac = cfg["audio"]
        self.sample_rate = ac.get("sample_rate", 16000)
        self._device = self._resolve_device(ac.get("device", ""))
        self._chunks = []
        self._stream = None

    def _resolve_device(self, spec):
        """Resolve device spec: empty=default, int=index, str=substring match."""
        if not spec:
            return None
        try:
            return int(spec)
        except ValueError:
            pass
        for info in sd.query_devices():
            if spec.lower() in info["name"].lower() and info["max_input_channels"] > 0:
                log.info("Audio device matched: %s (index %d)", info["name"], info["index"])
                return info["index"]
        log.warning("Audio device '%s' not found, using default", spec)
        return None

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("sounddevice status: %s", status)
        self._chunks.append(indata[:, 0].copy())

    def start(self):
        self._chunks.clear()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()
        log.debug("Recording started (device=%s, rate=%d)", self._device, self.sample_rate)

    def stop(self):
        """Stop recording and return samples as float32 numpy array."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._chunks:
            return np.array([], dtype=np.float32)
        samples = np.concatenate(self._chunks)
        self._chunks.clear()
        log.debug("Recording stopped: %.2fs, %d samples", len(samples) / self.sample_rate, len(samples))
        return samples
