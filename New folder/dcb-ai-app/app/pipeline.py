"""
The single request pipeline, shared by the SSE endpoint, the plain JSON endpoint and the eval / stress harnesses --
so all of them exercise exactly the same code path (only the delivery differs).

Deterministic routes run first and never call the model (ladder ported from the KPCL POC); only what is left reaches
the one generation call:

  L1  screen         empty / too long / pasted code / injection / harmful / write request / personal data -> refusal
  L2a greeting, identity                                                           -> fixed answer
  L2b verified       reviewed mart SQL (DCB_Mart/example_queries.sql) + writer      -> answer, table, chart
  L2t tariff         rates, "which category is a homestay", "what is LT-4(a)", late-payment interest, ToD ... ->
                     the CESC tariff booklet FY2025-26..27-28 (app/tariff_booklet.py)
  L2c glossary       "what does DCB mean"                                           -> the glossary entry
  L2c' knowledge     "what is CESC", "who regulates CESC", "what is Gruha Jyothi"   -> knowledge_base.yaml (EN / KN;
                     also the fallback when the model is offline or declines)
  (Kannada questions are first rewritten into English deterministically -- app/kannada.py)
  L2d capability     needs data this statement lacks (monthly, consumers, other divisions, rates, losses...) -> note
  L1' screen         questions about the system itself, off-topic                   -> refusal
  L3  router         sub-divisions, years (incl. this / last year), families, slabs, metrics, intents (no LLM)
      unknown names, out-of-range years                                           -> refused by name
  L4  reports        rankings, comparisons, trends, splits, summaries over the mart -> answer, table, chart
  L3b slabs          slab-level wording (segments, qualifiers, slab codes, descriptions) resolved to exact slabs
                     (app/slab_lookup.py) -> answer over the mart's slab facts, or a clarifying question
  L5  metric lookup  1-3 metrics x one scope x one year, SQL from the registry     -> answer
  L6  prompt + LLM   one call -> {answerable, sql, answer_template, clarification_message, chart_data}
                     (cut-off / malformed reply: one retry; false "outside the data" refusal: one correction)
  L7  SQL guard      read-only shape, allowed tables, Division_Total guard, grain guards, NULLS LAST
  L8  execute        read-only files + authorizer + timeout + row cap; ONE repair round fed the exact error
  L9  result guards  impossible figures, subject mismatch, empty result
  L10 answer writer  template fill (one row) or narration (many rows), table, chart -- no second LLM call
  L11 reviewer       independent second pass with a deterministic veto; one repair round; else withheld
Every stage is timed (timing_ms) and reported to `progress(stage)` so the chat page can show it live.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app import answer as writer
from app import (capability, followup, guardrails, kannada, knowledge, mart_layer, metric_lookup, premise, reports, review,
                 router, slab_lookup, small_talk, tariff_booklet, verified_queries)
from app.config import settings
from app.db import ReadOnlyDB, RestrictedQueryError
from app.guardrails import REFUSALS
from app.llm import LLMUnavailable, generate_query_plan, verify_answer
from app.prompts import build_prompt, coverage_repair_prompt, load_schema_ddl, repair_prompt, verify_repair_prompt
from app.semantic_layer import (
    RouterIndex,
    extract_entities,
    is_definitional_question,
    is_identity_question,
    load_semantic_layer,
    prune_semantic_layer,
)
from app.sql_safety import UnsafeSQLError, validate_readonly_sql

_logger = logging.getLogger(__name__)

# Fixed persona response for identity/meta questions (see app.semantic_layer.is_identity_question) -- hardcoded,
# not LLM-generated, so it is a zero-latency answer the same way the router itself is pure string logic.
IDENTITY_ANSWER = (
    "**I'm the CESCOM DCB assistant for Holenarasipura Division.** I answer questions about the division's "
    "Demand-Collection-Balance statement for fiscal years 2015-16 to 2025-26: demand billed, collection and "
    "collection efficiency, arrears (opening and closing balance), installations (total, live, DC/MNR), connected "
    "load and consumption, for the whole division, each sub-division (Arkalgud, Hangarahalli, Holenarasipura, "
    "Ramanathapura, the Konanur section, and Saligrama up to 2017-18) and each tariff category.\n\n"
    "Try: \"What was the collection efficiency in Arkalgud in 2024-25?\", \"Top 3 tariff categories by demand\" or "
    "\"Which sub-division has the most pending dues?\""
)
GREETING_ANSWER = ("Hello! Ask me about Holenarasipura Division's demand, collection, arrears or installations, for "
                   "example \"What was the collection efficiency in Arkalgud in 2024-25?\"")
_GREETING_RE = re.compile(r"^\s*(hi|hello|hey|hii+|namaskara|namaskar|namaste|good (morning|afternoon|evening)|"
                          r"thanks|thank you|thank u|ok|okay|great|nice)\W*$", re.IGNORECASE)
_HARD_REFUSALS = ("empty", "too_long", "code", "injection", "harmful", "read_only", "personal_data")


@dataclass
class PipelineResult:
    question: str
    answerable: bool = False
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    answer: str = ""
    clarification_message: str = ""
    entities: dict = field(default_factory=dict)
    timing_ms: dict = field(default_factory=dict)
    error: str = ""
    chart_data: dict | None = None
    table: dict | None = None
    route: str = ""
    notes: list[str] = field(default_factory=list)
    verification: str = ""        # model-written answers: ok / fixed / rejected / skipped / off
    review_notes: list[str] = field(default_factory=list)
    total_row: tuple | None = None
    asked_as: str = ""            # the whole question a follow-up was taken as (app.followup)
    context: bool = False         # a later follow-up may build on this answer


Progress = Callable[[str], None]


def _noop(_stage: str) -> None:
    return None


def _ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 1)


def _finish(result: PipelineResult, answer: str, route: str, answerable: bool = True) -> PipelineResult:
    result.answer, result.route, result.answerable = answer, route, answerable
    if not answerable and not result.clarification_message:
        result.clarification_message = answer
    return result


# ---------------------------------------------------------------------------------------------- template fill
_FORMAT_SPEC_RE = re.compile(r"(\{[a-zA-Z_][a-zA-Z0-9_]*)(:[^}]*)(\})")


def _strip_format_specs(template: str) -> str:
    """Drop any Python format spec (e.g. '{pct:.2f}' -> '{pct}') the LLM put in a placeholder: every DB value is
    already turned into a display string, so a numeric spec applied to a str raises ValueError -- a real crash
    caught during this project's first live-LLM eval (see CLAUDE.md 4.1)."""
    return _FORMAT_SPEC_RE.sub(r"\1\3", template)


