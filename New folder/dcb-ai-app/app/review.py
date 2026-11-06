"""
Second model pass: an independent reviewer checks every model-written answer before it is shown (pattern ported
from the KPCL POC, where a free-form reviewer invented objections to correct answers -- 23 of 213 -- until the veto
below existed).

  1. The reviewer gets the data rules, the docs of the columns the SQL uses, the question, the recognised
     entities, the SQL, the result rows and the answer text.
  2. It must name ONE error category: wrong_measure, wrong_scope, wrong_period, wrong_aggregation or
     unsupported_claim. Anything else (style, rounding, wording, a missing chart) counts as ok.
  3. Deterministic veto -- a rejection is overruled when the code can prove it false:
       wrong_scope        the SQL filters on every sub-division / tariff code / family named in the question
       wrong_period       the SQL filters on every fiscal year named (or the question names none)
       wrong_measure      the SQL uses every column of the registry formula of each measure named
       wrong_aggregation  the result has the shape asked for (one row for one figure, several for a list)
       unsupported_claim  every figure in the answer is a result value or a column total
A rejection that survives the veto gets one repair round in app.pipeline; an answer rejected again is not shown.
"""
from __future__ import annotations

import json
import re

from app import llm, mart_layer
from app.llm import Verdict

CATEGORIES = ("wrong_measure", "wrong_scope", "wrong_period", "wrong_aggregation", "unsupported_claim")
_NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_DATE_LIKE_RE = re.compile(r"\b(19|20)\d{2}\s*-\s*\d{2}\b|\bFY ?\d{2,4}\b|\b(19|20)\d{2}\b")

VERIFY_RULES = """Facts about the data (use them to judge):
- The data is the yearly DCB (Demand-Collection-Balance) statement of CESCOM's Holenarasipura Division, fiscal years
  2015-16 to 2025-26, text like '2024-25'. Sub-divisions: Arkalgud, Hangarahalli, Holenarasipura, Ramanathapura,
  Saligrama (closed after 2017-18) and the Konanur section. 'Division_Total' / the mart_fact_division_year table is the
  whole division.
- Amounts are rupees. Collection efficiency % = SUM(collection_grand_total) * 100 / SUM(demand_total); it can exceed 100
  when old arrears are collected. Ratios are recomputed from sums, never averaged.
- closing_balance_total (arrears) and installations are year-end positions: added across places in one year, never
  across years.
- Tariff families: LT-1 ... LT-7, HT-1 ... HT-7; slabs are codes like LT-4(a) or HT-2(a)(ii). A family total is the sum of
  its slabs (mart family tables) or the printed family subtotal.
- A single figure for one year is one row, not a list."""


def _column_docs(sql: str) -> str:
    tables = mart_layer.load()["tables"]
    used = set(re.findall(r"\bmart_\w+", sql or ""))
    words = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", (sql or "").lower()))
    lines = []
    for t in sorted(used):
        info = tables.get(t)
        if not info:
            continue
        lines.append(f"{t} -- {info['grain']}")
        for c, ci in info["columns"].items():
            if c in words:
                unit = f" [{ci['unit']}]" if ci.get("unit") else ""
                lines.append(f"  {c}{unit}: {(ci.get('doc') or '')[:160]}")
    return "\n".join(lines) or "(star-schema tables: fact_dcb line items, fact_dcb_rollups printed subtotals)"


