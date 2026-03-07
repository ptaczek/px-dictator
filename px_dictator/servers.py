"""Child process lifecycle for sherpa-onnx, whisper-server, and llama-server."""

import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from . import config

log = logging.getLogger(__name__)

BIN_DIR = config.PROJECT_DIR / "bin"


class Server:
    def __init__(self, name):
        self.name = name
        self.proc = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def _kill(self):
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            log.warning("%s did not exit after SIGTERM, sending SIGKILL", self.name)
            self.proc.kill()
            self.proc.wait()
        self.proc = None
        log.info("%s stopped", self.name)


class SherpaServer(Server):
    def __init__(self):
        super().__init__("sherpa-onnx")

    def start(self, cfg):
        if self.is_running():
            return
        tc = cfg["transcription"]
        model_dir = config.DATA_DIR / "models" / "stt" / tc["model"]
        bin_dir = BIN_DIR / "sherpa-onnx"
        binary = bin_dir / "sherpa-onnx-offline-websocket-server"

        num_threads = max(1, (os.cpu_count() or 4) * 3 // 4)
        args = [
            str(binary),
            f"--tokens={model_dir / 'tokens.txt'}",
            f"--encoder={model_dir / 'encoder.int8.onnx'}",
            f"--decoder={model_dir / 'decoder.int8.onnx'}",
            f"--joiner={model_dir / 'joiner.int8.onnx'}",
            f"--port={tc['port']}",
            f"--num-threads={num_threads}",
            "--provider=cuda",
        ]

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(bin_dir) + ":" + env.get("LD_LIBRARY_PATH", "")

        log.info("Starting %s on port %d", self.name, tc["port"])
        self.proc = subprocess.Popen(
            args, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Wait for "Listening on:" in stderr
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode(errors="replace")
                raise RuntimeError(f"sherpa-onnx exited early: {err[-500:]}")
            line = self.proc.stderr.readline().decode(errors="replace")
            if "Listening on:" in line:
                log.info("sherpa-onnx ready: %s", line.strip())
                # Drain stderr in background to prevent pipe blocking
                import threading
                threading.Thread(
                    target=self._drain, args=(self.proc.stderr,),
                    daemon=True,
                ).start()
                threading.Thread(
                    target=self._drain, args=(self.proc.stdout,),
                    daemon=True,
                ).start()
                return
        raise TimeoutError("sherpa-onnx did not become ready within 30s")

    def stop(self):
        self._kill()

    @staticmethod
    def _drain(stream):
        try:
            for _ in stream:
                pass
        except Exception:
            pass


class WhisperServer(Server):
    def __init__(self):
        super().__init__("whisper-server")

    def start(self, cfg):
        if self.is_running():
            return
        tc = cfg["transcription"]
        model_path = config.DATA_DIR / "models" / "whisper" / tc["whisper_model"]
        bin_dir = BIN_DIR / "whisper"
        binary = bin_dir / "whisper-server"

        num_threads = max(1, (os.cpu_count() or 4) * 3 // 4)
        args = [
            str(binary),
            "--model", str(model_path),
            "--host", tc["host"],
            "--port", str(tc["port"]),
            "--threads", str(num_threads),
            "--convert",
        ]

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(bin_dir) + ":" + env.get("LD_LIBRARY_PATH", "")

        log.info("Starting %s on port %d with model %s",
                 self.name, tc["port"], tc["whisper_model"])
        self.proc = subprocess.Popen(
            args, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Poll /health until 200
        url = f"http://{tc['host']}:{tc['port']}/health"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("whisper-server exited early")
            try:
                r = httpx.get(url, timeout=2)
                if r.status_code == 200:
                    log.info("whisper-server ready")
                    return
            except httpx.ConnectError:
                pass
            time.sleep(0.5)
        raise TimeoutError("whisper-server did not become ready within 60s")

    def stop(self):
        self._kill()


class LlamaServer(Server):
    def __init__(self):
        super().__init__("llama-server")

    def start(self, cfg):
        if self.is_running():
            return
        ec = cfg["enhancement"]
        model_path = config.DATA_DIR / "models" / "llm" / ec["model"]
        bin_dir = BIN_DIR / "llama"
        binary = bin_dir / "llama-server"

        args = [
            str(binary),
            "--model", str(model_path),
            "--host", ec["host"],
            "--port", str(ec["port"]),
            "--ctx-size", str(ec.get("ctx_size", 4096)),
            "--threads", str(max(1, (os.cpu_count() or 4) // 2)),
            "--n-gpu-layers", str(ec.get("gpu_layers", 99)),
        ]

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(bin_dir) + ":" + env.get("LD_LIBRARY_PATH", "")

        log.info("Starting %s on port %d with model %s", self.name, ec["port"], ec["model"])
        self.proc = subprocess.Popen(
            args, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Poll /health until 200
        url = f"http://{ec['host']}:{ec['port']}/health"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server exited early")
            try:
                r = httpx.get(url, timeout=2)
                if r.status_code == 200:
                    log.info("llama-server ready")
                    return
            except httpx.ConnectError:
                pass
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready within 60s")

    def stop(self):
        self._kill()


class ServerManager:
    def __init__(self):
        self.sherpa = SherpaServer()
        self.whisper = WhisperServer()
        self.llama = LlamaServer()

    def start(self, cfg):
        engine = cfg["transcription"].get("engine", "sherpa-onnx")
        if engine == "whisper":
            self.whisper.start(cfg)
        else:
            self.sherpa.start(cfg)
        if cfg["enhancement"]["enabled"]:
            self.llama.start(cfg)

    def stop(self):
        self.llama.stop()
        self.sherpa.stop()
        self.whisper.stop()

    def restart(self, cfg):
        self.stop()
        self.start(cfg)
