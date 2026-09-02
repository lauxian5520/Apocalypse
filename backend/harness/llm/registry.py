"""Provider selection: settings in, `ModelAdapter` out."""
from core.config import get_settings
from core.providers import provider_config, require_configured
from harness.llm.base import ModelAdapter
from harness.llm.openai_compatible import OpenAICompatibleAdapter

settings = get_settings()


def build_adapter(model: str = "") -> ModelAdapter:
    """Adapter for the configured provider.

    `HARNESS_MODEL` overrides the provider's own default, so the workbench can
    run a stronger model than the sidebar chat without a second API key.
    """
    cfg = provider_config()
    require_configured(cfg)
    return OpenAICompatibleAdapter(cfg, model=model or settings.harness_model)
