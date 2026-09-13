"""Application settings, loaded from the project-root `.env` file."""
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.paths import DEFAULT_VAR_DIR, PROJECT_ROOT, resolve

ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Runtime data location ─────────────────────────────────────
    # One knob moves every piece of mutable state. The four paths below are
    # derived from it and only need setting to split them across volumes.
    var_dir: str = str(DEFAULT_VAR_DIR)
    sqlite_path: str = ""
    upload_dir: str = ""
    music_dir: str = ""
    feeds_dir: str = ""
    harness_dir: str = ""

    # ── Database ──────────────────────────────────────────────────
    db_type: str = "sqlite"
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_db: str = "myweb"
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_db: str = "myweb"

    # ── Auth ──────────────────────────────────────────────────────
    jwt_secret: str = "change-me"
    jwt_expire_hours: int = 72
    auth_cookie_name: str = "mw_auth"
    csrf_cookie_name: str = "mw_csrf"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str = ""
    cookie_path: str = "/"

    # ── AI providers ──────────────────────────────────────────────
    ai_provider: str = "deepseek"

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_api_url: str = "https://api.deepseek.com/v1/chat/completions"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_api_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    zhipu_api_key: str = ""
    zhipu_model: str = "glm-4.7-flash"
    zhipu_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_api_url: str = "https://api.openai.com/v1/chat/completions"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    custom_api_url: str = ""
    custom_api_key: str = ""
    custom_model: str = ""

    # ── Harness ───────────────────────────────────────────────────
    # The agent workbench. Defaults are the safe ones: admin-only, no shell.
    harness_enabled: bool = True
    harness_require_admin: bool = True
    harness_preset: str = "standard"
    harness_model: str = ""              # blank follows the provider's own model
    harness_max_steps: int = 24
    # Output cap per model call. 4096 truncates a `write` of any real file
    # mid-arguments, and a truncated tool call is unrecoverable — the JSON
    # never closes. Billing is per token actually generated, so a higher
    # ceiling costs nothing on short turns. Lower it for a provider that
    # rejects the value.
    harness_max_tokens: int = 8192
    harness_context_budget_tokens: int = 48000
    harness_shell_enabled: bool = False
    harness_shell_timeout_seconds: int = 30
    harness_shell_max_output_bytes: int = 32768
    harness_workspace_quota_mb: int = 64
    harness_search_url: str = ""         # blank uses the built-in DuckDuckGo endpoint

    # The Deep Research RL environment. Off by default, and that default is the
    # whole point: `standard` asks for `tools: ["*"]`, so without the gate in
    # `ToolRegistry._MODULE_GATES` the corpus tools would appear in the live
    # site's agent the moment the contract file exists. The corpus is a build
    # artifact of `rl/`, not web-app state, so its directory is resolved against
    # the repository root rather than VAR_DIR and is deliberately absent from
    # `runtime_dirs` — nothing creates it, and the tools degrade with a message
    # when it is missing.
    harness_corpus_enabled: bool = False
    harness_corpus_dir: str = "rl/data/corpus"

    # Subagents. Every knob here is a spend limit: a delegated run costs real
    # tokens and nobody is watching it turn by turn.
    harness_subagent_enabled: bool = True
    harness_subagent_max_depth: int = 1          # a subagent may not spawn one
    harness_subagent_max_per_session: int = 16
    harness_subagent_max_steps: int = 8
    harness_subagent_timeout_seconds: int = 300

    # ── Server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    allowed_origins: str = "http://localhost,http://localhost:8000"
    dev_reload: bool = False

    # ── Upload limits ─────────────────────────────────────────────
    max_image_size_mb: int = 10
    max_audio_size_mb: int = 50
    max_attachment_size_mb: int = 100

    # ── Derived values ────────────────────────────────────────────

    @model_validator(mode="after")
    def _resolve_runtime_paths(self):
        self.var_dir = resolve(self.var_dir)
        defaults = {
            "sqlite_path": "db/myweb.db",
            "upload_dir": "uploads",
            "music_dir": "music",
            "feeds_dir": "feeds",
            "harness_dir": "harness",
        }
        for field, relative in defaults.items():
            configured = getattr(self, field)
            setattr(self, field, resolve(configured or relative, base=Path(self.var_dir)))
        # Resolved against the repository root, not VAR_DIR: the corpus is an
        # input built by `rl/`, not runtime state the deployment owns.
        self.harness_corpus_dir = resolve(self.harness_corpus_dir or "rl/data/corpus")
        return self

    @property
    def database_url(self) -> str:
        t = self.db_type.lower()
        if t == "mysql":
            return (
                f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}"
                f"?charset=utf8mb4"
            )
        if t == "postgresql":
            return (
                f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
                f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
            )
        return f"sqlite:///{self.sqlite_path}"

    @property
    def avatar_dir(self) -> str:
        return str(Path(self.upload_dir) / "avatars")

    @property
    def harness_workspace_dir(self) -> str:
        """Root of the per-session agent workspaces."""
        return str(Path(self.harness_dir) / "workspaces")

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def runtime_dirs(self) -> list[str]:
        """Every directory that must exist before the app can serve traffic."""
        return [
            self.var_dir,
            str(Path(self.sqlite_path).parent),
            self.upload_dir,
            self.avatar_dir,
            self.music_dir,
            self.feeds_dir,
            self.harness_dir,
            self.harness_workspace_dir,
        ]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
