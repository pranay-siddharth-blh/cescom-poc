"""
The LLM client — initialized ONCE at FastAPI startup (design point #3) and
reused for every request, an async httpx client so one slow request never
blocks the event loop.

Design point #1 (single call): `generate_query_plan()` is the only LLM
call in the whole request path. It sends the exact same prompt this app
has always sent (SYSTEM_INSTRUCTIONS, schema, semantic layer, worked
examples, entities, the question — all built by app.prompts, unchanged)
and expects the same JSON object back matching `LLMQueryPlan`.

Provider: an internal OpenAI-compatible gateway (vLLM-backed) at
settings.sql_verify_base_url, model settings.sql_verify_model -- supplied
by the team as the key to use going forward. Plain-prompting call: no
response_format, no guided_json, no structured-output request of any
kind -- the model is asked in plain text to return JSON, the same way the
prompt has always asked, and the response is parsed defensively
(stripping a markdown fence if present).

TLS note: the gateway's cert is self-signed and not in the system trust
store (confirmed via a direct curl without -k: SSL cert verify failure),
and no CA bundle file exists anywhere in this project. TLS verification
is OFF for this specific client (see init_llm_client below) — by
explicit, deliberate request ("bypass it, it's an internal product"),
not a default or an oversight. This means the client will trust whatever
answers at settings.sql_verify_base_url without checking its identity,
which is fine on a private/internal-only network reached over VPN but
would not be an acceptable default for anything public-facing. If a real
CA bundle is ever supplied, set AI_MODEL_CA_BUNDLE_PATH and verification
turns back on automatically (see init_llm_client) — this isn't a
one-way door.
"""
from __future__ import annotations

import json
import re

import httpx

from app.config import settings
from app.models import LLMQueryPlan

_client: httpx.AsyncClient | None = None


class LLMUnavailable(RuntimeError):
    """The gateway is not configured in .env. The app still runs every route that needs no model (verified
    queries, metric lookups, reports, definitions, capability notes); the rest get a clear message."""


class LLMTruncated(ValueError):
    """The reply hit the token limit before its JSON was complete (it usually loops listing columns)."""

# Some models wrap JSON output in a markdown fence even under plain
# prompting -- stripped before validation rather than trusted to never
# appear.
_MARKDOWN_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

# This gateway's model is a reasoning model: it emits a full chain-of-
# thought block ending in a literal `</think>` tag before its actual
# answer (confirmed via a direct raw call -- e.g. "Thinking Process:
# 1. **Analyze the Request:** ...  </think>\n\n{...actual JSON...}"),
# all inside the one `content` string, not split into a separate
# reasoning field. Only the text after the LAST `</think>` is the answer;
# stripped before JSON parsing. enable_thinking:false is also requested
# below to avoid this + its latency entirely when the gateway honors it,
# but this strip stays regardless, since a model can emit think-tags even
# when asked not to.
_THINK_TAG_RE = re.compile(r"^.*</think>", re.IGNORECASE | re.DOTALL)


def init_llm_client() -> httpx.AsyncClient | None:
    """None (offline mode, logged by the caller) when SQL_VERIFY_API_KEY / _BASE_URL / _MODEL are not all set."""
    global _client
    if not settings.llm_configured:
        return None
    _client = httpx.AsyncClient(
        base_url=settings.sql_verify_base_url,
        headers={
            "Authorization": f"Bearer {settings.sql_verify_api_key}",
            "Content-Type": "application/json",
        },
        # If a real CA bundle is ever set (AI_MODEL_CA_BUNDLE_PATH), verify
        # against it -- otherwise TLS verification is OFF, by explicit
        # request for this internal-only gateway. See this module's
        # docstring for exactly why and what that means.
        verify=settings.ai_model_ca_bundle_path or False,
        # This gateway's model appears to emit a "thinking"/reasoning
        # preamble before its actual answer (observed in a smoke-test
        # response even with max_tokens=5) -- 60s wasn't enough for this
        # app's full prompt (schema + semantic layer + examples, several
        # KB) and produced a plain ReadTimeout, not an error from the
        # gateway itself. Raised well above that as a starting point;
        # revisit based on real observed latency once more data comes in.
        timeout=settings.llm_timeout_seconds,
    )
    return _client


async def shutdown_llm_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_llm_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("LLM client not initialized — did FastAPI's lifespan run?")
    return _client


# Fields LLMQueryPlan types as plain `str` (default "") -- pydantic accepts
# a MISSING key fine (falls back to the default) but rejects an explicit
# JSON `null` against a `str` type. Observed live: for an unanswerable
# question (e.g. a tariff code that doesn't exist), this model writes
# "sql": null / "answer_template": null instead of "" or omitting them --
# a reasonable, natural thing for a model to write, just not what this
# shared Pydantic model (a provider with native structured output never hit
# this since native structured output enforces the declared type) accepts
# as-is. Normalized below before validation, not by loosening the model's
# own type -- this is a this-provider parsing quirk, not a real semantic
# change to what these fields mean.
_NULLABLE_STRING_FIELDS = ("sql", "answer_template", "clarification_message")


