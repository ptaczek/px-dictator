"""Audio capture using pw-record (PipeWire native)."""

import logging
import os
import signal
import subprocess
import threading

import numpy as np

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, cfg):
        ac = cfg["audio"]
        self.sample_rate = ac.get("sample_rate", 16000)
        self._device = ac.get("device", "")
        self._chunks = []
        self._proc = None
        self._reader_thread = None

    def start(self):
        self._chunks.clear()
        cmd = ["pw-record", f"--rate={self.sample_rate}", "--channels=1", "--format=f32", "-"]
        if self._device:
            cmd.insert(1, f"--target={self._device}")
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
        )
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        log.debug("Recording started (device=%s, rate=%d)", self._device or "default", self.sample_rate)

    def _reader(self):
        fd = self._proc.stdout.fileno()
        while True:
            data = os.read(fd, 65536)
            if not data:
                break
            self._chunks.append(data)

    def stop(self):
        """Stop recording and return samples as float32 numpy array."""
        if self._proc:
            self._proc.send_signal(signal.SIGINT)
            self._proc.wait()
            self._proc = None
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        if not self._chunks:
            return np.array([], dtype=np.float32)
        raw = b"".join(self._chunks)
        self._chunks.clear()
        samples = np.frombuffer(raw, dtype=np.float32)
        log.debug("Recording stopped: %.2fs, %d samples", len(samples) / self.sample_rate, len(samples))
        return samples
