"""
Deterministic figure answers from the metric registry (no LLM; pattern ported from the KPCL POC).

Handles a question that names one to three registry metrics, at most ONE scope (the division, one sub-division,
one tariff family, LT or HT, or a family in one sub-division) and at most ONE fiscal year (none = the latest year,
said so in the answer), with no ranking / comparison / trend / breakdown wording. Examples:
"What was the collection efficiency in Arkalgud in 2024-25?", "HT-2 demand in 2015-16", "how many connections
do we have", "total dues in konanur". Anything else returns None and moves on.

The SQL is assembled from reviewed registry expressions and fixed filters; the only values in it are bound
parameters taken from validated entities. Every answer also gives the previous year for context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app import mart_layer, scope as scopes
from app.answer import fmt_value, pretty_label
from app.db import ReadOnlyDB

# Wording this lookup cannot honour exactly; such questions go on to the reports or the model.
_DECLINE_RE = re.compile(r"\b(average|avg|mean|per month|monthly|median|why|reason|because|due to|forecast|predict|"
                         r"expected|estimate|slab|description|excluding|except|without|only the|share|percentage of)\b", re.I)
_MANY_INTENTS = {"ranking", "comparison", "trend", "breakdown", "share"}


@dataclass
class MetricAnswer:
    answer: str
    sql: str
    params: list = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    metric: str = ""
    notes: list[str] = field(default_factory=list)


def default_period_note(fy: str) -> str:
    return f"No year was named, so the latest year in the data, {fy}, is used."


def _unit_col(key: str) -> str:
    """A column name whose unit matches the metric, for formatting."""
    return {"Rs": "x_amount_rs", "%": "x_pct", "count": "x_count", "units": "consumption_total", "kW": "connected_load_kw",
            "ratio": "x_ratio", "Rs/unit": "demand_per_unit_rs",
            "paise/unit": "energy_charge_per_unit_paise"}.get(mart_layer.metric(key)["unit"], key)


def fmt_metric(key: str, v, exact: bool = True) -> str:
    return fmt_value(_unit_col(key), v, exact=exact)


def _describe(key: str) -> str:
    return mart_layer.metric(key)["label"]


async def answer(e: dict, question: str, db: ReadOnlyDB, timeout: float) -> MetricAnswer | None:
    metrics = e.get("metrics", [])
    if not metrics or len(metrics) > 3:
        return None
    # the decline words are looked for outside the metric names ("FPPCA share of demand" is one metric, not a "share of")
    if set(e.get("intents", [])) & _MANY_INTENTS or _DECLINE_RE.search(e.get("rest_text", question) or ""):
        return None
    if len(e.get("fiscal_years", [])) > 1 or e.get("out_of_range") or e.get("unknown_tariffs") or e.get("unknown_subdivisions"):
        return None
    sc = scopes.from_entities(e)
    if sc is None:
        return None
    fys = e.get("fiscal_years", [])
    fy = fys[0] if fys else mart_layer.latest_fy()
    prev = mart_layer.previous_fy(fy)
    notes = [] if fys else [default_period_note(fy)]
    if e.get("relative_year") and fys:
        notes.append(f"'This year' is taken as the latest year in the data ({mart_layer.latest_fy()})."
                     if fy == mart_layer.latest_fy() else f"'Last year' is taken as {fy}.")

    exprs = [mart_layer.metric_sql(m) for m in metrics]
    extra = []
    for m in metrics:   # the parts of a ratio, for context
        ex = mart_layer.metric(m)["expr"]
        if "den" in ex:
            extra += [f"{ex['num']} AS {m}__num", f"{ex['den']} AS {m}__den"]
    sql = (f"SELECT fiscal_year, {', '.join(exprs + extra)}, COUNT(*) AS n_rows FROM {sc.table} "
           f"WHERE {sc.where_sql(['fiscal_year IN (?, ?)'])} GROUP BY fiscal_year ORDER BY fiscal_year")
    params = sc.params + [fy, prev or fy]
    res = await db.run(sql, max_rows=10, timeout_seconds=timeout, params=params)
    by_fy = {r[0]: dict(zip(res.columns, r)) for r in res.rows}
    cur = by_fy.get(fy)

    if not cur or all(cur.get(m) is None for m in metrics):
        first, last = scopes.availability(sc)
        why = ""
        if first and last and not (first <= fy <= last):
            why = f" It has figures for {first} to {last} only." if first != last else f" It has figures for {first} only."
        what = sc.label
        # the place / family has no data that year -> as before; the place has data but this measure wasn't printed
        # (MNR before 2018-19) -> name the measure and give its caveat
        head = (f"**There are no figures for {what} in {fy}.**{why}" if why or not cur else
                f"**There are no {', '.join(_describe(m) for m in metrics)} figures for {what} in {fy}.**")
        cav = mart_layer.caveats(sc.families_used, [fy], [sc.subdivision] if sc.subdivision else [], metrics_used=metrics)
        return MetricAnswer("\n\n".join([head] + cav), sql, params, res.columns, res.rows, metrics[0], notes + cav)

    # Amount and % (#3): an amount for a part (a sub-division, a tariff family, LT / HT) also says what share of the
    # whole it is -- the division, or for a family in a sub-division that sub-division.
    parent = _parent(sc)
    shares: dict[str, float] = {}
    amounts = [m for m in metrics if mart_layer.metric(m)["unit"] not in ("%", "ratio", "Rs/unit", "paise/unit")]
    if parent is not None and amounts:
        pres = await db.run(f"SELECT {', '.join(mart_layer.metric_sql(m) for m in amounts)} FROM {parent.table} "
                            f"WHERE {parent.where_sql(['fiscal_year = ?'])}", max_rows=1, timeout_seconds=timeout,
                            params=parent.params + [fy])
        if pres.rows:
            for m, total in zip(amounts, pres.rows[0]):
                if total and cur.get(m) is not None and total > 0 and cur[m] >= 0:
                    shares[m] = cur[m] * 100.0 / total
    lines = []
    for m in metrics:
        v, p = cur.get(m), (by_fy.get(prev) or {}).get(m) if prev else None
        meta = mart_layer.metric(m)
        when = f"at the end of {fy}" if meta["kind"] == "stock" else f"in {fy}"
        label = _describe(m)
        verb = "were" if label.endswith(("installations", "charges")) else "was"
        head = f"{label[:1].upper()}{label[1:]} for {sc.label} {when} {verb} {fmt_metric(m, v)}"
        ex = meta["expr"]
        # "(X against Y)" only for a plain SUM(a) / SUM(b) ratio (not for AT&C's composite formula)
        nm, dn = re.fullmatch(r"SUM\((\w+)\)", ex["num"].strip()), re.fullmatch(r"SUM\((\w+)\)", ex.get("den", "").strip())
        if "den" in ex and nm and dn and cur.get(f"{m}__den") is not None and meta["unit"] in ("%", "ratio", "Rs/unit", "paise/unit"):
            head += f" ({fmt_value(nm[1], cur[f'{m}__num'])} against {fmt_value(dn[1], cur[f'{m}__den'])})"
        if m in shares:
            head += f", {shares[m]:.2f}% of {parent.label}"
        change = ""
        if p is not None and v is not None:
            if meta["unit"] == "%":
                d = v - p
                change = (f"{'up' if d >= 0 else 'down'} {abs(d):.2f} points from {fmt_metric(m, p)} in {prev}")
            elif p:
                d = (v / p - 1) * 100
                change = f"{'up' if d >= 0 else 'down'} {abs(d):.1f}% from {fmt_metric(m, p, exact=False)} in {prev}"
        lines.append((head, change))
    parts = []
    for i, (head, change) in enumerate(lines):
        if i == 0:
            parts.append(f"**{head}.**" + (f" {change[:1].upper()}{change[1:]}." if change else ""))
        else:
            parts.append(head + (f"; {change}." if change else "."))
    if metrics[0] == "installations" and cur.get("installations") is not None and sc.kind in ("division", "subdivision"):
        live = await db.run(f"SELECT SUM(live_installations), SUM(dc_mnr_installations) FROM {sc.table} WHERE "
                            f"{sc.where_sql(['fiscal_year = ?'])}", max_rows=1, timeout_seconds=timeout, params=sc.params + [fy])
        lv, dc = live.rows[0]
        if lv is not None:
            parts.append(f"Of these, {fmt_value('x_count', lv)} were live and {fmt_value('x_count', dc)} were DC/MNR.")
    cav = mart_layer.caveats(sc.families_used, [fy], [sc.subdivision] if sc.subdivision else [], metrics_used=metrics)
    text = "\n\n".join(parts + notes + cav)
    return MetricAnswer(text, sql, params, res.columns, res.rows, metrics[0], notes + cav)


def _parent(sc: scopes.Scope) -> scopes.Scope | None:
    """The whole a scope is part of: the division for a sub-division / family / LT-HT, the sub-division for a family
    or LT-HT inside it; None for the division itself."""
    if sc.kind in ("family_sub", "voltage_sub"):
        p = scopes.build(sc.subdivision)
        return p
    if sc.kind in ("subdivision", "family", "voltage"):
        return scopes.build()
    return None


def label_for_fy(fy: str) -> str:
    return pretty_label(fy)