_NO_DATA = ("No matching data was found for that question — this combination may not exist, or may not have been "
            "active, for that period.")


def _fill_template(template: str, columns: list[str], rows: list[tuple], entities: dict,
                   preferred: str | None = None) -> str:
    # An aggregate with no GROUP BY returns one all-NULL row when nothing matched: the same "no data" case as zero
    # rows, never a literal "N/A" spliced into the template (CLAUDE.md 4.3 / 5).
    if not rows or (len(rows) == 1 and all(v is None for v in rows[0])):
        return _NO_DATA
    template = _strip_format_specs(template or "")
    if len(rows) == 1 and template:
        row_dict = {col: writer.fmt_plain(val) for col, val in zip(columns, rows[0])}
        fill_values = {**{k: v for k, v in entities.items() if isinstance(v, str)}, **row_dict}
        try:
            return guardrails.scrub(template.format(**fill_values)) or _labelled(columns, rows[0])
        except (KeyError, IndexError, ValueError):
            pass
    if len(rows) == 1:
        return _labelled(columns, rows[0])
    # Many rows: a plain-language narration from the real rows (leader, spread, trend) + the table below it,
    # never a guessed sentence and never a silently dropped row.
    return writer.narrate(columns, rows, preferred)


def _labelled(columns: list[str], row: tuple) -> str:
    return "\n".join(f"•  {writer.header(c)}: {writer.fmt_value(c, v, exact=True)}" for c, v in zip(columns, row))


