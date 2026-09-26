from __future__ import annotations

import threading
import logging

import requests

from .config import Settings, settings
from .security import redact_secrets

_MODEL_SEMAPHORE = threading.BoundedSemaphore(value=2)
LOGGER = logging.getLogger(__name__)


class ModelUnavailable(RuntimeError):
    pass


class ModelClient:
    """Minimal OpenAI-compatible model abstraction for HF Router or another provider."""

    def __init__(self, config: Settings = settings):
        self.config = config

    def _endpoint_and_token(self) -> tuple[str, str]:
        if self.config.llm_provider == "huggingface":
            return "https://router.huggingface.co/v1/chat/completions", self.config.hf_token
        if self.config.llm_provider == "openai_compatible":
            return f"{self.config.openai_base_url}/chat/completions", self.config.openai_api_key
        raise ModelUnavailable("Unsupported model provider.")

    def complete(self, system: str, user: str) -> str:
        if not self.config.llm_ready:
            raise ModelUnavailable("Model inference is not configured; deterministic analysis remains available.")
        endpoint, token = self._endpoint_and_token()
        model = self.config.llm_model.removeprefix("LLM_MODEL=").strip()
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.15,
            "max_tokens": 650,
        }
        acquired = _MODEL_SEMAPHORE.acquire(timeout=3)
        if not acquired:
            raise ModelUnavailable("Model service is busy. Try again shortly.")
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=min(self.config.request_timeout + 12, 30),
            )
            if not response.ok:
                status = response.status_code
                try:
                    provider_message = str(response.json().get("error", {}).get("message", ""))[:240]
                except (ValueError, AttributeError):
                    provider_message = ""
                safe_message = redact_secrets(provider_message)
                LOGGER.warning("Model provider returned HTTP %s: %s", status, safe_message or "no safe detail")
                hint = {
                    400: "Check the configured model identifier and provider suffix.",
                    401: "Check the model token.", 402: "Check inference credits.",
                    403: "Check the token's Inference Providers permission.",
                    404: "The selected model route is unavailable.",
                    429: "The provider rate limit was reached.",
                }.get(status, "The provider is temporarily unavailable.")
                raise ModelUnavailable(f"Model provider returned HTTP {status}. {hint}")
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return redact_secrets(str(content).strip())
        except ModelUnavailable:
            raise
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            raise ModelUnavailable(f"Model request failed safely ({type(exc).__name__}).") from exc
        finally:
            _MODEL_SEMAPHORE.release()