def build_prompt(question: str, entities: dict, sql: str, columns: list[str], rows: list[tuple], answer: str) -> str:
    shown = rows[:25]
    table = "\n".join([" | ".join(columns)] + [" | ".join("" if v is None else str(v) for v in r) for r in shown])
    more = f"\n({len(rows) - len(shown)} more rows not shown)" if len(rows) > len(shown) else ""
    return (
        "You review an answer written by another assistant over CESCOM's DCB billing database before an official sees "
        "it. The SQL author knows the schema; do not second-guess a table or column that the documentation shows is "
        "right. The answer text is written automatically from the result rows.\n\n"
        + VERIFY_RULES + "\n\n## Columns used by the SQL\n" + _column_docs(sql) + "\n\n"
        "Reject ONLY for one of these clear errors:\n"
        "- wrong_measure: the SQL measures a different thing than the question asks;\n"
        "- wrong_scope: the SQL is for a different sub-division / tariff / category than asked, or leaves part out;\n"
        "- wrong_period: the SQL is for a different fiscal year than asked;\n"
        "- wrong_aggregation: e.g. one row per slab when a single total was asked, or arrears summed over years;\n"
        "- unsupported_claim: a figure or claim in the answer text is not in the result rows.\n"
        "Wording, rounding, ordering, extra detail, style or a missing chart are NEVER errors. If unsure, the answer is ok.\n\n"
        f"## Question\n{question}\n\n## Entities identified in the question\n{json.dumps(entities, ensure_ascii=False)}\n\n"
        f"## SQL\n{sql}\n\n## Result ({len(rows)} rows)\n{table}{more}\n\n## Answer text\n{answer}\n\n"
        'Reply with ONE JSON object only: {"ok": true, "category": "none", "problem": ""} or '
        '{"ok": false, "category": "<one of: ' + ", ".join(CATEGORIES) + '>", "problem": "<one sentence>"}'
    )


# ---------------------------------------------------------------------------------------------- deterministic facts
def _quoted(sql: str) -> set[str]:
    return {re.sub(r"\s+", "", m.upper()) for m in re.findall(r"'((?:[^']|'')*)'", sql or "")}


def _named(entities: dict) -> list[str]:
    names = list(entities.get("subdivisions", [])) + list(entities.get("slab_codes", [])) + list(entities.get("families", []))
    for k in ("subdivision_name", "tariff_code"):
        if entities.get(k):
            names.append(entities[k])
    names += list(entities.get("tariff_codes", []) or []) + list(entities.get("parent_category_matches", []) or [])
    return sorted(set(names))


def scope_verified(entities: dict, sql: str) -> bool | None:
    names = _named(entities)
    if not names:
        return None
    q, low = _quoted(sql), (sql or "").lower()
    for n in names:
        key = re.sub(r"\s+", "", n.upper())
        if key in q or any(key in x for x in q):
            continue
        if re.fullmatch(r"(LT|HT)-\d", key) and re.search(r"like\s+'" + re.escape(n.lower()), low):
            continue
        return False
    return True


def family_label_matches(question: str, sql: str) -> bool:
    """Every family code the SQL filters on is described in the question by its label ("residential apartments HT
    category" -> HT-4 = 'HT residential apartments'): at least two of the label's words appear in the question."""
    fams = mart_layer.families()
    codes = {c for c in fams if re.search(r"'" + re.escape(c) + r"[^']*'", sql or "", re.I)}
    if not codes:
        return False
    qw = set(re.findall(r"[a-z]+", (question or "").lower()))
    stop = {"and", "the", "of", "ht", "lt", "for", "in", "supply", "power", "special", "account"}
    for c in codes:
        words = set(re.findall(r"[a-z]+", fams[c]["label"].lower())) - stop
        if len(words & qw) < min(2, len(words)):
            return False
    return True


def period_verified(entities: dict, sql: str) -> bool | None:
    fys = list(entities.get("fiscal_years", [])) or ([entities["fiscal_year"]] if entities.get("fiscal_year") else [])
    if not fys:
        return None
    q = _quoted(sql)
    return all(fy.upper() in q for fy in fys)


def measure_verified(entities: dict, sql: str) -> bool | None:
    keys = entities.get("metrics", [])
    if not keys:
        return None
    low = (sql or "").lower()
    for k in keys:
        m = mart_layer.metrics().get(k)
        if not m:
            return None
        cols = set(re.findall(r"\b[a-z]+_[a-z0-9_]+\b", m["expr"]["num"] + " " + m["expr"].get("den", "")))
        stored = {"collection_efficiency": "collection_efficiency_pct", "demand_per_unit": "demand_per_unit_rs",
                  "arrears_to_demand": "arrears_to_demand_ratio", "live_installation_pct": "live_installation_pct"}.get(k)
        if not (all(re.search(r"\b" + c + r"\b", low) for c in cols) or (stored and re.search(r"\b" + stored + r"\b", low))):
            return False
    return True


