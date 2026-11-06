"""
On-screen view of a report workbook ("View report"), rendered from the very openpyxl Workbook that the download
writes, so what is seen is what is downloaded. Ported from the KPCL POC (read-only reference) unchanged in logic;
used by GET /api/mis-report/view (dashboard) and GET /api/answer-report/{id}/view (chat).

workbook_view(wb) -> {"sheets": [{"name", "tab_color", "html", "charts", "width", "height"}]}

  * cells: values shown through their Excel number formats (the formats this app writes: #,##0 / 0.00 / 0.0% /
    signed "+0.0"%"" and " pts" sections / literal suffixes), fills, fonts, alignment, wrapping, borders, merged cells,
    column widths, row heights and hidden columns;
  * conditional formats: the simple rules this app writes (AND(ISNUMBER(A5),A5>=100) and the like) are evaluated
    per cell so the colours match Excel;
  * the header row above the frozen panes stays in view while scrolling; Excel-style column letters and row numbers;
  * images (the emblem) at their anchor cell;
  * charts: their series are read from the cells they reference and returned as data for Chart.js, placed at the
    chart's anchor cell with the chart's size.

Every cell value is HTML-escaped; nothing from the workbook is inserted as markup.
"""
from __future__ import annotations

import html
import re
from decimal import ROUND_HALF_UP, Decimal

from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

_PX_PER_PT = 96 / 72
_DEFAULT_COL_W = 8.43
_DEFAULT_ROW_PT = 15.0
_HDR_W, _HDR_H = 40, 22       # row-number column and column-letter row


def _col_px(width_chars: float | None) -> int:
    return int(round((width_chars or _DEFAULT_COL_W) * 7 + 5))


def _row_px(height_pt: float | None) -> int:
    return int(round((height_pt or _DEFAULT_ROW_PT) * _PX_PER_PT))


def _rgb(color) -> str | None:
    """'#RRGGBB' from an openpyxl Color, or None (theme / indexed / unset colours fall back to the page default)."""
    try:
        v = color.rgb if color is not None else None
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(v, str) or len(v) < 6 or not re.fullmatch(r"[0-9A-Fa-f]{6,8}", v):
        return None
    return "#" + v[-6:].upper()


# ------------------------------------------------------------------------------------------------ number formats
def _split_sections(fmt: str) -> list[str]:
    out, cur, q = [], "", False
    for ch in fmt:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def _render_section(value: float, section: str) -> str:
    percent = False
    parts: list[tuple[str, str]] = []    # (kind, text) in order: 'lit' or 'num'
    i = 0
    while i < len(section):
        ch = section[i]
        if ch == '"':
            j = section.index('"', i + 1) if '"' in section[i + 1:] else len(section)
            parts.append(("lit", section[i + 1:j]))
            i = j + 1
            continue
        if ch == "\\" and i + 1 < len(section):
            parts.append(("lit", section[i + 1]))
            i += 2
            continue
        if ch in "0#,.":
            if parts and parts[-1][0] == "num":
                parts[-1] = ("num", parts[-1][1] + ch)
            else:
                parts.append(("num", ch))
            i += 1
            continue
        if ch == "%":
            percent = True
        if ch not in "_*":
            parts.append(("lit", ch))
        i += 1
    if percent:
        value *= 100
    out = []
    done = False
    for kind, text in parts:
        if kind == "lit":
            out.append(text)
            continue
        if done:
            continue
        done = True
        grouping = "," in text.split(".")[0]
        dec = text.split(".")[1] if "." in text else ""
        fixed, optional = dec.count("0"), dec.count("#")
        places = fixed + optional
        # Excel rounds the number as written (8671.845 -> 8,671.85), not its binary double (8671.8449999...), and it
        # keeps 15 significant digits: 29131030.054999996 is stored and shown as 29131030.055 -> 29,131,030.06.
        q = Decimal(f"{float(value):.15g}").quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        s = f"{q:,.{places}f}" if grouping else f"{q:.{places}f}"
        if s.startswith("-") and float(q) == 0:
            s = s[1:]
        if optional and "." in s:
            whole, frac = s.split(".")
            frac = frac.rstrip("0") if len(frac.rstrip("0")) >= fixed else frac[:fixed]
            frac = frac.ljust(fixed, "0")
            s = whole + ("." + frac if frac else "")
        out.append(s)
    return "".join(out)


