"""
Deterministic answer writing -- never a second LLM call (pattern ported from the KPCL POC, DCB units):

  * figures are formatted by their unit, taken from the mart's column docs with name-based fallbacks for aliases
    the model invents: rupees as crore / lakh with the exact amount alongside, counts, units (kWh), %, ratios;
  * one-row model results -> the model's answer_template filled from the row (app.pipeline keeps that part);
  * many-row results -> plain-language narration: who leads, share of the total (only for amounts that add up),
    spread, first-to-last change for a year series, then a short list and a pointer to the table;
  * result_table / build_chart turn any result into the table and chart the chat page renders -- chart values
    always come from the real rows, never from the model.
"""
from __future__ import annotations

import math
import re

from app import mart_layer

LIST_LIMIT = 6
TABLE_ROW_CAP = 500

# ---------------------------------------------------------------------------------------------- units
_UNIT_BY_NAME = [
    (r"paise", "paise/unit"),
    (r"(pct|percent|percentage|efficiency|share)", "%"),
    (r"(per_unit|rate_rs|rs_per_unit|avg_rate|average_rate|tariff_rate)", "Rs/unit"),
    (r"ratio", "ratio"),
    (r"(installation|connections|consumers|_count$|^count|^n_|number_of|slab_count|years_with_data)", "count"),
    (r"(consumption|units_billed|kwh|energy_sold|^units$|_units$)", "units"),
    (r"(connected_load_hp|_hp$)", "HP"), (r"(connected_load_kva|_kva$)", "kVA"), (r"(connected_load|_kw$|load_kw)", "kW"),
    (r"(demand|collection|collected|balance|arrears|dues|outstanding|amount|revenue|charges?|fac|tax|interest|"
     r"surcharge|deposit|uncollected|shortfall|gap|_rs$|rupees|billed|realis|realiz)", "Rs"),
]
_PRECISION = {"%": 2, "Rs/unit": 2, "paise/unit": 1, "ratio": 2, "count": 0, "units": 0, "kW": 2, "HP": 2, "kVA": 2, "Rs": 2}


def col_unit(col: str) -> str | None:
    info = mart_layer.column_info().get(col)
    if info and info.get("unit"):
        return info["unit"]
    meta = mart_layer.metrics().get(col)
    if meta and meta.get("unit") in ("%", "ratio", "count", "units"):
        return meta["unit"]
    c = (col or "").lower()
    if c in ("fiscal_year", "start_year", "end_year", "fy_order", "family_order"):
        return None
    for pat, unit in _UNIT_BY_NAME:
        if re.search(pat, c):
            return unit
    return None


def col_kind(col: str) -> str | None:
    info = mart_layer.column_info().get(col)
    if info and info.get("kind"):
        return info["kind"]
    unit = col_unit(col)
    if unit in ("%", "ratio", "Rs/unit", "paise/unit") or re.search(r"(avg|average|mean|per_)", (col or "").lower()):
        return "ratio"
    if unit == "count" or re.search(r"(balance|arrears|outstanding|dues|deposit|load)", (col or "").lower()):
        return "stock"
    if unit in ("Rs", "units"):
        return "flow"
    return None


def adds_up_over_places(col: str) -> bool:
    return col_kind(col) in ("flow", "stock")


def adds_up_over_years(col: str) -> bool:
    return col_kind(col) == "flow"


