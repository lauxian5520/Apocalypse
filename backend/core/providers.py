"""Resolution of the configured LLM provider into a concrete endpoint.

Pure configuration derivation: this module decides *where* to send a request
and *whether* the credentials for it exist. It never opens a connection, so
both the plain chat service and the harness model adapter can share one answer
instead of each keeping a private copy of the provider table.
"""
from core.config import get_settings
from core.errors import ConfigurationError

settings = get_settings()

# Wire formats a provider can speak. Everything except Ollama is the OpenAI
# `/chat/completions` shape.
FORMAT_OPENAI = "openai"
FORMAT_OLLAMA = "ollama"


def provider_config(name: str = "") -> dict:
    """Return `{url, key, model, format}` for `name` (default: the configured one)."""
    p = (name or settings.ai_provider).lower()
    if p == "deepseek":
        return {
            "url": settings.deepseek_api_url,
            "key": settings.deepseek_api_key,
            "model": settings.deepseek_model,
            "format": FORMAT_OPENAI,
        }
    elif p == "gemini":
        return {
            "url": settings.gemini_api_url,
            "key": settings.gemini_api_key,
            "model": settings.gemini_model,
            "format": FORMAT_OPENAI,
        }
    elif p == "zhipu":
        return {
            "url": settings.zhipu_api_url,
            "key": settings.zhipu_api_key,
            "model": settings.zhipu_model,
            "format": FORMAT_OPENAI,
        }
    elif p == "openai":
        return {
            "url": settings.openai_api_url,
            "key": settings.openai_api_key,
            "model": settings.openai_model,
            "format": FORMAT_OPENAI,
        }
    elif p == "ollama":
        return {
            "url": f"{settings.ollama_base_url}/api/chat",
            "key": "",
            "model": settings.ollama_model,
            "format": FORMAT_OLLAMA,
        }
    else:  # custom
        return {
            "url": settings.custom_api_url,
            "key": settings.custom_api_key,
            "model": settings.custom_model,
            "format": FORMAT_OPENAI,
        }


def require_configured(cfg: dict) -> None:
    """Fail fast with an actionable message when the provider is not set up."""
    if not cfg["url"]:
        raise ConfigurationError(f"AI 未配置：provider={settings.ai_provider} 缺少 API URL")
    if cfg["format"] != FORMAT_OLLAMA and not cfg["key"]:
        raise ConfigurationError(
            f"AI 未配置：provider={settings.ai_provider} 缺少 API Key，请在 .env 中填写后重启服务"
        )


def auth_headers(cfg: dict) -> dict:
    """Request headers carrying the provider's credentials."""
    headers = {"Content-Type": "application/json"}
    if cfg["key"]:
        headers["Authorization"] = f"Bearer {cfg['key']}"
    return headers
