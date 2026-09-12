"""
App settings, loaded once from .env (or real env vars) at import time.

Design note: pydantic-settings gives us validation + a single source of
truth instead of scattering os.getenv() calls through the codebase.
"""
from __future__ import annotations

from pathlib import Path

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

    # Dashboard session-cookie signing key (see app/auth.py's docstring).
    # /api/login no longer checks a username/password at all -- any
    # non-blank username is accepted, by request -- but the session
    # cookie it hands out is still HMAC-signed with this secret, so a
    # visitor can't forge/tamper with one. Override in .env before this
    # app is ever reachable by anyone but you.
    session_secret: str = "dev-secret-change-me-in-env"

    @property
    def db_path(self) -> Path:
        if self.dcb_db_path:
            return Path(self.dcb_db_path)
        return DEFAULT_DB_PATH


settings = Settings()
