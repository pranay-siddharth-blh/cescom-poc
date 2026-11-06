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
    `dict`, so the shape the model must return is fully specified and
    validated (app.llm._parse_query_plan / _normalize_chart repair or drop
    a malformed chart spec rather than failing the whole answer)."""

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
    # Generous bound: app.guardrails refuses anything over 500 characters with a clear message; this only stops
    # huge bodies (the Protection middleware also caps the request size).
    question: str = Field(max_length=4000)
    # the last question the page had answered (as the server took it), so "and Konanur?" can be read as a follow-up
    previous: str | None = Field(default=None, max_length=4000)   # followup.resolve ignores one over 600
    # "voice": the response also carries spoken_answer (what the voice says, app/spoken.py). "text" is unchanged.
    mode: str = Field(default="text", pattern="^(text|voice)$")


class LoginRequest(BaseModel):
    """Body for POST /api/login -- see app/auth.py's docstring for exactly
    how limited this login is (one shared username/password, no user
    table)."""

    username: str = Field(max_length=200)
    password: str = Field(default="", max_length=200)


class AskResponse(BaseModel):
    question: str
    answerable: bool
    sql: str = ""                       # only when DCB_SHOW_TECHNICAL_DETAILS=true
    columns: list[str] = []
    rows: list[list] = []
    truncated: bool = False
    answer: str
    clarification_message: str = ""
    entities: dict = {}                 # only when DCB_SHOW_TECHNICAL_DETAILS=true
    timing_ms: dict = {}                # only when DCB_SHOW_TECHNICAL_DETAILS=true
    # Real chart data built from the query result (app.answer.build_chart): type, labels, values, series_label,
    # unit, and optionally several `series` -- the same keys the chat page has always read, never model-authored.
    chart_data: dict | None = None
    table: dict | None = None           # formatted table + raw values for the page's table view and CSV
    route: str = ""                     # only when DCB_SHOW_TECHNICAL_DETAILS=true
    notes: list[str] = []
    verification: str = ""              # only when DCB_SHOW_TECHNICAL_DETAILS=true
    error: str = ""                     # only when DCB_SHOW_TECHNICAL_DETAILS=true
    report_id: str = ""                 # set when the answer can be downloaded as an Excel report
    asked_as: str = ""                  # the whole question a follow-up was taken as ("" when it was not one)
    context: bool = False               # a later follow-up may build on this answer
    spoken_answer: str = ""             # voice mode only: 1-2 sentences built from the answer (app/spoken.py), no model call
