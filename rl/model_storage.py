"""Default Hugging Face downloads to the RL data directory."""
import os
from pathlib import Path
from collections.abc import MutableMapping


DEFAULT_HF_HOME = Path(__file__).resolve().parent / "data" / "models" / "huggingface"


def configure_hf_home(environ: MutableMapping[str, str] | None = None) -> str:
    """Set the project default before importing transformers or huggingface_hub.

    An explicitly configured Hugging Face cache keeps precedence.
    """
    env = os.environ if environ is None else environ
    if not env.get("HF_HOME") and not env.get("HF_HUB_CACHE"):
        env["HF_HOME"] = str(DEFAULT_HF_HOME)
    return env.get("HF_HUB_CACHE") or env.get("HF_HOME", "")