# ---------------------------------------------------------------------------------------------- the ladder
async def answer_question(question: str, db: ReadOnlyDB, router_index: RouterIndex, max_rows: int,
                          timeout_seconds: float, progress: Progress | None = None,
                          previous: str | None = None) -> PipelineResult:
    """`progress(stage)` is called as each real stage starts (the SSE endpoint streams them to the page).
    Rupee figures asked "in crores" / "in lakhs" get the conversion added after the answer is written,
    deterministically, whichever route produced it.

    Around the routes (RDPR POC pattern, 2026-10-05):
      L0  small talk     the whole message is a greeting / thanks / goodbye / "ok" / abuse -> friendly fixed reply;
                         abusive words in a real question are taken out first (app.small_talk)
      L0' follow-up      "and Konanur?", "what about 2023-24?", "and the demand?" after an answered question is
                         rewritten into the whole question it stands for (app.followup); the page shows it
      L12 premise        a figure the question states as fact is recomputed; a contradicted one is corrected first
      L13 polish         exact rupees kept once (in the headline), caveats labelled "Note:" (app.answer.polish)
    `previous` is the last question the page had answered (as the server took it)."""
    talk = small_talk.kind(question)
    cleaned = small_talk.strip_abuse(question) if not talk else question
    if talk or (question.strip() and not cleaned.strip()):
        talk = talk or "abuse"
        return PipelineResult(question="" if talk == "abuse" else question, answerable=True,
                              answer=small_talk.REPLIES[talk], route=f"small_talk:{talk}")
    asked_as = followup.resolve(cleaned, previous) if previous else None
    effective = asked_as or cleaned
    # A question typed in Kannada is rewritten deterministically into English for the routes (app/kannada.py); the
    # answer says how it was understood, and knowledge-base answers come back in Kannada. If nothing in it could be
    # understood, the original text goes through unchanged (as before).
    kn = kannada.is_kannada(effective)
    asked = effective
    if kn:
        rewritten = kannada.to_english(effective)
        if len(re.findall(r"[A-Za-z0-9]+", rewritten)) >= 2:
            asked = rewritten
    result = await _answer_question(asked, db, router_index, max_rows, timeout_seconds, progress or _noop, kn)
    result.question = effective
    result.asked_as = asked_as or ""
    if result.answerable and result.answer:
        result.answer = writer.convert_units(asked, result.answer)
        result.answer = writer.polish(asked, result.answer, result.notes)
        if result.route.startswith(premise.DATA_ROUTES):
            t0 = time.monotonic()
            try:
                correction = await premise.check(asked, db, timeout_seconds, result.answer)
            except Exception:  # noqa: BLE001 -- the check is extra; the answer stands without it
                _logger.exception("premise check failed for %r", asked[:200])
                correction = ""
            if correction:
                result.answer = correction + "\n\n" + result.answer
                result.notes = [correction] + result.notes
            result.timing_ms["premise_ms"] = _ms(t0)
    # a follow-up can build on this answer only when it came from the data about something named
    result.context = bool(result.answerable and result.route.startswith(premise.DATA_ROUTES) and followup.about_data(asked))
    if kn and asked != effective and not result.route.startswith(("knowledge", "refused:")):
        result.answer = kannada.understood_note(asked) + "\n\n" + result.answer
        result.entities = {**result.entities, "understood_as": asked}
    return result


