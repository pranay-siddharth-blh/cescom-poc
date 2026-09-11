"""
App settings, loaded once from .env (or real env vars) at import time.

Design note: pydantic-settings gives us validation + a single source of
truth instead of scattering os.getenv() calls through the codebase.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# dcb-ai-app/app/config.py -> dcb-ai-app/ -> "New folder"/ (project root,
# where the 4 locked pipeline folders live, siblings of this app folder).
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
PROJECT_ROOT = REPO_ROOT.parent

DEFAULT_DB_PATH = PROJECT_ROOT / "DCB_Star_Schema" / "dcb_star_schema.sqlite"
SEMANTIC_LAYER_PATH = PROJECT_ROOT / "DCB_Semantic_Layer" / "semantic_layer.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # .env lives at the PROJECT root (sibling of DCB_Star_Schema/,
        # DCB_Semantic_Layer/, etc.), not inside dcb-ai-app/ — matching the
        # sibling BLH-BESCOM project's pattern of one .env at the project
        # root. See PROJECT_ROOT/.env.example for the documented keys.
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    dcb_db_path: str = ""
    dcb_query_timeout_seconds: float = 8.0
    dcb_max_rows: int = 200

    # --- Optional second LLM provider, for latency comparison against the
    # Gemini path above -- additive, never required. Naming matches the
    # sibling BLH-BESCOM project's .env convention (LLM_PROVIDER /
    # LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_TLS_VERIFY) plus
    # LLM_TIMEOUT, new here. When llm_provider is blank (the default),
    # nothing below is read or validated and app/llm.py behaves exactly as
    # it did before this was added -- see alt_provider_active.
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_tls_verify: bool = True
    llm_timeout: float = 120.0

    @field_validator("llm_tls_verify", mode="before")
    @classmethod
    def _blank_tls_verify_uses_default(cls, v):
        # A real bug caught while building this: .env intentionally leaves
        # LLM_TLS_VERIFY= blank (per the user's own instruction, since it's
        # meant to stay unset while LLM_PROVIDER itself is unset). Without
        # this, pydantic tried to parse "" as a bool and raised at
        # Settings() import time -- i.e. it would have broken the app
        # (including the already-working Gemini path) the moment this
        # blank line was added to .env. Tried returning PydanticUndefined
        # first, expecting pydantic to fall through to the field default;
        # it doesn't (confirmed: raises `bool_type` on PydanticUndefined
        # itself) -- so the default is spelled out explicitly here instead,
        # matching the field's own `= True` above.
        if isinstance(v, str) and v.strip() == "":
            return True
        return v

    @field_validator("llm_timeout", mode="before")
    @classmethod
    def _blank_timeout_uses_default(cls, v):
        # Same bug, same fix, for LLM_TIMEOUT= -- see
        # _blank_tls_verify_uses_default above. Keep in sync with the
        # field's own `= 120.0` default.
        if isinstance(v, str) and v.strip() == "":
            return 120.0
        return v

    @property
    def db_path(self) -> Path:
        if self.dcb_db_path:
            return Path(self.dcb_db_path)
        return DEFAULT_DB_PATH

    @property
    def alt_provider_active(self) -> bool:
        """True iff an alternative LLM provider should be used instead of
        Gemini for every request. Only LLM_PROVIDER being set flips this --
        the other LLM_* fields are validated (fail loud, not guessed) in
        app/llm.py at startup, matching the existing GEMINI_API_KEY/
        GEMINI_MODEL pattern."""
        return bool(self.llm_provider.strip())


settings = Settings()
