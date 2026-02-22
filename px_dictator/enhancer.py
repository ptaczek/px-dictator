"""Text enhancement via llama-server OpenAI-compatible API."""

import logging
import re
import time

import httpx

log = logging.getLogger(__name__)

# Qwen3 models wrap output in <think>...</think> before the actual answer
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_think(text):
    """Remove Qwen3 thinking tags, return only the final answer."""
    return _THINK_RE.sub("", text).strip()


def enhance(text: str, cfg: dict) -> str:
    """Send raw transcription to llama-server for grammar/punctuation fix."""
    ec = cfg["enhancement"]
    if not ec.get("enabled", False) or not text.strip():
        return text

    url = f"http://{ec['host']}:{ec['port']}/v1/chat/completions"
    body = {
        "messages": [
            {"role": "system", "content": ec["system_prompt"]},
            {"role": "user", "content": text},
        ],
        "temperature": 0.7,
        "max_tokens": 512,
        "stream": False,
    }

    log.debug("Enhancing text via %s", url)

    # Retry once on 503 (server reports healthy before inference is ready)
    for attempt in range(2):
        try:
            r = httpx.post(url, json=body, timeout=30)
            if r.status_code == 503 and attempt == 0:
                log.debug("Server returned 503, retrying after 1s...")
                time.sleep(1)
                continue
            r.raise_for_status()
            data = r.json()
            raw_content = data["choices"][0]["message"]["content"]
            enhanced = _strip_think(raw_content)

            # If stripping thinking left nothing, fall back to raw transcription
            if not enhanced:
                log.warning("Enhancement produced empty result, using raw text")
                return text

            log.info("Enhanced: %s → %s", text[:40], enhanced[:40])
            return enhanced
        except Exception as e:
            log.error("Enhancement failed, returning raw text: %s", e)
            return text
    return text