async def _answer_question(question: str, db: ReadOnlyDB, router_index: RouterIndex, max_rows: int,
                           timeout_seconds: float, progress: Progress, kn: bool = False) -> PipelineResult:
    progress("understanding")
    result = PipelineResult(question=question)
    timing = result.timing_ms
    t0 = time.monotonic()

    screened = guardrails.screen_question(question)
    if screened in _HARD_REFUSALS:
        timing["router_ms"] = _ms(t0)
        return _finish(result, REFUSALS[screened], f"refused:{screened}", False)
    if _GREETING_RE.match(question or ""):
        timing["router_ms"] = _ms(t0)
        return _finish(result, GREETING_ANSWER, "greeting")
    if is_identity_question(question):
        # Short-circuit before any LLM/DB work: an identity question is not a schema-relevance gap.
        timing["router_ms"] = _ms(t0)
        return _finish(result, IDENTITY_ANSWER, "identity")

    vq = verified_queries.match(question)
    if vq is not None:
        progress("querying")
        try:
            qr = await db.run(vq.sql, max_rows=max_rows, timeout_seconds=timeout_seconds)
            result.sql, result.columns, result.rows, result.truncated = vq.sql, qr.columns, qr.rows, qr.truncated
            text, result.chart_data = verified_queries.render(vq, qr.columns, qr.rows)
            result.table = writer.result_table(qr.columns, qr.rows)
            result.entities = {"verified_query": vq.key}
            timing["db_ms"] = _ms(t0)
            return _finish(result, text, f"verified:{vq.key}")
        except Exception:  # noqa: BLE001 -- a verified query should never fail; fall through
            _logger.exception("verified query %s failed", vq.key)
            result = PipelineResult(question=question)
            timing = result.timing_ms

    # Tariff rates, categories and rules come from the CESC tariff booklet FY2025-26 to FY2027-28 (app/tariff_booklet.py):
    # before the glossary, whose tariff-code definitions (locked semantic layer) predate the 2024-25 restructuring.
    if screened != "system" and not guardrails.is_general_knowledge(question):
        try:
            ta = await tariff_booklet.answer(question, db, timeout_seconds)
        except Exception:  # noqa: BLE001 -- never fatal: fall through to the other routes
            _logger.exception("tariff booklet route failed for %r", question[:200])
            ta = None
        if ta is not None:
            timing["router_ms"] = _ms(t0)
            result.sql, result.columns, result.rows = ta.sql, ta.columns, ta.rows
            if ta.rows:
                result.table = (writer.result_table(ta.columns, ta.rows) if ta.sql else
                                {"columns": ta.columns, "keys": ta.columns, "units": [None] * len(ta.columns),
                                 "rows": [[str(c) for c in r] for r in ta.rows], "raw": [list(r) for r in ta.rows],
                                 "truncated": False})
            return _finish(result, ta.answer, f"tariff:{ta.key}")

    # Data this statement does not hold ("technical loss in Konanur", "arrears of each consumer"): the reviewed note,
    # before the knowledge routes, which would otherwise explain the place named in the question.
    note = capability.check(question)
    if note:
        timing["router_ms"] = _ms(t0)
        return _finish(result, note, "capability", False)

    definitional_key = is_definitional_question(question)
    if (definitional_key is not None or knowledge.is_definition(question)) and screened != "system":
        # The knowledge base explains a term better where it has an entry (e.g. DC/MNR, whose short glossary text in
        # the locked semantic layer is wrong: MNR is "meter not recording"); the glossary stays for everything else.
        kb = knowledge.define(question, kn)
        if kb:
            timing["router_ms"] = _ms(t0)
            return _finish(result, kb, "knowledge")
    if definitional_key is not None:
        # A bare "what is LT-1" is a glossary lookup, not a data query: read straight from the semantic layer.
        full_layer = load_semantic_layer()
        if definitional_key in full_layer["tariff_code_glossary"]:
            definition = full_layer["tariff_code_glossary"][definitional_key]
        else:
            definition = full_layer["glossary"][definitional_key]
        timing["router_ms"] = _ms(t0)
        return _finish(result, f"**{definitional_key}**: {definition}", "glossary")

    # General questions with no data intent ("What is CESC?", "Who regulates CESC?", "What is Gruha Jyothi?") come
    # from the knowledge base (app/knowledge.py); before the off-topic refusal, since "founded" / "history" trip it.
    if screened != "system":
        kb = knowledge.primary(question, kn)
        if kb:
            timing["router_ms"] = _ms(t0)
            return _finish(result, kb, "knowledge")

    if screened is not None:
        timing["router_ms"] = _ms(t0)
        return _finish(result, REFUSALS[screened], f"refused:{screened}", False)

    e = router.extract(question)
    result.entities = e
    unknown = _unknown_names(e)
    if unknown:
        timing["router_ms"] = _ms(t0)
        return _finish(result, unknown, "unknown_name", False)
    if e["out_of_range"] and not e["fiscal_years"]:
        fys = mart_layer.fiscal_years()
        timing["router_ms"] = _ms(t0)
        return _finish(result, f"**The data covers fiscal years {fys[0]} to {fys[-1]} only, so there are no figures for "
                               f"{' or '.join(e['out_of_range'])}.**", "out_of_range", False)
    timing["router_ms"] = _ms(t0)
    llm_entities = extract_entities(question, router_index)
    needs_model = _needs_model(question, llm_entities, e)
    if needs_model or e.get("slab_codes"):
        # Slab-level wording: resolved to exact slabs and answered from the mart (app/slab_lookup.py); ambiguous
        # wording gets a clarifying question. Only what it cannot resolve goes on to the model.
        t1 = time.monotonic()
        progress("querying")
        try:
            sa = await slab_lookup.answer(question, e, llm_entities, db, timeout_seconds)
        except Exception:  # noqa: BLE001 -- never fatal: fall through to the model
            _logger.exception("slab lookup failed for %r", question[:200])
            sa = None
        if sa is not None:
            timing["db_ms"] = _ms(t1)
            result.sql, result.columns, result.rows, result.notes = sa.sql, sa.columns, sa.rows, sa.notes
            if sa.answerable and len(sa.rows) > 1:
                result.table = writer.result_table(sa.columns, sa.rows)
            result.chart_data, result.total_row = sa.chart, sa.total
            result.entities = {**e, "slabs": [f"{s.code} | {s.desc}" for s in sa.slabs]}
            return _finish(result, sa.answer, f"slab:{sa.key}", sa.answerable)
    if needs_model:
        return await _llm_path(result, question, e, db, router_index, max_rows, timeout_seconds, progress, kn)

    t1 = time.monotonic()
    progress("querying")
    try:
        rep = await reports.match_and_run(question, e, db, timeout_seconds)
    except Exception:  # noqa: BLE001 -- a report should never fail; fall through to the next route
        _logger.exception("report failed for %r", question[:200])
        rep = None
    if rep is not None:
        timing["db_ms"] = _ms(t1)
        result.sql, result.columns, result.rows = rep.sql, rep.columns, rep.rows
        result.table = writer.result_table(rep.columns, rep.rows)
        result.chart_data, result.total_row = rep.chart, rep.total
        return _finish(result, rep.answer, f"report:{rep.key}")

    t1 = time.monotonic()
    try:
        ma = await metric_lookup.answer(e, question, db, timeout_seconds)
    except Exception:  # noqa: BLE001
        _logger.exception("metric lookup failed for %r", question[:200])
        ma = None
    if ma is not None:
        timing["db_ms"] = _ms(t1)
        result.sql, result.columns, result.rows, result.notes = ma.sql, ma.columns, ma.rows, ma.notes
        return _finish(result, ma.answer, f"metric:{ma.metric}", not ma.answer.startswith("**There are no "))

    return await _llm_path(result, question, e, db, router_index, max_rows, timeout_seconds, progress, kn)


