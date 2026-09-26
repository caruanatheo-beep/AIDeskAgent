from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_name: str = "AI Sales Desk Assistant"
    enable_llm: bool = _bool("ENABLE_LLM", False)
    allow_public_llm: bool = _bool("ALLOW_PUBLIC_LLM", False)
    llm_provider: str = os.getenv("LLM_PROVIDER", "huggingface").strip().lower()
    llm_model: str = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct:featherless-ai").strip()
    hf_token: str = os.getenv("HF_TOKEN", "").strip()
    fred_api_key: str = os.getenv("FRED_API_KEY", "").strip()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    request_timeout: int = _int("REQUEST_TIMEOUT_SECONDS", 8, 3, 20)
    cache_ttl: int = _int("CACHE_TTL_SECONDS", 900, 60, 3600)
    live_cache_ttl: int = _int("LIVE_CACHE_TTL_SECONDS", 60, 15, 300)
    max_llm_calls: int = _int("MAX_LLM_CALLS_PER_SESSION", 5, 1, 20)
    llm_cooldown: int = _int("LLM_COOLDOWN_SECONDS", 10, 1, 60)
    max_news_items: int = _int("MAX_NEWS_ITEMS", 10, 3, 20)
    max_agent_steps: int = _int("MAX_AGENT_STEPS", 4, 1, 8)
    max_tool_calls: int = _int("MAX_TOOL_CALLS_PER_SESSION", 18, 3, 50)
    max_calls_per_tool: int = _int("MAX_CALLS_PER_TOOL", 4, 1, 10)
    max_memory_turns: int = _int("MAX_MEMORY_TURNS", 6, 1, 12)
    max_context_chars: int = _int("MAX_CONTEXT_CHARS", 18000, 4000, 40000)
    web_search_enabled: bool = _bool("ENABLE_WEB_SEARCH", True)
    web_search_max_results: int = _int("WEB_SEARCH_MAX_RESULTS", 5, 1, 10)
    preferred_regions: tuple[str, ...] = _csv("PREFERRED_REGIONS", "United States,Euro Area,United Kingdom")
    server_port: int = _int("GRADIO_SERVER_PORT", 7860, 1024, 65535)

    @property
    def on_hugging_face(self) -> bool:
        return bool(os.getenv("SPACE_ID"))

    @property
    def server_name(self) -> str:
        return os.getenv("GRADIO_SERVER_NAME", "0.0.0.0" if self.on_hugging_face else "127.0.0.1")

    @property
    def llm_ready(self) -> bool:
        if not self.enable_llm:
            return False
        if self.llm_provider == "huggingface":
            return bool(self.hf_token)
        if self.llm_provider == "openai_compatible":
            return bool(self.openai_api_key)
        return False


settings = Settings()
