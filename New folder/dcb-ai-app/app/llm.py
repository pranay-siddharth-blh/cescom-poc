"""
The Gemini client — initialized ONCE at FastAPI startup (design point #3)
and reused for every request; called via the async `client.aio` interface
(design point #4) so one slow request never blocks the event loop.

Design point #1 (single call): `generate_query_plan()` is the only LLM
call in the whole request path. It asks for structured JSON output
matching `LLMQueryPlan` (SQL + answer_template together) via Gemini's
native structured-output mode (response_schema), so there's no second
call to turn the result into prose.
"""
from __future__ import annotations

from google import genai
from google.genai import types

from app.config import settings
from app.models import LLMQueryPlan

_client: genai.Client | None = None


def init_llm_client() -> genai.Client:
    global _client
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set in .env (project root). Copy .env.example "
            "to .env and fill in the real value before starting the app."
        )
    if not settings.gemini_model:
        # Matching the sibling BLH-BESCOM project's convention: no guessed
        # default when the model string is blank -- fail clearly at startup
        # instead of silently calling an unconfirmed model.
        raise RuntimeError(
            "GEMINI_MODEL is not set in .env (project root). Confirm the exact "
            "model string (e.g. from https://aistudio.google.com/apikey) and "
            "set it before starting the app."
        )
    _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def get_llm_client() -> genai.Client:
    if _client is None:
        raise RuntimeError("LLM client not initialized — did FastAPI's lifespan run?")
    return _client


async def generate_query_plan(prompt: str) -> LLMQueryPlan:
    """The one LLM call in the whole request path."""
    client = get_llm_client()
    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=LLMQueryPlan,
            temperature=0.0,
        ),
    )
    parsed = response.parsed
    if isinstance(parsed, LLMQueryPlan):
        return parsed
    # Fall back to manual validation if the SDK didn't auto-parse (e.g. a
    # future SDK version changes `.parsed`'s type) — still a single call,
    # just defensive parsing of its already-returned JSON text.
    return LLMQueryPlan.model_validate_json(response.text)