# Words that name something narrower than a whole tariff family or sub-division: a slab ("BMA and Municipal Corp",
# "10 HP metered", "free lighting") or a consumer group that spans families ("industries", "EV charging"). The
# deterministic routes work at family / sub-division level, so these go to the model, whose prompt carries the
# slab-matching rules (tariff_category_matches / candidates) tuned in CLAUDE.md section 10.
_SLAB_WORDS_RE = re.compile(
    r"\b(industr\w*|factor(y|ies)|domestic|households?|homes?|farmers?|agricultur\w*|irrigation|pump\w*|ip ?sets?|"
    r"commercial|shops?|business\w*|hospitals?|schools?|temples?|street ?lights?|public lighting|water supply|"
    r"apartments?|ev|e\.v\.|charging|vehicles?|temporary|government|govt|lighting|heating|motive|institution\w*|"
    r"bma|municipal|ulbs?|urban local|panchayat\w*|village|hoardings?|advertis\w*|metered|un-?metered|hp\b|kv\b|"
    r"rural|urban|free lighting|office lighting|bhagya\w*|kutira\w*|slabs?|sub-?categor\w*|description)\b", re.I)


# Plain descriptors of a whole family, harmless when the family code itself is named ("domestic (LT-1) vs
# industrial (HT-2)"): with a code present they add nothing the deterministic routes would get wrong.
_FAMILY_DESCRIPTOR_RE = re.compile(r"\b(domestic|industr\w*|commercial|irrigation|residential|apartments?)\b", re.I)


