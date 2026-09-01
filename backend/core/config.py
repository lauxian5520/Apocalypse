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
        }
        for field, relative in defaults.items():
            configured = getattr(self, field)
            setattr(self, field, resolve(configured or relative, base=Path(self.var_dir)))
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
        ]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