def to_float(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("₹", "")
        try:
            return float(s) if s else None
        except ValueError:
            return None
    return None


def fmt_number(v: float, places: int) -> str:
    return f"{v:,.{places}f}" if places > 0 else f"{v:,.0f}"


def fmt_rupees(v: float, exact: bool = False) -> str:
    """₹305.90 crore / ₹12.34 lakh / ₹1,234.56; exact=True adds the full amount: ₹305.90 crore (₹3,058,984,018.63)."""
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e7:
        short = f"{sign}₹{a / 1e7:,.2f} crore"
    elif a >= 1e5:
        short = f"{sign}₹{a / 1e5:,.2f} lakh"
    else:
        return f"{sign}₹{a:,.2f}"
    return f"{short} (₹{fmt_number(v, 2)})" if exact else short


def fmt_value(col: str, v, exact: bool = False) -> str:
    """A figure with its unit: ₹305.90 crore / 93.62% / 181,893 / 375,153,780 units / 7.12 per unit."""
    if v is None:
        return "no data"
    num = to_float(v)
    if num is None:
        return pretty_label(v)
    unit = col_unit(col)
    if unit == "Rs":
        return fmt_rupees(num, exact)
    if unit is None:
        return fmt_number(num, 0 if float(num).is_integer() else 2)
    text = fmt_number(num, _PRECISION.get(unit, 2))
    if unit == "%":
        return text + "%"
    if unit in ("count", "ratio"):
        return text
    if unit == "Rs/unit":
        return f"₹{text} per unit"
    if unit == "paise/unit":
        return f"{text} paise per unit"
    return f"{text} {unit}"


def fmt_plain(v) -> str:
    """The number formatting model-written templates get (they may add their own ₹ / % signs)."""
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return f"{int(v):,}"
        return f"{v:,.2f}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    if v is None:
        return "N/A"
    return str(v)


# ---------------------------------------------------------------------------------------------- labels
_FY_RE = re.compile(r"^(\d{4})-(\d{2})$")


def is_fy(s) -> bool:
    m = _FY_RE.match(str(s or ""))
    return bool(m) and (int(m.group(1)) + 1) % 100 == int(m.group(2))


def pretty_label(v) -> str:
    s = "" if v is None else str(v)
    if s == "Division_Total":
        return "Whole division"
    if s == "Division_Office":
        return "Division office"
    if is_fy(s):
        return f"FY {s}"
    return s or "Unspecified"


_WORDS = {"pct": "%", "fy": "FY", "rs": "(₹)", "dc": "DC", "mnr": "MNR", "lt": "LT", "ht": "HT", "fac": "FAC", "kw": "(kW)",
          "hp": "(HP)", "kva": "(kVA)", "yoy": "YoY", "misc": "misc", "ob": "OB", "cb": "CB", "pg": "P&G", "subdivision": "sub-division",
          "id": "", "code": "code", "grand": "", "per": "per", "no": "no."}
_HEADERS = {"fiscal_year": "Fiscal year", "subdivision_name": "Sub-division", "family_code": "Tariff family",
            "family_label": "Category", "tariff_code": "Tariff code", "description": "Slab", "demand_total": "Demand (₹)",
            "collection_grand_total": "Collection (₹)", "closing_balance_total": "Closing balance (₹)",
            "opening_balance_total": "Opening balance (₹)", "collection_efficiency_pct": "Collection efficiency %",
            "total_installations": "Installations", "live_installations": "Live installations",
            "consumption_total": "Consumption (units)", "uncollected_demand": "Uncollected demand (₹)",
            "dc_mnr_installations": "DC/MNR installations", "demand_per_unit_rs": "Demand per unit (₹)",
            "voltage_class": "LT / HT"}


def header(col: str) -> str:
    if col in _HEADERS:
        return _HEADERS[col]
    words = [_WORDS.get(w, w) for w in (col or "").lower().split("_")]
    text = " ".join(w for w in words if w).strip()
    return (text[:1].upper() + text[1:]) if text else col


def in_sentence(col: str) -> str:
    h = re.sub(r"\s*\((₹|units|kW|HP|kVA)\)", "", header(col))
    return h[:1].lower() + h[1:] if len(h) > 1 and h[1].islower() else h


# ---------------------------------------------------------------------------------------------- tables and charts
def result_table(columns: list[str], rows: list[tuple]) -> dict | None:
    """{columns: headers, keys: raw column names, units, rows: formatted cells, raw: raw values} for the page and CSV."""
    if not columns or not rows:
        return None
    shown = rows[:TABLE_ROW_CAP]
    return {
        "columns": [header(c) for c in columns],
        "keys": list(columns),
        "units": [col_unit(c) for c in columns],
        "rows": [[_cell(c, v) for c, v in zip(columns, r)] for r in shown],
        "raw": [[v for v in r] for r in shown],
        "truncated": len(rows) > len(shown),
    }


def _cell(col: str, v) -> str:
    if v is None:
        return "—"
    num = to_float(v) if not isinstance(v, str) else None
    if num is None:
        return pretty_label(v)
    unit = col_unit(col)
    if unit == "Rs":
        return fmt_number(num, 2)
    if unit is None:
        return fmt_number(num, 0 if float(num).is_integer() else 2)
    return fmt_number(num, _PRECISION.get(unit, 2)) + ("%" if unit == "%" else "")


def build_chart(chart_type: str, label_col: str, value_col: str, series_label: str, columns: list[str],
                rows: list[tuple], extra: list[tuple[str, str]] | None = None) -> dict | None:
    """Chart data from the real rows (fails closed to None). `extra` adds series: [(value_col, label)]."""
    if not rows or label_col not in columns or value_col not in columns or chart_type not in ("bar", "line"):
        return None
    li = columns.index(label_col)
    series_cols = [(value_col, series_label or header(value_col))] + list(extra or [])
    labels, data = [], [[] for _ in series_cols]
    for r in rows:
        vals = [to_float(r[columns.index(c)]) if c in columns else None for c, _ in series_cols]
        if vals[0] is None:
            continue
        labels.append(pretty_label(r[li]) if not is_fy(r[li]) else str(r[li]))
        for i, v in enumerate(vals):
            data[i].append(v)
    if not labels:
        return None
    unit = col_unit(value_col)
    chart = {"type": chart_type, "labels": labels, "values": data[0], "series_label": series_cols[0][1], "unit": unit}
    if len(series_cols) > 1:
        chart["series"] = [{"label": lab, "values": vals} for (c, lab), vals in zip(series_cols, data)]
    return chart


# ---------------------------------------------------------------------------------------------- narration
def _label_index(columns: list[str], rows: list[tuple]) -> int:
    for pref in ("subdivision_name", "family_code", "tariff_code", "fiscal_year", "description"):
        if pref in columns and len({r[columns.index(pref)] for r in rows}) > 1:
            return columns.index(pref)
    for i, c in enumerate(columns):
        if all(isinstance(r[i], str) or r[i] is None for r in rows) and len({r[i] for r in rows}) > 1:
            return i
    return 0


def _value_index(columns: list[str], rows: list[tuple], label_i: int, preferred: str | None) -> int | None:
    if preferred in columns:
        return columns.index(preferred)
    numeric = [i for i, c in enumerate(columns) if i != label_i
               and any(to_float(r[i]) is not None and not isinstance(r[i], str) for r in rows)
               and c not in ("fy_order", "start_year", "end_year", "family_order", "slab_count")]
    for i in numeric:
        if col_unit(columns[i]) == "%":
            return i
    return numeric[0] if numeric else None


def _composite_rows(columns: list[str], rows: list[tuple], li: int) -> list[tuple]:
    """When the label column repeats (two slabs share a code, several slabs per year), label each row with every
    varying text column joined, so no two list lines look identical."""
    labels = [r[li] for r in rows]
    if len(set(labels)) == len(labels):
        return rows
    text_cols = [i for i, c in enumerate(columns)
                 if all(isinstance(r[i], str) or r[i] is None for r in rows) and len({r[i] for r in rows}) > 1]
    out = []
    for r in rows:
        lab = " · ".join(str(r[i]) for i in text_cols if r[i] is not None) or str(r[li])
        out.append(tuple(lab if i == li else v for i, v in enumerate(r)))
    return out


def narrate(columns: list[str], rows: list[tuple], preferred: str | None = None) -> str:
    """Plain-language summary of a many-row result."""
    if not rows:
        return ""
    # A year series takes priority: several slabs / codes per year are summed per year when the measure adds up
    # over places, otherwise listed row by row with composite labels.
    if "fiscal_year" in columns and len({r[columns.index("fiscal_year")] for r in rows}) > 1:
        fi = columns.index("fiscal_year")
        vi = _value_index(columns, rows, fi, preferred)
        if vi is not None and len({r[fi] for r in rows}) < len(rows):
            if adds_up_over_places(columns[vi]):
                per: dict = {}
                for r in rows:
                    v = to_float(r[vi])
                    if v is not None:
                        per[r[fi]] = per.get(r[fi], 0.0) + v
                return narrate(["fiscal_year", columns[vi]], sorted(per.items()), columns[vi])
            rows = _composite_rows(columns, rows, fi)
            return _narrate_list(columns, rows, fi, vi)
        if vi is not None:
            return _narrate(columns, rows, fi, vi)
    li = _label_index(columns, rows)
    vi = _value_index(columns, rows, li, preferred)
    rows = _composite_rows(columns, rows, li)
    return _narrate(columns, rows, li, vi)


def _narrate_list(columns: list[str], rows: list[tuple], li: int, vi: int) -> str:
    col = columns[vi]
    shown = rows[:LIST_LIMIT * 2]
    lines = "\n".join(f"•  {pretty_label(r[li])} — {fmt_value(col, r[vi])}" for r in shown)
    more = f"\n\nThe other {len(rows) - len(shown)} are in the table below." if len(rows) > len(shown) else ""
    return f"{header(col).split(' (')[0]} by year and category:\n\n{lines}{more}"


_NOUNS = [("subdivision", "sub-divisions"), ("family", "tariff categories"), ("tariff", "tariff categories"),
          ("booklet", "tariff categories"), ("description", "slabs"), ("slab", "slabs"), ("voltage", "classes"),
          ("segment", "segments"), ("fiscal_year", "years")]


def _noun(col: str) -> str:
    c = (col or "").lower()
    return next((n for hint, n in _NOUNS if hint in c), "items")


def _name(col: str) -> str:
    """The measure's name inside a sentence, without a trailing unit sign ("collection efficiency", not "... %")."""
    return re.sub(r"\s*%$", "", in_sentence(col)).strip()


def _narrate(columns: list[str], rows: list[tuple], li: int, vi: int | None) -> str:
    """Plain sentences first (RDPR / KPCL style): the range and the latest against the first for a year series; the
    leader, its share and the runners-up for a ranking; then a short list, the rest pointing to the table."""
    if vi is None:
        items = [pretty_label(r[li]) for r in rows[:LIST_LIMIT * 2]]
        more = f" and {len(rows) - len(items)} more" if len(rows) > len(items) else ""
        return f"{len(rows)} results: " + ", ".join(items) + more + "."
    col = columns[vi]
    pts = [(r[li], to_float(r[vi])) for r in rows if to_float(r[vi]) is not None]
    if not pts:
        return f"No figures for {_name(col)} in this result."
    f = lambda v: fmt_value(col, v)  # noqa: E731
    pct = col_unit(col) == "%"
    name = _name(col)
    time_series = all(is_fy(p[0]) for p in pts)
    out = []
    if time_series and len(pts) > 1:
        pts = sorted(pts, key=lambda p: str(p[0]))
        (a_lab, a), (b_lab, b) = pts[0], pts[-1]
        hi = max(pts, key=lambda p: p[1])
        lo = min(pts, key=lambda p: p[1])
        n = len(pts)
        A, B, H, L = (pretty_label(x) for x in (a_lab, b_lab, hi[0], lo[0]))
        if adds_up_over_years(col) and n >= 3 and all(v >= 0 for _, v in pts):
            text = (f"Over the {n} years from {A} to {B}, {name} totalled {f(sum(v for _, v in pts))}. "
                    f"It was highest in {H} ({f(hi[1])}) and lowest in {L} ({f(lo[1])}).")
        else:
            text = f"Across the {n} years from {A} to {B}, {name} ranged from {f(lo[1])} in {L} to {f(hi[1])} in {H}."
        if pct:
            d = b - a
            text += (f" The latest, {f(b)} in {B}, is about level with the first." if abs(d) < 0.05 else
                     f" The latest, {f(b)} in {B}, is {abs(d):.2f} points {'higher' if d > 0 else 'lower'} than the first "
                     f"({f(a)} in {A}).")
        elif a:
            ch = (b / a - 1) * 100
            text += (f" The latest, {f(b)} in {B}, is about level with the first." if abs(ch) < 1 else
                     f" The latest, {f(b)} in {B}, is {abs(ch):.1f}% {'higher' if ch > 0 else 'lower'} than the first "
                     f"({f(a)} in {A})" + (f", {b / a:.1f} times as much." if ch > 100 else "."))
        out.append(text)
    else:
        ranked = sorted(pts, key=lambda p: p[1], reverse=True)
        noun = _noun(columns[li])
        top, bottom = ranked[0], ranked[-1]
        total = sum(p[1] for p in pts)
        if adds_up_over_places(col) and total > 0 and top[1] > 0 and len(ranked) > 1 and all(v >= 0 for _, v in pts):
            text = (f"{pretty_label(top[0])} has the highest {name}, {f(top[1])}, which is {top[1] / total * 100:.1f}% of the "
                    f"{f(total)} total across {len(ranked)} {noun}.")
            if len(ranked) >= 3:
                top3 = sum(v for _, v in ranked[:3])
                text += (f" It is followed by {pretty_label(ranked[1][0])} ({f(ranked[1][1])}) and {pretty_label(ranked[2][0])} "
                         f"({f(ranked[2][1])}); together the top three are {top3 / total * 100:.1f}%.")
            elif len(ranked) == 2:
                text += f" {pretty_label(bottom[0])} has {f(bottom[1])}."
        elif len(ranked) > 1:
            text = (f"{pretty_label(top[0])} has the highest {name}, {f(top[1])}, and {pretty_label(bottom[0])} the lowest, "
                    f"{f(bottom[1])}.")
            if pct and len(ranked) >= 3:
                text += f" The gap between them is {top[1] - bottom[1]:.2f} points."
        else:
            text = f"{pretty_label(top[0])}: {f(top[1])}."
        out.append(text)
        pts = ranked
    shown = pts[:LIST_LIMIT]
    out.append("\n".join(f"•  {pretty_label(lab)} — {f(v)}" for lab, v in shown))
    if len(pts) > len(shown):
        out.append(f"The other {len(pts) - len(shown)} are in the table below.")
    return "\n\n".join(out)


# ---------------------------------------------------------------------------------------------- final polish
# Every route's answer gets the same finish (RDPR / KPCL reading style): the exact rupee amount is kept once, in the
# bold headline (the table always has every exact figure); later paragraphs show crore / lakh only, unless the
# question asks for the exact amount. Caveat paragraphs are labelled "Note:".
_EXACT_ASK_RE = re.compile(r"\b(exact|exactly|precise|full (amount|figure)|in rupees|rupees?\b|actual amount|to the rupee)\b", re.I)
_EXACT_AFTER_RE = re.compile(r"(crore|lakh)\s\(₹-?[\d,]+(?:\.\d+)?\)")
_NOTE_STARTS = ("No year was named", "Saligrama had closed", "'This year' is", "\u2018This year\u2019 is", "Miscellaneous revenue",
                "From 2024-25 the tariff restructuring", "Up to 2017-18", "These slabs have figures")


def polish(question: str, text: str, notes: list[str] | None = None) -> str:
    if not text:
        return text
    keep_exact = bool(_EXACT_ASK_RE.search(question or ""))
    known = {n.strip() for n in (notes or []) if n}
    try:
        known |= {i["caveat"].strip() for i in mart_layer.known_issues() if i.get("caveat")}
    except Exception:  # noqa: BLE001 -- the polish is cosmetic; never fail an answer for it
        pass
    paras = text.split("\n\n")
    out = []
    for i, para in enumerate(paras):
        p_ = para
        if i > 0 and not keep_exact and not p_.startswith("**"):
            p_ = _EXACT_AFTER_RE.sub(r"\1", p_)
        bare = p_.strip()
        if i > 0 and not bare.startswith(("Note:", "•", "**")) and (bare in known or bare.startswith(_NOTE_STARTS)
                                                                  or any(k and bare.startswith(k[:60]) for k in known)):
            p_ = "Note: " + bare
        out.append(p_)
    return "\n\n".join(out)


def convert_units(question: str, answer: str) -> str:
    """'in crores' / 'in lakhs' asked: add the conversion of the exact rupee amounts the answer states."""
    q = (question or "").lower()
    want = "crore" if re.search(r"\bcrores?\b|\bcr\b", q) else "lakh" if re.search(r"\blakhs?\b|\blacs?\b", q) else None
    if not want or not answer:
        return answer
    if re.search(rf"\d\s*{want}", answer):
        return answer
    nums = [to_float(x) for x in re.findall(r"(?<![\d.])\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|(?<![\d.,])\d{6,}(?:\.\d+)?", answer)]
    nums = [n for n in nums if n and n >= 1e5][:4]
    if not nums:
        return answer
    div = 1e7 if want == "crore" else 1e5
    conv = "; ".join(f"{fmt_number(n, 2)} = ₹{n / div:,.2f} {want}" for n in nums)
    return f"{answer}\n\nIn {want}s: {conv}."
