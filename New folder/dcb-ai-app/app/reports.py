"""
Reviewed, parameterised reports for broad questions (pattern ported from the KPCL POC). Each report is fixed SQL over
the mart (read-only, bound parameters from validated entities only) plus a deterministic writer, so the same
question always gets the same, correct answer and no model call:

  trend              one scope, a measure over the years ("collection efficiency trend", "since 2020", "why ...")
  year_comparison    one scope, two years ("compare this year and last year collection")
  family_comparison  two or more tariff families ("LT-1 vs HT-2 demand"), one year or over the years
  subdivision_series two or more sub-divisions over the years
  voltage_split      LT vs HT vs miscellaneous for one year
  subdivision_rank   sub-divisions ranked / compared for one year ("which area has the most pending dues",
                     "Arkalgud or Konanur, which collects better", "worst performing area")
  tariff_rank        tariff families (or slabs) ranked / broken down for one scope and year ("top 3 tariff
                     categories by demand", "category-wise collection in Arkalgud")
  summary            the key figures of one scope for one year ("show me arkalgud numbers", "latest figures")

A report matches only on explicit wording AND the right entities; anything else falls through to the metric
lookup or the model. No year named -> the latest year, and the answer says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app import mart_layer, scope as scopes
from app.answer import build_chart, fmt_value, narrate, pretty_label
from app.db import ReadOnlyDB
from app.metric_lookup import default_period_note, fmt_metric

KPI_KEYS = ["demand", "collection", "collection_efficiency", "uncollected_demand", "closing_balance", "installations",
            "live_installations", "dc_mnr_installations", "consumption", "demand_per_unit"]


@dataclass
class Report:
    key: str
    answer: str
    sql: str
    params: list = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    chart: dict | None = None
    total: tuple | None = None       # a total row for the Excel report (flows / stocks only)
    notes: list[str] = field(default_factory=list)


async def _q(db: ReadOnlyDB, sql: str, params: list, timeout: float, max_rows: int = 500):
    return await db.run(sql, max_rows=max_rows, timeout_seconds=timeout, params=params)


def _b(text: str) -> str:
    return f"**{text}**"


def _one_fy(e: dict) -> tuple[str | None, list[str]]:
    fys = e.get("fiscal_years", [])
    if not fys:
        fy = mart_layer.latest_fy()
        return fy, [default_period_note(fy)]
    if len(fys) == 1:
        return fys[0], []
    return None, []


def _alias(key: str) -> str:
    return {"demand": "demand_total", "collection": "collection_grand_total", "collection_efficiency": "collection_efficiency_pct",
            "closing_balance": "closing_balance_total", "opening_balance": "opening_balance_total",
            "installations": "total_installations", "consumption": "consumption_total",
            "demand_per_unit": "demand_per_unit_rs", "demand_per_installation": "demand_per_installation_rs",
            "arrears_to_demand": "arrears_to_demand_ratio", "energy_charges": "demand_energy_charges",
            "fixed_charges": "demand_fixed_charges", "fac_demand": "demand_fac", "tax_demand": "demand_tax",
            "interest_demand": "demand_interest", "connected_load": "connected_load_kw"}.get(key, key)


def _expr(key: str) -> str:
    return mart_layer.metric_sql(key, _alias(key))


def _label(key: str) -> str:
    return mart_layer.metric(key)["label"]


# Consumer groups named in words ("industries", "farmers") map to more than one tariff family; the model decides.
_GROUP_WORDS_RE = re.compile(r"\b(industr\w*|factor(y|ies)|domestic|households?|homes?|farmers?|agricultur\w*|irrigation|"
                             r"pump\w*|commercial|shops?|business\w*|hospitals?|schools?|temples?|street ?lights?|"
                             r"water supply|apartments?|ev|charging|temporary|government|govt)\b", re.I)


def _short(label: str) -> str:
    return re.split(r" \(|:", label or "")[0]


def _metric_for(q: str, e: dict, default: str) -> str:
    ms = e.get("metrics", [])
    if ms:
        return ms[0]
    if re.search(r"\b(collect\w*|pay\w*|paying|perform\w*|efficien\w*|recover\w*)\b", q):
        return "collection_efficiency"
    if re.search(r"\b(pending|dues|arrears|outstanding|owe\w*)\b", q):
        return "closing_balance"
    return default


def _period_rows(fys: list[str] | None) -> tuple[str, list]:
    if fys:
        return f"fiscal_year IN ({', '.join('?' * len(fys))})", list(fys)
    return "1 = 1", []


def _caveats(families=None, fys=None, subs=None, scopes_=None, metrics=None) -> list[str]:
    return mart_layer.caveats(families or [], fys or [], subs or [], scopes_ or [], metrics or [])


def _finish(parts: list[str], notes: list[str]) -> str:
    return "\n\n".join([p for p in parts if p] + notes)


# ---------------------------------------------------------------------------------------------- trend / year comparison
_WHY_RE = re.compile(r"\bwhy\b|\breason\w*\b|\bwhat (caused|happened|explains)\b", re.I)


async def trend(q: str, e: dict, db: ReadOnlyDB, t: float) -> Report | None:
    intents = set(e.get("intents", []))
    fys = e.get("fiscal_years", [])
    why = bool(_WHY_RE.search(q))
    if not ("trend" in intents or len(fys) >= 3 or why or (len(fys) == 2 and intents & {"comparison", "change"})):
        return None
    # "Which sub-divisions need attention first, and why?" is a ranking with a reason, not a trend
    if len(e.get("metrics", [])) > 2 or (intents & {"ranking", "breakdown"} and (not why or re.search(r"\bwhich\b", q))):
        return None
    sc = scopes.from_entities(e)
    if sc is None:
        return None
    key = _metric_for(q, e, "demand")
    keys = e.get("metrics") or [key]
    exprs = ", ".join(_expr(k) for k in keys)
    cond, params = _period_rows(fys if len(fys) >= 2 else None)
    sql = f"SELECT fiscal_year, {exprs} FROM {sc.table} WHERE {sc.where_sql([cond])} GROUP BY fiscal_year ORDER BY fiscal_year"
    res = await _q(db, sql, sc.params + params, t)
    if not res.rows:
        return None
    col = _alias(keys[0])
    unit = mart_layer.metric(keys[0])["unit"]
    notes = []
    if len(fys) == 2 and len(res.rows) == 2:
        (fa, va), (fb, vb) = [(r[0], r[1]) for r in res.rows]
        if va is None or vb is None:
            return None
        if unit == "%":
            ch = f"{'up' if vb >= va else 'down'} {abs(vb - va):.2f} points"
        else:
            ch = f"{'up' if vb >= va else 'down'} {abs(vb / va - 1) * 100:.1f}%" if va else ""
        head = (f"{_label(keys[0])[:1].upper()}{_label(keys[0])[1:]} for {sc.label}: {fmt_metric(keys[0], vb)} in {fb} "
                f"against {fmt_metric(keys[0], va)} in {fa} ({ch}).")
        lines = []
        for k in keys[1:]:
            i = res.columns.index(_alias(k))
            a, b = res.rows[0][i], res.rows[1][i]
            lines.append(f"{_label(k)[:1].upper()}{_label(k)[1:]}: {fmt_metric(k, b)} in {fb} against {fmt_metric(k, a)} in {fa}.")
        text = _finish([_b(head)] + lines, notes + _caveats(sc.families_used, fys, [sc.subdivision] if sc.subdivision else [], metrics=keys))
        chart = build_chart("bar", "fiscal_year", col, _label(keys[0]), res.columns, res.rows)
        return Report("year_comparison", text, sql, sc.params + params, res.columns, res.rows, chart)
    body = narrate(res.columns, res.rows, col)
    head = f"{_label(keys[0])[:1].upper()}{_label(keys[0])[1:]} for {sc.label}, {res.rows[0][0]} to {res.rows[-1][0]}"
    if why:
        notes.append("The DCB statement records what was billed and collected each year, not the reasons behind a "
                     "change; the figures above show where and when it happened.")
    first, last = scopes.availability(sc)
    if sc.subdivision and last and last < mart_layer.latest_fy():
        notes.append(f"{sc.subdivision} has figures up to {last} only.")
    fam_fys = [r[0] for r in res.rows]
    text = _finish([_b(head + "."), body], notes + _caveats(sc.families_used, fam_fys, [sc.subdivision] if sc.subdivision else [], metrics=keys))
    extra = [(_alias(k), _label(k)) for k in keys[1:] if mart_layer.metric(k)["unit"] == unit]
    chart = build_chart("line", "fiscal_year", col, _label(keys[0]), res.columns, res.rows, extra)
    total = None
    if mart_layer.metric(keys[0])["kind"] == "flow" and len(keys) == 1:
        total = ("Total", sum(r[1] or 0 for r in res.rows))
    return Report("trend", text, sql, sc.params + params, res.columns, res.rows, chart, total)


# ---------------------------------------------------------------------------------------------- comparisons of several
async def family_comparison(q: str, e: dict, db: ReadOnlyDB, t: float) -> Report | None:
    fams = e.get("families", [])
    if len(fams) < 2 or e.get("slab_codes") or len(e.get("subdivisions", [])) > 1:
        return None
    key = _metric_for(q, e, "demand")
    sub = (e.get("subdivisions") or [None])[0]
    table = scopes.T_FAM_SUB if sub else scopes.T_FAM_DIV
    where = ["family_code IN (" + ", ".join("?" * len(fams)) + ")"] + (["subdivision_name = ?"] if sub else [])
    params = list(fams) + ([sub] if sub else [])
    fys = e.get("fiscal_years", [])
    place = f"in {sub}" if sub else "in the whole division"
    if len(fys) == 1:
        sql = (f"SELECT family_code, family_label, {_expr(key)} FROM {table} WHERE {' AND '.join(where)} AND fiscal_year = ? "
               f"GROUP BY family_code ORDER BY {_alias(key)} DESC NULLS LAST")
        res = await _q(db, sql, params + fys, t)
        found = {r[0] for r in res.rows}
        missing = [f for f in fams if f not in found]
        parts = [_b(f"{_label(key)[:1].upper()}{_label(key)[1:]} {place} in {fys[0]}: " + "; ".join(
            f"{r[0]} {fmt_metric(key, r[2])}" for r in res.rows) + ".")]
        if len(res.rows) >= 2 and res.rows[1][2]:
            a, b = res.rows[0], res.rows[1]
            if mart_layer.metric(key)["unit"] == "%":
                parts.append(f"{a[0]} is {a[2] - b[2]:.2f} points above {b[0]}.")
            else:
                parts.append(f"{a[0]} is {a[2] / b[2]:.2f} times {b[0]}.")
        if missing:
            parts.append(f"{', '.join(missing)} had no billing {place} in {fys[0]}.")
        chart = build_chart("bar", "family_code", _alias(key), _label(key), res.columns, res.rows)
        return Report("family_comparison", _finish(parts, _caveats(fams, fys, [sub] if sub else [], metrics=[key])), sql, params + fys,
                      res.columns, res.rows, chart)
    cond, pp = _period_rows(fys or None)
    # one column per family, the registry formula restricted to that family (any measure, ratios included)
    expr, n = mart_layer.metric_sql_filtered(key, "family_code = ?")
    pivot = ", ".join(f"{expr} AS \"{f}\"" for f in fams)
    pparams = [f for f in fams for _ in range(n)]
    sql = (f"SELECT fiscal_year, {pivot} FROM {table} WHERE {' AND '.join(where)} AND {cond} "
           f"GROUP BY fiscal_year ORDER BY fiscal_year")
    res = await _q(db, sql, pparams + params + pp, t)
    if not res.rows:
        return None
    last, first = res.rows[-1], res.rows[0]
    parts = [_b(f"{_label(key)[:1].upper()}{_label(key)[1:]} of {' and '.join(fams)} {place}, {first[0]} to {last[0]}.")]
    parts.append(f"In {last[0]}: " + "; ".join(f"{f} {fmt_metric(key, last[i + 1])}" for i, f in enumerate(fams)) + ".")
    parts.append(f"In {first[0]}: " + "; ".join(f"{f} {fmt_metric(key, first[i + 1])}" for i, f in enumerate(fams)) + ".")
    parts.append(_series_story(fams, res.rows, key))
    chart = build_chart("line", "fiscal_year", fams[0], fams[0], res.columns, res.rows, [(f, f) for f in fams[1:]])
    if chart:   # the series columns are named after the families, so the unit comes from the measure
        chart["unit"] = mart_layer.metric(key)["unit"]
    return Report("family_comparison", _finish(parts, _caveats(fams, [r[0] for r in res.rows], [sub] if sub else [], metrics=[key])),
                  sql, pparams + params + pp, res.columns, res.rows, chart)


def _series_story(names: list[str], rows: list[tuple], key: str) -> str:
    """How two or more series moved over the years, in plain words: each one's change from the first year to the
    last, and which was higher in how many years (RDPR / KPCL answer style)."""
    if len(rows) < 2:
        return ""
    pct = mart_layer.metric(key)["unit"] == "%"
    bits = []
    for i, n in enumerate(names):
        a, b = rows[0][i + 1], rows[-1][i + 1]
        if a is None or b is None:
            continue
        if pct:
            bits.append(f"{n} moved {b - a:+.2f} points")
        elif a:
            r = b / a
            bits.append(f"{n} grew {r:.1f} times" if r >= 2 else f"{n} changed {(r - 1) * 100:+.1f}%")
    text = (f"From {rows[0][0]} to {rows[-1][0]}, " + "; ".join(bits) + ".") if bits else ""
    if len(names) == 2:
        both = [r for r in rows if r[1] is not None and r[2] is not None]
        lead = sum(1 for r in both if r[1] > r[2])
        if both:
            if lead == len(both):
                text += f" {names[0]} was higher in every one of the {len(both)} years."
            elif lead == 0:
                text += f" {names[1]} was higher in every one of the {len(both)} years."
            else:
                text += f" {names[0]} was higher in {lead} of the {len(both)} years, {names[1]} in {len(both) - lead}."
    return text.strip()


async def subdivision_series(q: str, e: dict, db: ReadOnlyDB, t: float) -> Report | None:
    subs = [s for s in e.get("subdivisions", []) if s != "Division_Office"]
    fys = e.get("fiscal_years", [])
    if len(subs) < 2 or not ("trend" in e.get("intents", []) or len(fys) >= 2) or len(e.get("families", [])) > 1:
        return None
    key = _metric_for(q, e, "collection_efficiency")
    fam = (e.get("families") or [None])[0]
    table = scopes.T_FAM_SUB if fam else scopes.T_SUB
    cond, pp = _period_rows(fys or None)
    # one column per sub-division, the registry formula restricted to it (AT&C's composite formula included)
    expr, n = mart_layer.metric_sql_filtered(key, "subdivision_name = ?")
    cols = ", ".join(f"{expr} AS \"{s}\"" for s in subs)
    cp = [s for s in subs for _ in range(n)]
    where = [cond] + (["family_code = ?"] if fam else [])
    sql = f"SELECT fiscal_year, {cols} FROM {table} WHERE {' AND '.join(where)} GROUP BY fiscal_year ORDER BY fiscal_year"
    params = cp + pp + ([fam] if fam else [])
    res = await _q(db, sql, params, t)
    if not res.rows:
        return None
    last = res.rows[-1]
    what = f"{_label(key)}" + (f" for {fam}" if fam else "")
    parts = [_b(f"{what[:1].upper()}{what[1:]} of {', '.join(subs)}, {res.rows[0][0]} to {last[0]}."),
             f"In {last[0]}: " + "; ".join(f"{s} {fmt_metric(key, last[i + 1])}" for i, s in enumerate(subs)) + "."]
    chart = build_chart("line", "fiscal_year", subs[0], subs[0], res.columns, res.rows, [(s, s) for s in subs[1:]])
    if chart:   # the series columns are named after the sub-divisions, so the unit comes from the measure
        chart["unit"] = mart_layer.metric(key)["unit"]
    return Report("subdivision_series", _finish(parts, _caveats([fam] if fam else [], [r[0] for r in res.rows], subs, metrics=[key])),
                  sql, params, res.columns, res.rows, chart)


# ---------------------------------------------------------------------------------------------- LT / HT split
_VOLTAGE_SPLIT_RE = re.compile(r"\b(lt\s*(vs\.?|versus|and|&|/|or)\s*ht|ht\s*(vs\.?|versus|and|&|/|or)\s*lt|"
                               r"low tension\s*(vs\.?|versus|and|&|or)\s*high tension|lt[- /]ht (split|share|mix|breakdown)|"
                               r"voltage[- ]wise)\b", re.I)


async def voltage_split(q: str, e: dict, db: ReadOnlyDB, t: float) -> Report | None:
    if not _VOLTAGE_SPLIT_RE.search(q) or e.get("families") or len(e.get("subdivisions", [])) > 1:
        return None
    fy, notes = _one_fy(e)
    if fy is None:
        return None
    sub = (e.get("subdivisions") or [None])[0]
    table = scopes.T_FAM_SUB if sub else scopes.T_FAM_DIV
    where = "fiscal_year = ?" + (" AND subdivision_name = ?" if sub else "")
    params = [fy] + ([sub] if sub else [])
    sql = (f"SELECT voltage_class, {_expr('demand')}, {_expr('collection')}, {_expr('collection_efficiency')}, "
           f"{_expr('closing_balance')}, {_expr('installations')} FROM {table} WHERE {where} "
           f"GROUP BY voltage_class ORDER BY CASE voltage_class WHEN 'LT' THEN 1 WHEN 'HT' THEN 2 ELSE 3 END")
    res = await _q(db, sql, params, t)
    if not res.rows:
        return None
    rows = [dict(zip(res.columns, r)) for r in res.rows]
    total = sum(r["demand_total"] or 0 for r in rows)
    place = sub or "the whole division"
    head = f"LT, HT and miscellaneous revenue in {place}, {fy}"
    lines = [f"•  {r['voltage_class']}: demand {fmt_metric('demand', r['demand_total'], False)} "
             f"({(r['demand_total'] or 0) / total * 100:.1f}% of demand), collection "
             f"{fmt_metric('collection', r['collection_grand_total'], False)}, efficiency "
             f"{fmt_metric('collection_efficiency', r['collection_efficiency_pct'])}, "
             f"{fmt_value('x_count', r['total_installations'])} installations" for r in rows if total]
    chart = build_chart("bar", "voltage_class", "demand_total", "Demand (₹)", res.columns, res.rows)
    tot = ("Total", total)
    return Report("voltage_split", _finish([_b(head + "."), "\n".join(lines)], notes + _caveats(["MISC"], [fy], [sub] if sub else [])),
                  sql, params, res.columns, res.rows, chart, tot)


# ---------------------------------------------------------------------------------------------- rankings
_SUB_CONTEXT_RE = re.compile(r"\b(sub-?\s?divisions?|areas?|sections?|places?|where|locations?|offices?|regions?|zones?|"
                             r"all (the )?sub-?divisions)\b", re.I)
_TARIFF_CONTEXT_RE = re.compile(r"\b(tariffs?|categor(y|ies)|famil(y|ies)|slabs?|sub-?categor(y|ies)|schemes?|"
                                r"consumer (types?|groups?|classes)|lt categories|ht categories|which (lt|ht))\b", re.I)


async def subdivision_rank(q: str, e: dict, db: ReadOnlyDB, t: float) -> Report | None:
    intents = set(e.get("intents", []))
    subs = [s for s in e.get("subdivisions", []) if s != "Division_Office"]
    named_pair = len(subs) >= 2
    if not (named_pair or (_SUB_CONTEXT_RE.search(q) and intents & {"ranking", "breakdown", "comparison", "share", "summary"})):
        return None
    if _TARIFF_CONTEXT_RE.search(q) and not named_pair and not _SUB_CONTEXT_RE.search(q):
        return None
    if len(e.get("families", [])) > 1 or e.get("slab_codes"):
        return None
    if _GROUP_WORDS_RE.search(q) and not e.get("families"):
        return None
    fy, notes = _one_fy(e)
    if fy is None:
        return None
    key = _metric_for(q, e, "collection_efficiency")
    fam = (e.get("families") or [None])[0]
    table = scopes.T_FAM_SUB if fam else scopes.T_SUB
    where, params = ["fiscal_year = ?"], [fy]
    if fam:
        where.append("family_code = ?")
        params.append(fam)
    if named_pair:
        where.append("subdivision_name IN (" + ", ".join("?" * len(subs)) + ")")
        params += subs
    low = e.get("direction") == "low" or re.search(r"\b(not paying|poor\w*|weak\w*|lagging|attention|defaulting)\b", q)
    order = "ASC" if low else "DESC"
    others = [k for k in ("demand", "collection", "collection_efficiency", "closing_balance", "installations") if k != key]
    sql = (f"SELECT subdivision_name, {_expr(key)}, {', '.join(_expr(k) for k in others)} FROM {table} "
           f"WHERE {' AND '.join(where)} GROUP BY subdivision_name ORDER BY {_alias(key)} {order} NULLS LAST")
    res = await _q(db, sql, params, t)
    if not res.rows:
        return None
    rows = res.rows[:e["top_n"]] if e.get("top_n") else res.rows
    what = _label(key) + (f" for {fam}" if fam else "")
    lead, tail = rows[0], rows[-1]
    word = "lowest" if low else "highest"
    if named_pair and len(rows) == 2:
        a, b = (rows[0], rows[1]) if not low else (rows[1], rows[0])
        verb = {"collection_efficiency": "collected better", "closing_balance": "had more arrears"}.get(key, f"had higher {what}")
        head = (f"In {fy}, {a[0]} {verb} than {b[0]}: {what} {fmt_metric(key, a[1])} against {fmt_metric(key, b[1])}.")
    else:
        head = f"In {fy}, {lead[0]} had the {word} {what}: {fmt_metric(key, lead[1], False)}"
        if len(rows) > 1:
            head += f"; {tail[0]} the {'highest' if low else 'lowest'}: {fmt_metric(key, tail[1], False)}"
        head += "."
    lines = "\n".join(f"•  {r[0]} — {fmt_metric(key, r[1], False)}" + (
        f" (collection efficiency {fmt_metric('collection_efficiency', r[res.columns.index('collection_efficiency_pct')])})"
        if key != "collection_efficiency" and "collection_efficiency_pct" in res.columns else "") for r in rows)
    cav = _caveats([fam] if fam else [], [fy], subs, ["division_vs_subdivisions"] if mart_layer.metric(key)["kind"] != "ratio" and not named_pair else [],
                   metrics=[key])
    closed = [s["name"] for s in mart_layer.subdivisions() if s["is_geographic"] and s["last_fy"] < fy]
    if closed and not named_pair:
        notes.append(f"{', '.join(closed)} had closed by {fy}.")
    chart = build_chart("bar", "subdivision_name", _alias(key), what[:1].upper() + what[1:], res.columns, rows)
    return Report("subdivision_rank", _finish([_b(head), lines], notes + cav), sql, params, res.columns, rows, chart)


async def tariff_rank(q: str, e: dict, db: ReadOnlyDB, t: float) -> Report | None:
    intents = set(e.get("intents", []))
    if not (_TARIFF_CONTEXT_RE.search(q) and intents & {"ranking", "breakdown", "share", "summary", "comparison"}):
        return None
    if len(e.get("families", [])) > 1 or len(e.get("subdivisions", [])) > 1 or e.get("slab_codes"):
        return None
    if _GROUP_WORDS_RE.search(q) and not e.get("families"):
        return None
    fy, notes = _one_fy(e)
    if fy is None:
        return None
    key = _metric_for(q, e, "demand")
    sub = (e.get("subdivisions") or [None])[0]
    slab = bool(re.search(r"\b(slabs?|sub-?categor(y|ies))\b", q)) or bool(e.get("families"))
    fam = (e.get("families") or [None])[0]
    if slab:
        table = scopes.T_SLAB_SUB if sub else scopes.T_SLAB_DIV
        label_cols = "tariff_code, description"
        group = "tariff_code, description"
    else:
        table = scopes.T_FAM_SUB if sub else scopes.T_FAM_DIV
        label_cols = "family_code, family_label"
        group = "family_code, family_label"
    where, params = ["fiscal_year = ?"], [fy]
    if sub:
        where.append("subdivision_name = ?")
        params.append(sub)
    if fam:
        where.append("family_code = ?")
        params.append(fam)
    if e.get("voltage") in ("LT", "HT"):
        where.append("voltage_class = ?")
        params.append(e["voltage"])
    elif not re.search(r"\bmisc", q) and not slab and key not in ("collection_efficiency",):
        pass
    order = "ASC" if e.get("direction") == "low" else "DESC"
    sql = (f"SELECT {label_cols}, {_expr(key)} FROM {table} WHERE {' AND '.join(where)} "
           f"GROUP BY {group} HAVING {_alias(key)} IS NOT NULL ORDER BY {_alias(key)} {order} NULLS LAST")
    res = await _q(db, sql, params, t)
    if not res.rows:
        return None
    all_rows = res.rows
    rows = all_rows[:e["top_n"]] if e.get("top_n") else all_rows
    vi = 2
    place = f"in {sub}" if sub else "in the whole division"
    what = _label(key)
    lead = rows[0]
    kind = mart_layer.metric(key)["kind"]
    head = f"In {fy}, {lead[0]} ({_short(lead[1]) if not slab else lead[1][:60]}) had the {'lowest' if order == 'ASC' else 'highest'} {what} {place}: {fmt_metric(key, lead[vi])}"
    total = sum(r[vi] or 0 for r in all_rows) if kind != "ratio" else None
    if total and kind != "ratio" and lead[vi] and lead[vi] > 0:
        head += f", {lead[vi] / total * 100:.1f}% of the {fmt_metric(key, total, False)} total"
    head += "."
    lines = "\n".join(f"•  {r[0]} ({_short(r[1]) if not slab else r[1][:60]}) — {fmt_metric(key, r[vi], False)}"
                      + (f", {r[vi] / total * 100:.1f}%" if total and r[vi] is not None else "") for r in rows[:10])
    more = f"The other {len(rows) - 10} are in the table below." if len(rows) > 10 else ""
    fams_used = [r[0] for r in rows] if not slab else ([fam] if fam else [])
    cav = _caveats(fams_used, [fy], [sub] if sub else [], metrics=[key])
    chart = build_chart("bar", res.columns[0], _alias(key), what[:1].upper() + what[1:], res.columns, rows[:15])
    tot = ("Total", "", total) if total is not None else None
    return Report("tariff_rank", _finish([_b(head), lines, more], notes + cav), sql, params, res.columns, rows, chart, tot)


# ---------------------------------------------------------------------------------------------- summary
async def summary(q: str, e: dict, db: ReadOnlyDB, t: float) -> Report | None:
    intents = set(e.get("intents", []))
    named = e.get("subdivisions") or e.get("families") or e.get("scope") == "division" or e.get("voltage")
    if not ("summary" in intents or (named and not e.get("metrics") and not intents)):
        return None
    if intents & {"ranking", "trend", "breakdown"} or len(e.get("metrics", [])) > 0 and "summary" not in intents:
        return None
    sc = scopes.from_entities(e)
    fy, notes = _one_fy(e)
    if sc is None or fy is None:
        return None
    prev = mart_layer.previous_fy(fy)
    keys = [k for k in KPI_KEYS if not (sc.kind in ("family", "family_sub", "voltage", "voltage_sub") and k == "dc_mnr_installations")]
    sql = (f"SELECT fiscal_year, {', '.join(_expr(k) for k in keys)} FROM {sc.table} "
           f"WHERE {sc.where_sql(['fiscal_year IN (?, ?)'])} GROUP BY fiscal_year ORDER BY fiscal_year")
    params = sc.params + [fy, prev or fy]
    res = await _q(db, sql, params, t)
    by = {r[0]: dict(zip(res.columns, r)) for r in res.rows}
    cur, old = by.get(fy), by.get(prev) if prev else None
    if not cur:
        first, last = scopes.availability(sc)
        return Report("summary", _b(f"There are no figures for {sc.label} in {fy}.") +
                      (f" It has figures for {first} to {last} only." if first and last else ""), sql, params)
    lines, table_rows = [], []
    for k in keys:
        a = _alias(k)
        v, p = cur.get(a), (old or {}).get(a)
        if v is None:
            continue
        unit = mart_layer.metric(k)["unit"]
        ch = ""
        if p is not None:
            ch = f" ({v - p:+.2f} points)" if unit == "%" else (f" ({(v / p - 1) * 100:+.1f}%)" if p else "")
        lines.append(f"•  {_label(k)[:1].upper()}{_label(k)[1:]}: {fmt_metric(k, v, False)}{ch}")
        table_rows.append((_label(k), v, p, (v - p) if p is not None else None))
    head = (f"{sc.label}, {fy}: demand {fmt_metric('demand', cur.get('demand_total'), False)}, collection "
            f"{fmt_metric('collection', cur.get('collection_grand_total'), False)}, collection efficiency "
            f"{fmt_metric('collection_efficiency', cur.get('collection_efficiency_pct'))}.")
    parts = [_b(head), "\n".join(lines) + (f"\n\nChanges are against {prev}." if old else "")]
    top_sql = ""
    if sc.kind in ("division", "subdivision"):
        tt = scopes.T_FAM_SUB if sc.subdivision else scopes.T_FAM_DIV
        tw = "fiscal_year = ?" + (" AND subdivision_name = ?" if sc.subdivision else "")
        top_sql = f"SELECT family_code, family_label, SUM(demand_total) AS demand_total FROM {tt} WHERE {tw} GROUP BY family_code ORDER BY demand_total DESC LIMIT 3"
        top = await _q(db, top_sql, [fy] + ([sc.subdivision] if sc.subdivision else []), t)
        if top.rows and cur.get("demand_total"):
            parts.append("Largest tariff families by demand: " + "; ".join(
                f"{r[0]} ({_short(r[1])}) {fmt_metric('demand', r[2], False)}, "
                f"{r[2] / cur['demand_total'] * 100:.1f}%" for r in top.rows) + ".")
    cols = ["measure", fy, prev or "previous", "change"]
    return Report("summary", _finish(parts, notes + _caveats(sc.families_used, [fy], [sc.subdivision] if sc.subdivision else [])),
                  sql + (";\n" + top_sql if top_sql else ""), params, cols, table_rows,
                  build_chart("bar", "fiscal_year", "collection_efficiency_pct", "Collection efficiency %", res.columns, res.rows))


# ---------------------------------------------------------------------------------------------- dispatch
ORDER = [trend, family_comparison, subdivision_series, voltage_split, subdivision_rank, tariff_rank, summary]


async def match_and_run(question: str, e: dict, db: ReadOnlyDB, timeout: float) -> Report | None:
    q = (question or "").lower()
    if e.get("slab_codes") or e.get("unknown_tariffs") or e.get("unknown_subdivisions") or e.get("out_of_range"):
        return None
    for fn in ORDER:
        rep = await fn(q, e, db, timeout)
        if rep is not None:
            return rep
    return None