def _needs_model(question: str, llm_entities: dict, e: dict | None = None) -> bool:
    if llm_entities.get("tariff_category_matches") or llm_entities.get("tariff_category_candidates"):
        return True
    q = question or ""
    if re.search(r"\bsubsid", q, re.I):
        q = re.sub(r"\b(the\s+)?(government|govt|gok|state)\b", " ", q, flags=re.I)
    if e and e.get("families"):
        q = _FAMILY_DESCRIPTOR_RE.sub(" ", q)
    return bool(_SLAB_WORDS_RE.search(q))


def _unknown_names(e: dict) -> str | None:
    if e.get("unknown_tariffs"):
        fams = [f for f in mart_layer.families() if f != "MISC"]
        return (f"**I don't recognise {' or '.join(sorted(set(e['unknown_tariffs'])))} as a tariff category in this "
                f"data.** The categories are {', '.join(fams)} and the miscellaneous revenue codes.")
    if e.get("unknown_subdivisions") and not e.get("subdivisions"):
        names = ", ".join(s for s in mart_layer.geographic_subdivisions())
        return (f"**{' or '.join(e['unknown_subdivisions'])} is not a sub-division of Holenarasipura Division in this "
                f"data.** Its sub-divisions and sections are: {names}.")
    return None


# ---------------------------------------------------------------------------------------------- the model path
async def _llm_path(result: PipelineResult, question: str, e: dict, db: ReadOnlyDB, router_index: RouterIndex,
                    max_rows: int, timeout_seconds: float, progress: Progress, kn: bool = False) -> PipelineResult:
    timing = result.timing_ms
    progress("writing_sql")
    t0 = time.monotonic()
    entities = extract_entities(question, router_index)
    prompt = build_prompt(question, load_schema_ddl(), prune_semantic_layer(question, router_index), entities)
    timing["prompt_ms"] = _ms(t0)
    timing["prompt_tokens_est"] = len(prompt) // 4
    review_entities = {**e, **entities}
    result.entities = review_entities

    t0 = time.monotonic()
    try:
        try:
            plan = await generate_query_plan(prompt)
        except (ValueError, KeyError) as exc:   # cut off or malformed JSON: one retry, told what went wrong
            _logger.warning("unusable plan (%s); retrying once", exc)
            plan = await generate_query_plan(prompt, retry=True)
    except LLMUnavailable:
        timing["llm_ms"] = _ms(t0)
        kb = knowledge.fallback(question, kn)
        if kb:
            return _finish(result, kb, "knowledge_fallback")
        return _finish(result, REFUSALS["llm_unavailable"], "llm_unavailable", False)
    except Exception as exc:  # noqa: BLE001 — logged and surfaced as a clean message, never a stack trace
        _logger.exception("LLM call failed for question: %r", question)
        timing["llm_ms"] = _ms(t0)
        result.error = f"LLM call failed: {exc}"
        return _finish(result, "Sorry, I couldn't process that question right now (LLM error).", "llm_error", False)
    timing["llm_ms"] = _ms(t0)

    if not plan.answerable and guardrails.false_coverage_claim(plan.clarification_message, e, mart_layer.fiscal_years()):
        _logger.warning("false coverage claim for %r: %s", question[:200], plan.clarification_message[:200])
        t0 = time.monotonic()
        try:
            plan = await generate_query_plan(coverage_repair_prompt(prompt, plan.clarification_message))
        except Exception as exc:  # noqa: BLE001
            result.error = f"coverage repair failed: {exc}"
        timing["coverage_repair_ms"] = _ms(t0)
    if not plan.answerable:
        kb = knowledge.fallback(question, kn)
        if kb:
            return _finish(result, kb, "knowledge_fallback")
        result.clarification_message = guardrails.safe_refusal(plan.clarification_message)
        return _finish(result, result.clarification_message or "I can't answer that from this dataset.", "llm_declined", False)

    first = await _execute_plan(result, question, review_entities, db, max_rows, timeout_seconds, progress, prompt, plan)
    if isinstance(first, PipelineResult):
        return first
    if not settings.dcb_verify_answers:
        result.verification = "off"
        return _finish(result, _with_notes(result, first), "llm")

    verdict = await _review(result, question, review_entities, first, progress)
    if verdict is None or verdict.ok:
        result.verification = "ok" if verdict else "skipped"
        return _finish(result, _with_notes(result, first), "llm")

    _logger.warning("reviewer rejected the answer to %r: %s", question[:200], verdict.problem)
    result.error = f"reviewer: {verdict.problem}"
    t0 = time.monotonic()
    progress("repairing")
    try:
        plan = await generate_query_plan(verify_repair_prompt(prompt, result.sql, verdict.problem))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("repair after review failed for %r: %s", question[:200], exc)
        return _unverified(result)
    timing["review_repair_llm_ms"] = _ms(t0)
    if not plan.answerable:
        return _unverified(result)
    second = await _execute_plan(result, question, review_entities, db, max_rows, timeout_seconds, progress, prompt, plan)
    if isinstance(second, PipelineResult):
        return _unverified(result)
    verdict2 = await _review(result, question, review_entities, second, progress, attempt=2)
    if verdict2 is not None and not verdict2.ok:
        _logger.warning("reviewer rejected the repaired answer to %r: %s", question[:200], verdict2.problem)
        result.error = f"reviewer (after repair): {verdict2.problem}"
        return _unverified(result)
    result.verification = "fixed" if verdict2 else "fixed:second review skipped"
    return _finish(result, _with_notes(result, second), "llm")