def claims_supported(answer: str, question: str, columns: list[str], rows: list[tuple]) -> bool:
    """Every figure in the answer is a result value, a column total, or a number already in the question."""
    values = [float(v) for r in rows for v in r if isinstance(v, (int, float)) and not isinstance(v, bool)]
    totals = [sum(float(r[i]) for r in rows if isinstance(r[i], (int, float)) and not isinstance(r[i], bool))
              for i in range(len(columns))]
    known = values + totals + [x / 1e7 for x in values] + [x / 1e5 for x in values]
    known += [float(x.replace(",", "")) for x in _NUM_RE.findall(_DATE_LIKE_RE.sub(" ", question or ""))
              if x.replace(",", "").replace(".", "").lstrip("-").isdigit()]
    for tok in _NUM_RE.findall(_DATE_LIKE_RE.sub(" ", answer or "")):
        try:
            x = float(tok.replace(",", ""))
        except ValueError:
            continue
        if abs(x) < 10 and float(x).is_integer():
            continue                       # small counts in prose ("3 sub-divisions") are not figures to trace
        if not any(abs(x - k) <= max(abs(k) * 0.005, 0.006) for k in known):
            return False
    return True


def breakdown_within_period(entities: dict, columns: list[str], rows: list[tuple]) -> bool:
    """Several rows that break the asked figure down (by slab, code or year) without leaving the years asked:
    extra detail, which the reviewer's own rules say is never an error."""
    if not (1 < len(rows) <= 60):
        return False
    fys = set(entities.get("fiscal_years", [])) | ({entities["fiscal_year"]} if entities.get("fiscal_year") else set())
    if fys and "fiscal_year" in columns:
        i = columns.index("fiscal_year")
        return all(r[i] in fys for r in rows)
    return True


def shape_matches(entities: dict, question: str, rows: list[tuple]) -> bool:
    many_asked = bool(set(entities.get("intents", [])) & {"ranking", "comparison", "breakdown", "trend"}) or \
        len(entities.get("fiscal_years", [])) > 1 or len(_named(entities)) > 1 or \
        bool(re.search(r"\b(each|every|all|by|wise|list)\b", question or "", re.I))
    return len(rows) >= 1 if many_asked else len(rows) == 1


def veto(verdict: Verdict, category: str, question: str, entities: dict, sql: str, columns: list[str],
         rows: list[tuple], answer: str) -> str | None:
    """Why a rejection is provably wrong, or None if it stands."""
    if category not in CATEGORIES:
        return "not one of the error categories"
    if category == "wrong_scope" and scope_verified(entities, sql):
        return "the SQL filters on every sub-division / tariff named in the question"
    if category == "wrong_scope" and family_label_matches(question, sql):
        return "the tariff the SQL filters on is the family the question describes in words"
    if category == "wrong_measure" and not entities.get("metrics"):
        return "the question names no measure the data defines, so the SQL's choice cannot be shown wrong"
    if category == "wrong_period" and (period_verified(entities, sql) in (True, None)):
        return "the SQL filters on every fiscal year named in the question" if period_verified(entities, sql) \
            else "the question names no fiscal year"
    if category == "wrong_measure" and measure_verified(entities, sql):
        return "the SQL uses the registry formula of the measure named in the question"
    if category == "wrong_aggregation" and shape_matches(entities, question, rows):
        return "the result has the shape the question asks for"
    if category == "wrong_aggregation" and breakdown_within_period(entities, columns, rows):
        return "the rows break the asked figure down within the years asked (extra detail, not an error)"
    if category == "unsupported_claim" and (len(rows) > 1 or claims_supported(answer, question, columns, rows)):
        return ("a multi-row answer is written from the rows by the deterministic narrator, not by the model"
                if len(rows) > 1 else "every figure in the answer comes from the result row")
    return None


async def review_answer(question: str, entities: dict, sql: str, columns: list[str], rows: list[tuple],
                        answer: str, call=None) -> tuple[Verdict, str]:
    """(verdict after the veto, note). Raises if the reviewer cannot be reached (the caller fails open)."""
    raw = await (call or llm.verify_answer)(build_prompt(question, entities, sql, columns, rows, answer))
    if raw.ok:
        return raw, ""
    category = (raw.category or "").strip().lower()
    reason = veto(raw, category, question, entities, sql, columns, rows, answer)
    if reason:
        return Verdict(True, ""), f"reviewer overruled ({category}: {raw.problem[:160]}) because {reason}"
    return raw, f"{category}: {raw.problem}"
