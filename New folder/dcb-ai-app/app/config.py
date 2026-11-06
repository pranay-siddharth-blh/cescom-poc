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
# The mart (built by DCB_Mart/build_mart.py from the star schema, which it only reads) and its generated
# semantic layer: metric registry, capability manifest, known issues, entity lists, table / column docs.
MART_DIR = PROJECT_ROOT / "DCB_Mart"
DEFAULT_MART_DB_PATH = MART_DIR / "dcb_mart.sqlite"
MART_SEMANTIC_PATH = MART_DIR / "mart_semantic_layer.json"
MART_EXAMPLES_PATH = MART_DIR / "example_queries.sql"
INTEGRITY_MANIFEST_PATH = REPO_ROOT / "integrity_manifest.json"
# Voice mode: the reference clip and logs resolve against dcb-ai-app/ (the RDPR code calls this AI_DIR).
AI_DIR = REPO_ROOT


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

    # Internal OpenAI-compatible gateway (vLLM-backed), reached over the VPN -- the ONLY model provider this app
    # has (see app/llm.py). Gemini was removed entirely: no Gemini setting, key or SDK remains. Read only from
    # the project-root .env (or real environment variables). Named sql_verify_* to
    # match the .env this app's own team supplied (env var names inherited
    # as-is, not renamed) -- despite the name, this IS the primary/only
    # generation call in this app, not a second-opinion check alongside
    # another provider (dcb-ai-app has no such second call; see design
    # point #1, one LLM call per question).
    sql_verify_model: str = ""
    sql_verify_base_url: str = ""
    sql_verify_api_key: str = ""
    # Self-signed internal CA for the gateway's TLS cert. Blank -> verify
    # against the system trust store only. Confirmed REQUIRED right now: a
    # direct curl to this gateway without -k fails TLS verification (cert
    # problem), and no CA bundle file exists anywhere in this project --
    # deliberately never worked around by disabling verification.
    ai_model_ca_bundle_path: str = ""

    # LLM call limits. A cut-off reply (finish_reason=length) is retried once with a shorter-answer note.
    llm_timeout_seconds: float = 180.0
    llm_max_tokens: int = 2048

    dcb_db_path: str = ""
    dcb_mart_db_path: str = ""
    dcb_query_timeout_seconds: float = 8.0
    dcb_max_rows: int = 200

    # SQL, routes, timings, entities and error details in the UI and API, and FastAPI's /docs. Off for normal
    # users; set DCB_SHOW_TECHNICAL_DETAILS=true for a developer session.
    dcb_show_technical_details: bool = False
    # Abuse and overload guards (app/protection.py, app/main.py): questions / data calls per client IP per
    # rolling minute, questions answered at the same time (each may hold one gateway call), how long a question
    # waits for a free slot before a polite "busy" answer, and the largest request body accepted.
    dcb_rate_limit_per_minute: int = 60
    dcb_max_concurrent_questions: int = 8
    dcb_queue_wait_seconds: float = 20.0
    dcb_max_request_bytes: int = 16384
    # The tender analyser's PDF upload (POST /api/tender/analyse, app/tender.py) has its own cap.
    dcb_max_upload_bytes: int = 15_000_000
    # Second model pass (app/review.py): an independent reviewer checks every model-written answer before it
    # is shown; a clear error gets one repair round, an answer rejected twice is not shown. If the reviewer
    # cannot be reached, the answer (which already passed every deterministic guard) is shown and logged.
    dcb_verify_answers: bool = True
    dcb_verify_timeout_seconds: float = 30.0
    # The gateway model's context is 32k tokens; the prompt is kept to about this many (estimated) tokens.
    dcb_prompt_token_budget: int = 24000

    # Dashboard session-cookie signing key (see app/auth.py's docstring).
    # /api/login no longer checks a username/password at all -- any
    # non-blank username is accepted, by request -- but the session
    # cookie it hands out is still HMAC-signed with this secret, so a
    # visitor can't forge/tamper with one. Override in .env before this
    # app is ever reachable by anyone but you.
    session_secret: str = "dev-secret-change-me-in-env"

    # ---- Voice mode (the hands-free voice agent in AI mode; ported from RDPR, which tuned KPCL's port of the Meghalaya
    # voice agent). Same variable names as those apps. Speech to text: qwen3-asr; text to speech: Qwen3-TTS (vLLM-Omni,
    # OpenAI /audio/speech shape) cloning the officer reference clip. Both on the same internal gateway (VPN).
    asr_model: str = "qwen3-asr"
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_timeout_seconds: float = 60.0
    asr_context_hints_enabled: bool = True        # DCB terms as an ASR prompt; an echo of it is dropped
    tts_enabled: bool = False
    tts_model: str = "qwen3-tts"
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_timeout_seconds: float = 20.0
    tts_voice: str = "serena"
    tts_language: str = "English"
    tts_task_type: str = "CustomVoice"            # Base = clone the reference clip (TTS_REF_AUDIO_PATH)
    # The CESC persona, sent as the TTS style instruction (Qwen3-TTS honours it for the cloned Base voice too).
    # (The gateway accepts at most 500 characters here.)
    dcb_voice_instructions: str = (
        "Female voice: a composed, experienced senior officer of Chamundeshwari Electricity Supply Corporation, about "
        "forty, briefing officials. Medium-low pitch, warm and steady. Clear, neutral Indian English; calm, confident, "
        "respectful. Measured pace, slower and very clear for figures, rupees in lakh and crore, percentages, tariff "
        "codes and years, with a brief pause before each key figure. Say Karnataka place names the local way.")
    tts_max_new_tokens: int = 2048
    tts_ref_audio_path: str = ""                  # relative paths resolve against dcb-ai-app/
    tts_x_vector_only_mode: bool = True
    tts_stream_enabled: bool = True
    tts_stream_sample_rate: int = 24000
    tts_speed_enabled: bool = True
    voice_max_concurrent_calls: int = 4           # ASR/TTS calls in flight at once from this server
    voice_rate_limit_per_minute: int = 240        # transcribe / speak / filler calls per client IP per minute
    voice_max_audio_bytes: int = 10_000_000       # one spoken question as 16 kHz WAV is ~32 KB a second
    voice_max_seconds: float = 30.0               # longest recording accepted as one question
    voice_max_transcript_chars: int = 300         # longest transcript accepted as one question
    voice_collect_mishearings: bool = False       # text only, with the officers' consent: logs/voice_mishearings.jsonl

    @property
    def db_path(self) -> Path:
        if self.dcb_db_path:
            return Path(self.dcb_db_path)
        return DEFAULT_DB_PATH

    @property
    def mart_db_path(self) -> Path:
        return Path(self.dcb_mart_db_path) if self.dcb_mart_db_path else DEFAULT_MART_DB_PATH

    @property
    def llm_configured(self) -> bool:
        return bool(self.sql_verify_model and self.sql_verify_base_url and self.sql_verify_api_key)

    @property
    def asr_configured(self) -> bool:
        return bool(self.asr_base_url and self.asr_api_key and self.asr_model)

    @property
    def tts_configured(self) -> bool:
        return bool(self.tts_enabled and self.tts_base_url and self.tts_api_key and self.tts_model)


settings = Settings()
