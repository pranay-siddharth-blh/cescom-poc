"""
The Gemini client — initialized ONCE at FastAPI startup (design point #3)
and reused for every request; called via the async `client.aio` interface
(design point #4) so one slow request never blocks the event loop.

Design point #1 (single call): `generate_query_plan()` is the only LLM
call in the whole request path. It asks for structured JSON output
matching `LLMQueryPlan` (SQL + answer_template together) via Gemini's
native structured-output mode (response_schema), so there's no second
call to turn the result into prose.

--- Optional second provider (added for latency comparison, not a
replacement) ---------------------------------------------------------
When LLM_PROVIDER is blank (the default), everything below the Gemini
section is inert: `alt_provider_active` is False, `_alt_client` stays
None, and `generate_query_plan()` calls the exact same Gemini code path
as before this was added -- zero behavior change for the existing
default configuration.

When LLM_PROVIDER is set (currently only "openai" is implemented --
i.e. any OpenAI-compatible /v1 chat-completions endpoint, matching the
sibling BLH-BESCOM project's LLM_PROVIDER/LLM_BASE_URL/LLM_API_KEY/
LLM_MODEL/LLM_TLS_VERIFY convention), every request routes through that
provider *instead of* Gemini -- this is a single global switch for
comparing providers end-to-end, not a per-request choice. Gemini's own
client is still initialized either way (so switching back requires no
restart-time surprises), but is simply not called while the alt path is
active.
"""
from __future__ import annotations

import json

import httpx
from google import genai
from google.genai import types
from openai import AsyncOpenAI

from app.config import settings
from app.models import LLMQueryPlan

_client: genai.Client | None = None
_alt_client: AsyncOpenAI | None = None


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

    if settings.alt_provider_active:
        _init_alt_llm_client()

    return _client


def get_llm_client() -> genai.Client:
    if _client is None:
        raise RuntimeError("LLM client not initialized — did FastAPI's lifespan run?")
    return _client


async def generate_query_plan(prompt: str) -> LLMQueryPlan:
    """The one LLM call in the whole request path. Dispatches to whichever
    provider is active -- see the module docstring's "Optional second
    provider" section. Gemini remains the path taken whenever
    settings.alt_provider_active is False (the default)."""
    if settings.alt_provider_active:
        return await _generate_query_plan_alt(prompt)
    return await _generate_query_plan_gemini(prompt)


async def _generate_query_plan_gemini(prompt: str) -> LLMQueryPlan:
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


# --- Alternative provider (OpenAI-compatible) -----------------------------

_SUPPORTED_ALT_PROVIDERS = {"openai"}


def _validate_alt_provider_config() -> None:
    """Fail loud, same rule as GEMINI_API_KEY/GEMINI_MODEL above: if
    LLM_PROVIDER is set, every other LLM_* field it needs must be set too --
    no guessed default, no silent fallback. Raises before any client is
    constructed and before any network activity, so this can be (and is,
    in tests/alt_provider_config_test.py) exercised with zero risk of an
    accidental live call."""
    provider = settings.llm_provider.strip().lower()
    if provider not in _SUPPORTED_ALT_PROVIDERS:
        raise RuntimeError(
            f"LLM_PROVIDER={settings.llm_provider!r} is not a supported alternative "
            f"provider -- only {sorted(_SUPPORTED_ALT_PROVIDERS)} is implemented "
            "(an OpenAI-compatible /v1 chat-completions endpoint)."
        )
    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", settings.llm_base_url),
            ("LLM_API_KEY", settings.llm_api_key),
            ("LLM_MODEL", settings.llm_model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "LLM_PROVIDER is set to " + repr(settings.llm_provider) + " but "
            + ", ".join(missing) + " is not set in .env (project root). Fill in "
            "all of LLM_BASE_URL / LLM_API_KEY / LLM_MODEL before the alternative "
            "provider path can be used — no default is guessed for any of them."
        )


def _init_alt_llm_client() -> AsyncOpenAI:
    global _alt_client
    _validate_alt_provider_config()
    # LLM_TLS_VERIFY mirrors the sibling BLH-BESCOM project's opt-out for a
    # self-signed-cert internal endpoint — default True (verify), and
    # deliberately not the default for a real/production endpoint if ever
    # set to False. A custom httpx.AsyncClient is the supported way to
    # control this on the openai SDK's AsyncOpenAI client.
    http_client = httpx.AsyncClient(
        verify=settings.llm_tls_verify, timeout=settings.llm_timeout
    )
    _alt_client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        http_client=http_client,
        timeout=settings.llm_timeout,
    )
    return _alt_client


def get_alt_llm_client() -> AsyncOpenAI:
    if _alt_client is None:
        raise RuntimeError(
            "Alternative LLM client not initialized — did FastAPI's lifespan run "
            "with LLM_PROVIDER set?"
        )
    return _alt_client


async def _generate_query_plan_alt(prompt: str) -> LLMQueryPlan:
    """Single-call structured output via an OpenAI-compatible chat-
    completions endpoint (design point #1, same as the Gemini path): one
    request returns SQL + answer_template together as JSON, parsed
    directly into LLMQueryPlan — no second call phrases the answer.
    response_format={"type":"json_object"} is used rather than a strict
    json_schema mode, since not every OpenAI-compatible backend supports
    schema-constrained decoding; the schema is instead given to the model
    as part of the system message."""
    client = get_alt_llm_client()
    schema_hint = json.dumps(LLMQueryPlan.model_json_schema())
    response = await client.chat.completions.create(
        model=settings.llm_model,
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You must respond with a single JSON object and nothing else, "
                    "matching exactly this JSON Schema:\n" + schema_hint
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content
    return LLMQueryPlan.model_validate_json(content)