def _normalize_chart(data: dict) -> None:
    """A chart spec with a wrong key or type is repaired or dropped rather than failing the whole answer."""
    chart = data.get("chart_data")
    if not isinstance(chart, dict):
        data["chart_data"] = None
        return
    if "type" not in chart and "chart_type" in chart:
        chart["type"] = chart.pop("chart_type")
    if chart.get("type") not in ("bar", "line") or not chart.get("label_column") or not chart.get("value_column"):
        data["chart_data"] = None


def _parse_query_plan(content: str) -> LLMQueryPlan:
    text = _THINK_TAG_RE.sub("", (content or "").strip(), count=1).strip()
    text = _MARKDOWN_FENCE_RE.sub("", text).strip()
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        text = m.group(0) if m else text
    data = json.loads(text, strict=False)   # raw newlines inside the SQL string are allowed
    for field in _NULLABLE_STRING_FIELDS:
        if data.get(field) is None:
            data[field] = ""
    data["answerable"] = bool(data.get("answerable", False))
    _normalize_chart(data)
    return LLMQueryPlan.model_validate(data)


# This gateway gets no native structured-output enforcement (unlike
# a native response_schema) -- plain prompting only. Observed live,
# consistently: the model writes correct sql/answer_template but omits
# the `answerable` key entirely rather than writing `"answerable": true`
# explicitly (the one LLMQueryPlan field with no default -- see
# app/models.py -- so its absence is a hard validation error, not a
# silently-defaulted gap). This reminder is appended only for this
# provider's request, not part of the shared prompt in app/prompts.py.
_JSON_FIELD_REMINDER = (
    "\n\nRespond with a single JSON object containing ALL of these keys, "
    "even when a value is empty/false/null -- do not omit any key: "
    "answerable, sql, answer_template, clarification_message, chart_data."
)


RETRY_NOTE = ("\n\nIMPORTANT: your previous reply was cut off or was not valid JSON. Reply with ONE short JSON "
              "object. The SQL must select only the few columns the question needs (usually 2 to 6), each aliased "
              "once; never list every column of a table.")


async def generate_query_plan(prompt: str, retry: bool = False) -> LLMQueryPlan:
    """The one generation call in the request path -- the same shared prompt content every provider gets
    (app.prompts.build_prompt), plus one small provider-specific reminder appended here (see _JSON_FIELD_REMINDER)
    to compensate for this gateway having no native structured-output enforcement. retry=True is the single
    retry after a cut-off or malformed reply: a note and a slightly different temperature (the same sampling
    would reproduce the same broken reply). Repair rounds (app.pipeline) call this again with a repair prompt."""
    if _client is None:
        raise LLMUnavailable("LLM gateway not configured")
    response = await _client.post(
        "/chat/completions",
        json={
            "model": settings.sql_verify_model,
            "messages": [{"role": "user", "content": prompt + _JSON_FIELD_REMINDER + (RETRY_NOTE if retry else "")}],
            "temperature": 0.2 if retry else 0.0,
            "max_tokens": settings.llm_max_tokens,
            # Qwen3's own vLLM toggle to skip the reasoning/"thinking"
            # preamble entirely -- not an OpenAI-standard field, ignored
            # harmlessly by a gateway that doesn't recognize it. Worth
            # requesting regardless of _THINK_TAG_RE above: that strip
            # only fixes parsing, this one also avoids the extra
            # generation time reasoning costs, which is what pushed the
            # full-size prompt past the gateway's own ~60s proxy timeout
            # (a 504, not a client-side timeout) in testing.
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    response.raise_for_status()
    choice = response.json()["choices"][0]
    content = choice["message"]["content"]
    try:
        return _parse_query_plan(content)
    except (ValueError, KeyError):
        if choice.get("finish_reason") == "length":
            raise LLMTruncated("the reply hit the token limit before the JSON was complete") from None
        raise


# ---------------------------------------------------------------------------------------------- answer review
class Verdict:
    """The reviewer's judgement of one model-written answer (app/review.py)."""

    def __init__(self, ok: bool, problem: str = "", category: str = ""):
        self.ok, self.problem, self.category = ok, problem, category

    def __repr__(self) -> str:
        return f"Verdict(ok={self.ok}, category={self.category!r}, problem={self.problem!r})"


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_verdict(content: str) -> Verdict:
    text = _THINK_TAG_RE.sub("", (content or "").strip(), count=1).strip()
    text = _MARKDOWN_FENCE_RE.sub("", text).strip()
    if not text.startswith("{"):
        m = _JSON_OBJECT_RE.search(text)
        text = m.group(0) if m else text
    data = json.loads(text, strict=False)
    ok = data.get("ok")
    if isinstance(ok, str):
        ok = ok.strip().lower() in ("true", "yes", "ok", "correct")
    return Verdict(bool(ok), str(data.get("problem") or "").strip()[:500], str(data.get("category") or "").strip()[:40])


async def verify_answer(verify_prompt: str) -> Verdict:
    """Second, independent model pass: does this SQL + result + answer really answer the question?"""
    if _client is None:
        raise LLMUnavailable("LLM gateway not configured")
    response = await _client.post("/chat/completions", json={
        "model": settings.sql_verify_model,
        "messages": [{"role": "user", "content": verify_prompt}],
        "temperature": 0.0,
        "max_tokens": 300,
        "chat_template_kwargs": {"enable_thinking": False},
    }, timeout=settings.dcb_verify_timeout_seconds)
    response.raise_for_status()
    return parse_verdict(response.json()["choices"][0]["message"]["content"])