def format_value(value, number_format: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if not isinstance(value, (int, float)):
        return str(value)
    fmt = (number_format or "General").strip()
    if fmt == "General":
        if isinstance(value, int) or float(value).is_integer():
            return f"{int(value)}"
        return f"{value:.10g}"
    secs = _split_sections(fmt)
    if value < 0 and len(secs) >= 2:
        return _render_section(abs(value), secs[1])
    if value == 0 and len(secs) >= 3:
        return _render_section(0.0, secs[2])
    return _render_section(value, secs[0])    # one section: the minus sign comes with the number itself


# ------------------------------------------------------------------------------------------------ conditional formats
_COND_RE = re.compile(r"^\s*\$?([A-Z]+)\$?(\d+)\s*(>=|<=|<>|=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")


def _eval_rule(formula: str, value, first_col: int, first_row: int, col: int, row: int) -> bool:
    """AND(ISNUMBER(D6),D6>0,D6<100) style rules, evaluated with the cell's own offset from the range's first cell."""
    f = formula.strip().lstrip("=")
    m = re.fullmatch(r"AND\((.*)\)", f, re.S)
    parts = [p.strip() for p in (m.group(1) if m else f).split(",")]
    for p in parts:
        if p.upper().startswith("ISNUMBER("):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            continue
        c = _COND_RE.match(p)
        if not c or not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        op, num = c.group(3), float(c.group(4))
        v = float(value)
        ok = {">": v > num, "<": v < num, ">=": v >= num, "<=": v <= num, "=": v == num, "<>": v != num}[op]
        if not ok:
            return False
    return True


def _conditional_styles(ws) -> dict[tuple[int, int], dict]:
    out: dict[tuple[int, int], dict] = {}
    try:
        items = list(ws.conditional_formatting)
    except Exception:  # noqa: BLE001
        return out
    for cf in items:
        rules = cf.rules
        for rng in str(cf.sqref).split():
            min_col, min_row, max_col, max_row = range_boundaries(rng)
            for rule in rules:
                if not rule.formula:
                    continue
                dxf = rule.dxf
                style = {}
                if dxf is not None and dxf.fill is not None:
                    bg = _rgb(dxf.fill.fgColor) or _rgb(dxf.fill.bgColor)
                    if bg:
                        style["background"] = bg
                if dxf is not None and dxf.font is not None:
                    fc = _rgb(dxf.font.color)
                    if fc:
                        style["color"] = fc
                    if dxf.font.b:
                        style["font-weight"] = "700"
                for r in range(min_row, max_row + 1):
                    for c in range(min_col, max_col + 1):
                        if (r, c) in out:
                            continue    # the first rule that applies wins, as in Excel with stop-if-true ranges we write
                        v = ws.cell(r, c).value
                        if _eval_rule(rule.formula[0], v, min_col, min_row, c, r):
                            out[(r, c)] = style
    return out


# ------------------------------------------------------------------------------------------------ charts
def _ref_values(wb, ref: str) -> list:
    m = re.match(r"^'?(.*?)'?!(.+)$", ref)
    if not m:
        return []
    sheet, rng = m.group(1).replace("''", "'"), m.group(2).replace("$", "")
    if sheet not in wb.sheetnames:
        return []
    ws = wb[sheet]
    min_col, min_row, max_col, max_row = range_boundaries(rng)
    return [ws.cell(r, c).value for r in range(min_row, max_row + 1) for c in range(min_col, max_col + 1)]


def _title_text(t) -> str:
    try:
        return "".join(r.t for p in t.tx.rich.p for r in (p.r or []))
    except Exception:  # noqa: BLE001
        return ""


def _srgb(choice) -> str | None:
    v = getattr(choice, "srgbClr", None) if choice is not None else None
    v = getattr(v, "val", v)
    return "#" + str(v)[-6:].upper() if isinstance(v, str) and re.fullmatch(r"[0-9A-Fa-f]{6,8}", v) else None


def _series_colour(s) -> tuple[str | None, bool]:
    gp = s.graphicalProperties
    if gp is None:
        return None, False
    line = gp.line
    colour = _srgb(gp.solidFill) or (_srgb(line.solidFill) if line is not None else None)
    dashed = bool(line is not None and line.prstDash and line.prstDash != "solid")
    return colour, dashed


def _chart_spec(wb, ch) -> dict | None:
    datasets, labels = [], None
    for sub in getattr(ch, "_charts", [ch]):
        kind = "line" if sub.tagname == "lineChart" else "bar"
        for s in sub.series:
            ref = s.val.numRef.f if s.val is not None and s.val.numRef is not None else None
            if not ref:
                continue
            vals = [v if isinstance(v, (int, float)) and not isinstance(v, bool) else None for v in _ref_values(wb, ref)]
            name = ""
            if s.tx is not None and s.tx.strRef is not None:
                got = _ref_values(wb, s.tx.strRef.f)
                name = str(got[0]) if got else ""
            if labels is None and s.cat is not None:
                cref = (s.cat.strRef or s.cat.numRef)
                if cref is not None:
                    labels = ["" if v is None else str(v) for v in _ref_values(wb, cref.f)]
            colour, dashed = _series_colour(s)
            datasets.append({"type": kind, "label": name, "data": vals, "color": colour, "dashed": dashed})
    if not datasets:
        return None
    n = max(len(d["data"]) for d in datasets)
    try:
        y_title = _title_text(ch.y_axis.title) if ch.y_axis.title is not None else ""
    except Exception:  # noqa: BLE001
        y_title = ""
    return {"title": _title_text(ch.title) if ch.title is not None else "", "labels": labels or [str(i + 1) for i in range(n)],
            "datasets": datasets, "y_title": y_title, "legend": ch.legend is not None,
            "width": int(round((ch.width or 15) * 37.8)), "height": int(round((ch.height or 7.5) * 37.8))}


def _anchor_cell(obj) -> tuple[int, int]:
    a = obj.anchor
    if isinstance(a, str):
        col, row = coordinate_from_string(a)
        return column_index_from_string(col), row
    try:
        return a._from.col + 1, a._from.row + 1
    except Exception:  # noqa: BLE001
        return 1, 1


# ------------------------------------------------------------------------------------------------ sheets
def _cell_css(cell, is_num: bool) -> str:
    css = []
    f = cell.font
    if f is not None:
        if f.b:
            css.append("font-weight:700")
        if f.i:
            css.append("font-style:italic")
        if f.sz:
            css.append(f"font-size:{float(f.sz) * _PX_PER_PT:.1f}px")
        c = _rgb(f.color)
        if c:
            css.append(f"color:{c}")
    fill = cell.fill
    if fill is not None and fill.fill_type == "solid":
        bg = _rgb(fill.fgColor)
        if bg:
            css.append(f"background:{bg}")
    al = cell.alignment
    h = al.horizontal if al is not None else None
    css.append(f"text-align:{ {'center': 'center', 'right': 'right', 'left': 'left', 'centerContinuous': 'center'}.get(h, 'right' if is_num else 'left') }")
    v = al.vertical if al is not None else None
    css.append(f"vertical-align:{ {'top': 'top', 'center': 'middle'}.get(v, 'bottom') }")
    if al is not None and al.wrap_text:
        css.append("white-space:normal;overflow-wrap:anywhere")
    if al is not None and al.indent:
        css.append(f"padding-left:{4 + int(al.indent) * 9}px")
    b = cell.border
    if b is not None:
        for side in ("left", "right", "top", "bottom"):
            sd = getattr(b, side)
            if sd is not None and sd.style:
                w = 2 if sd.style in ("medium", "thick", "double") else 1
                col = _rgb(sd.color) or "#CFD5E2"
                css.append(f"border-{side}:{w}px solid {col}")
    return ";".join(css)


def sheet_view(wb, ws) -> dict:
    max_row, max_col = ws.max_row, ws.max_column
    for img in getattr(ws, "_images", []):
        c, r = _anchor_cell(img)
        max_col, max_row = max(max_col, c), max(max_row, r)
    merged: dict[tuple[int, int], tuple[int, int]] = {}
    covered: set[tuple[int, int]] = set()
    for mr in ws.merged_cells.ranges:
        merged[(mr.min_row, mr.min_col)] = (mr.max_row - mr.min_row + 1, mr.max_col - mr.min_col + 1)
        for r in range(mr.min_row, mr.max_row + 1):
            for c in range(mr.min_col, mr.max_col + 1):
                if (r, c) != (mr.min_row, mr.min_col):
                    covered.add((r, c))
    hidden = {c for c in range(1, max_col + 1) if ws.column_dimensions[get_column_letter(c)].hidden}
    widths = {c: (0 if c in hidden else _col_px(ws.column_dimensions[get_column_letter(c)].width)) for c in range(1, max_col + 1)}
    heights = {r: _row_px(ws.row_dimensions[r].height) for r in range(1, max_row + 1)}
    cond = _conditional_styles(ws)
    freeze_row = 0
    if ws.freeze_panes:
        _, fr = coordinate_from_string(ws.freeze_panes)
        freeze_row = fr - 1          # the header row: the last row above the frozen pane
    grid = ws.sheet_view.showGridLines is not False

    # x positions also for columns past the last filled one, where a chart may be anchored (e.g. to the right of a table)
    far_col = max([max_col] + [_anchor_cell(ch)[0] for ch in getattr(ws, "_charts", [])]) + 1
    left = {1: _HDR_W}
    for c in range(2, far_col + 1):
        w = widths.get(c - 1)
        left[c] = left[c - 1] + (w if w is not None else _col_px(ws.column_dimensions[get_column_letter(c - 1)].width))
    top = {1: _HDR_H}
    for r in range(2, max_row + 2):
        top[r] = top[r - 1] + heights.get(r - 1, _row_px(None))

    # an explicit table width makes the browser use the column widths below (fixed layout), exactly as Excel does
    table_w = _HDR_W + sum(w for c, w in widths.items() if c not in hidden)
    out = [f'<table class="xv-grid{"" if grid else " nogrid"}" style="width:{table_w}px"><colgroup><col style="width:{_HDR_W}px">']
    out += [f'<col style="width:{widths[c]}px">' for c in range(1, max_col + 1) if c not in hidden]
    out.append('</colgroup><thead><tr><th class="xv-corner"></th>')
    out += [f'<th class="xv-col">{get_column_letter(c)}</th>' for c in range(1, max_col + 1) if c not in hidden]
    out.append("</tr></thead><tbody>")
    for r in range(1, max_row + 1):
        sticky = ' class="xv-sticky"' if freeze_row and r == freeze_row else ""
        out.append(f'<tr style="height:{heights[r]}px"{sticky}><th class="xv-row">{r}</th>')
        for c in range(1, max_col + 1):
            if c in hidden or (r, c) in covered:
                continue
            cell = ws.cell(r, c)
            v = cell.value
            is_num = isinstance(v, (int, float)) and not isinstance(v, bool)
            text = html.escape(format_value(v, cell.number_format))
            span = merged.get((r, c))
            attrs = ""
            if span:
                rs, cs = span
                cs_vis = sum(1 for cc in range(c, c + cs) if cc not in hidden)
                attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs_vis}"' if cs_vis > 1 else "")
            css = _cell_css(cell, is_num)
            # like Excel, unwrapped text runs on over the empty cells to its right instead of being cut off
            if isinstance(v, str) and not span and "white-space:normal" not in css and "text-align:left" in css \
                    and c < max_col and (r, c + 1) not in covered and ws.cell(r, c + 1).value is None:
                css += ";overflow:visible;position:relative;z-index:1"
            if (r, c) in cond:
                css += ";" + ";".join(f"{k}:{val}" for k, val in cond[(r, c)].items())
            out.append(f'<td{attrs} style="{css}">{text}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")

    images = []
    for img in getattr(ws, "_images", []):
        c, r = _anchor_cell(img)
        images.append(f'<img class="xv-img" src="/static/cescom_official_logo.png" alt="" '
                      f'style="left:{left.get(c, _HDR_W) + 2}px;top:{top.get(r, _HDR_H) + 2}px;width:{int(img.width)}px;height:{int(img.height)}px">')
    charts = []
    for ch in getattr(ws, "_charts", []):
        spec = _chart_spec(wb, ch)
        if spec:
            c, r = _anchor_cell(ch)
            spec["left"], spec["top"] = left.get(c, _HDR_W), top.get(r, _HDR_H)
            charts.append(spec)
    width = left[max_col + 1] if max_col + 1 in left else table_w
    height = top[max_row + 1]
    for spec in charts:
        width = max(width, spec["left"] + spec["width"] + 24)
        height = max(height, spec["top"] + spec["height"] + 24)
    tab = _rgb(ws.sheet_properties.tabColor) if ws.sheet_properties.tabColor is not None else None
    footer = " · ".join(t for t in (ws.oddFooter.left.text, ws.oddFooter.center.text) if t)
    return {"name": ws.title, "tab_color": tab, "html": "".join(out) + "".join(images), "charts": charts,
            "width": width, "height": height, "footer": footer}


def workbook_view(wb) -> dict:
    return {"sheets": [sheet_view(wb, ws) for ws in wb.worksheets if ws.sheet_state == "visible"]}
