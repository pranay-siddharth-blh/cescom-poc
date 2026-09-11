"""Pydantic models for the single structured LLM call (design point #1)
and for the FastAPI request/response bodies."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LLMQueryPlan(BaseModel):
    """The ONE JSON object the LLM returns per question — SQL and the
    answer template together, in a single call. `answerable=false` is how
    the model tells us a question can't be safely answered (no matching
    tariff code, needs a write, out of scope, etc.) instead of guessing."""

    answerable: bool = Field(
        description="False if the question cannot be safely/correctly answered "
        "from this schema (ambiguous, needs a write, references data that doesn't "
        "exist, etc.) — in that case leave sql/answer_template empty and explain "
        "in clarification_message instead of guessing."
    )
    sql: str = Field(
        default="",
        description="A single read-only SQLite SELECT (or WITH...SELECT) statement. "
        "Every SELECT column MUST have an explicit alias (AS ...) matching a "
        "placeholder used in answer_template, so the template can be filled without "
        "a second LLM call.",
    )
    answer_template: str = Field(
        default="",
        description="Natural-language answer with {placeholder} tokens. Each "
        "placeholder must be either a SELECT column alias from `sql`, or one of "
        "the known entities you were given (fiscal_year, subdivision_name, "
        "tariff_code) if the question names them directly.",
    )
    clarification_message: str = Field(
        default="",
        description="Set only when answerable=false: a short explanation of why, "
        "phrased to the end user (e.g. 'I don't see a tariff code matching X').",
    )


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    question: str
    answerable: bool
    sql: str = ""
    columns: list[str] = []
    rows: list[list] = []
    truncated: bool = False
    answer: str
    clarification_message: str = ""
    entities: dict = {}
    timing_ms: dict = {}