class _EmptyResult(Exception):
    pass


_EMPTY_HINT = (
    "The query ran but returned no rows (or only NULLs). Before concluding there is no data, check: (1) if the "
    "question names no fiscal year, do NOT filter on the latest year -- return every fiscal year (GROUP BY "
    "fiscal_year, ORDER BY fiscal_year), because a slab or sub-division may not exist in the latest year (Saligrama "
    "closed after 2017-18; many slabs were renamed in 2024-25); (2) copy description / tariff_code values exactly "
    "from the entities block (tariff_category_matches / candidates), and when several descriptions or codes are "
    "meant use IN (...), never two '=' filters on the same column joined by AND; (3) a tariff_category_id "
    "sub-query must use IN, not =, because one code has several descriptions. If the data truly has nothing, "
    "return the same SQL again.")


def _empty(rows: list[tuple]) -> bool:
    return not rows or all(all(v is None for v in r) for r in rows)


def _with_notes(result: PipelineResult, text: str) -> str:
    return text + ("\n\n" + "\n\n".join(result.notes) if result.notes and text != _NO_DATA else "")


async def _review(result: PipelineResult, question: str, entities: dict, text: str, progress: Progress,
                  attempt: int = 1):
    """The reviewer's verdict, or None if it could not be reached or replied unusably (fail open: the answer has
    already passed every deterministic guard; the event is logged)."""
    progress("verifying")
    t0 = time.monotonic()
    try:
        verdict, note = await review.review_answer(question, entities, result.sql, result.columns, result.rows, text,
                                                   call=verify_answer)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("reviewer unavailable for %r: %s", question[:200], exc)
        return None
    finally:
        result.timing_ms[f"review_ms_{attempt}"] = _ms(t0)
    if note:
        _logger.warning("review of %r: %s", question[:200], note)
        result.review_notes.append(note)
    return verdict


def _unverified(result: PipelineResult) -> PipelineResult:
    """Nothing the reviewer doubted is shown: no figure, table or chart."""
    result.verification = "rejected"
    result.columns, result.rows, result.table, result.chart_data, result.notes = [], [], None, None, []
    return _finish(result, REFUSALS["unverified"], "llm_unverified", False)


