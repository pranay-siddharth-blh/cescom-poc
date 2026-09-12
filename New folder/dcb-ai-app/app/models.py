"""Pydantic models for the single structured LLM call (design point #1)
and for the FastAPI request/response bodies."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChartSpec(BaseModel):
    """What the LLM actually returns for a chart: a REFERENCE to its own
    SELECT column aliases, not literal data values. Mirrors answer_template's
    {placeholder} mechanism (design point #1) — the LLM produces this
    alongside `sql` in the same single call, before the query has run, so it
    cannot know the real result values any more than answer_template can.
    An earlier version of this field asked the LLM for literal labels/values
    arrays directly; a live-eval run (all 10 example + 2 adversarial
    questions) showed that on every multi-row question it either fabricated
    plausible-looking numbers that didn't match the real query result at
    all, or gave up with empty arrays — never correct. Referencing column
    aliases instead lets app.pipeline build the real ChartData deterministically
    from the actual query result, the same way _fill_template builds the
    real answer text from real rows, never from LLM-authored numbers.

    Deliberately a concrete nested model with explicit properties, NOT
    `dict` — Gemini's structured-output mode (response_schema) converts a
    bare `dict` field into a JSON Schema with `additionalProperties: true`,
    which the google-genai SDK's own `_raise_for_unsupported_mldev_properties`
    rejects outright for the plain Gemini Developer API client this app uses
    (non-Vertex): "additionalProperties is only supported in Gemini
    Enterprise Agent Platform mode, not in Gemini Developer API mode." A
    `dict` field would have broken every single LLM call, not just
    chart-shaped ones — confirmed by calling the SDK's real schema-conversion
    path (`google.genai._transformers.t_schema`) directly before choosing
    this shape."""

    type: Literal["bar", "line"]
    label_column: str = Field(
        description="A column alias from your own SELECT (the `AS ...` name) "
        "to use as the chart's labels / x-axis categories.",
    )
    value_column: str = Field(
        description="A column alias from your own SELECT holding the "
        "numeric value to plot — must be a plain numeric column, not text.",
    )
    series_label: str = ""


class ChartData(BaseModel):
    """The final, real chart data returned to the frontend — built by
    app.pipeline from the actual query result (see ChartSpec above), never
    authored by the LLM."""

    type: Literal["bar", "line"]
    labels: list[str]
    values: list[float]
    series_label: str = ""


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
    chart_data: ChartSpec | None = Field(
        default=None,
        description="Set ONLY for genuinely comparison- or trend-shaped questions "
        "(year-over-year series, X vs Y, multi-row breakdowns) where a bar/line "
        "chart would help — leave null for single-value answers. Reference your "
        "own SELECT's column aliases (label_column/value_column); do NOT invent "
        "literal data values here.",
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
    chart_data: ChartData | None = None