async def _execute_plan(result: PipelineResult, question: str, entities: dict, db: ReadOnlyDB, max_rows: int,
                        timeout_seconds: float, progress: Progress, prompt: str, plan) -> str | PipelineResult:
    """Guard, run and write up one plan, with ONE repair round fed the exact guard / SQL error. Returns the answer
    text, or a finished refusal."""
    timing = result.timing_ms
    sql, template, chart = plan.sql, plan.answer_template, plan.chart_data
    qr, last_error, blocked, empty_first = None, "", False, None
    for attempt in range(2):
        t0 = time.monotonic()
        progress("checking_sql")
        try:
            safe_sql = validate_readonly_sql(sql)
            timing[f"safety_ms_{attempt}"] = _ms(t0)
            result.sql = safe_sql
            t0 = time.monotonic()
            progress("running_query")
            qr = await db.run(safe_sql, max_rows=max_rows, timeout_seconds=timeout_seconds)
            timing[f"db_ms_{attempt}"] = _ms(t0)
            bad = guardrails.implausible_result(qr.columns, qr.rows)
            if bad:
                qr = None
                raise UnsafeSQLError(f"the result is impossible: {bad}; check the formula and the filters")
            if attempt == 0 and _empty(qr.rows):
                # An empty result is often a filter mistake, not a real "no data": a slab that closed before the
                # latest year, a description spelled differently from the table, two contradictory equalities.
                # One repair round, told exactly that; an empty answer after it is reported honestly.
                empty_first = qr
                qr = None
                raise _EmptyResult()
            break
        except _EmptyResult:
            last_error, blocked = _EMPTY_HINT, False
            if not result.sql:
                result.sql = sql
        except UnsafeSQLError as exc:
            last_error, blocked = f"Rejected by the SQL guard: {exc}", False
            if not result.sql:
                result.sql = sql
        except RestrictedQueryError as exc:
            last_error, blocked = f"Access denied: {exc}. Read only the tables listed in the schema.", True
        except Exception as exc:  # noqa: BLE001 -- SQL error or timeout
            last_error, blocked = f"SQL execution failed: {exc}", False
        timing[f"db_ms_{attempt}"] = _ms(t0)
        if attempt == 0:
            t0 = time.monotonic()
            progress("repairing")
            try:
                plan = await generate_query_plan(repair_prompt(prompt, sql, last_error))
            except Exception as exc:  # noqa: BLE001
                timing["repair_llm_ms"] = _ms(t0)
                last_error = f"{last_error} (repair call failed: {exc})"
                break
            timing["repair_llm_ms"] = _ms(t0)
            if not plan.answerable:
                result.clarification_message = guardrails.safe_refusal(plan.clarification_message)
                return _finish(result, result.clarification_message, "llm_declined", False)
            sql, template, chart = plan.sql, plan.answer_template, plan.chart_data

    if qr is None and empty_first is not None and last_error.startswith(_EMPTY_HINT):
        qr = empty_first    # the repair could not run: the first (empty) result stands, reported as "no data"
    if qr is None:
        _logger.warning("query failed for %r: %s", question[:200], last_error)
        result.error = last_error
        if last_error.startswith("Rejected by the SQL guard"):
            text = "Sorry, the generated query didn't pass the read-only safety check."
        else:
            text = "Sorry, that query couldn't be executed against the database."
        return _finish(result, text, "llm_sql_failed" + (":blocked" if blocked else ""), False)

    if guardrails.subject_mismatch(question, result.sql):
        _logger.warning("subject mismatch for %r; SQL %s", question[:200], result.sql)
        return _finish(result, REFUSALS["mismatch"], "llm_mismatch", False)

    t0 = time.monotonic()
    progress("writing_answer")
    result.columns, result.rows, result.truncated = qr.columns, qr.rows, qr.truncated
    result.table = writer.result_table(qr.columns, qr.rows) if len(qr.rows) > 1 else None
    result.chart_data = None
    if chart is not None:
        # Built from the real result: the model only named its own column aliases (models.ChartSpec).
        result.chart_data = writer.build_chart(chart.type, chart.label_column, chart.value_column, chart.series_label,
                                               qr.columns, qr.rows)
    text = _fill_template(template, qr.columns, qr.rows, entities, preferred=chart.value_column if chart else None)
    fams = list(entities.get("families", [])) + [c.split("(")[0] for c in entities.get("slab_codes", [])]
    result.notes = mart_layer.caveats(fams, entities.get("fiscal_years", []), entities.get("subdivisions", []))
    if qr.truncated:
        result.notes.append(f"Only the first {max_rows} rows are shown.")
    timing["fill_ms"] = _ms(t0)
    return text   # the caveats in result.notes are added after review (_with_notes): the reviewer judges the answer
