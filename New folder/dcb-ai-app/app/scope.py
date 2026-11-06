"""
Where a deterministic figure comes from: one Scope = one mart table plus fixed filters with bound parameters.
Values placed in the SQL are only ever validated entity values (sub-division names from the mart's own list,
family codes, fiscal years), passed as parameters -- never user text.

  division            mart_fact_division_year                        the whole division (printed grand total)
  subdivision         mart_fact_subdivision_year      subdivision_name
  family              mart_fact_family_division_year  family_code    a tariff family, whole division
  family_sub          mart_fact_family_subdivision_year              a tariff family in one sub-division
  voltage / voltage_sub  the family tables filtered on voltage_class (LT / HT)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import mart_layer

T_DIV = "mart_fact_division_year"
T_SUB = "mart_fact_subdivision_year"
T_FAM_DIV = "mart_fact_family_division_year"
T_FAM_SUB = "mart_fact_family_subdivision_year"
T_SLAB_DIV = "mart_fact_slab_division_year"
T_SLAB_SUB = "mart_fact_slab_subdivision_year"


@dataclass
class Scope:
    kind: str
    table: str
    where: list[str] = field(default_factory=list)
    params: list = field(default_factory=list)
    label: str = "Holenarasipura Division"
    subdivision: str | None = None
    family: str | None = None
    voltage: str | None = None

    def where_sql(self, extra: list[str] | None = None) -> str:
        conds = self.where + (extra or [])
        return " AND ".join(conds) if conds else "1 = 1"

    @property
    def families_used(self) -> list[str]:
        return [self.family] if self.family else []


def family_phrase(code: str) -> str:
    if code == "MISC":
        return "miscellaneous revenue"
    return code


def build(subdivision: str | None = None, family: str | None = None, voltage: str | None = None) -> Scope:
    if family:
        lab = family_phrase(family)
        if subdivision:
            return Scope("family_sub", T_FAM_SUB, ["subdivision_name = ?", "family_code = ?"], [subdivision, family],
                         f"{lab} in {subdivision}", subdivision, family)
        return Scope("family", T_FAM_DIV, ["family_code = ?"], [family], f"{lab} (whole division)", None, family)
    if voltage in ("LT", "HT"):
        if subdivision:
            return Scope("voltage_sub", T_FAM_SUB, ["subdivision_name = ?", "voltage_class = ?"], [subdivision, voltage],
                         f"{voltage} categories in {subdivision}", subdivision, voltage=voltage)
        return Scope("voltage", T_FAM_DIV, ["voltage_class = ?"], [voltage], f"{voltage} categories (whole division)",
                     voltage=voltage)
    if subdivision:
        kind = "section" if subdivision == "Konanur" else "sub-division"
        return Scope("subdivision", T_SUB, ["subdivision_name = ?"], [subdivision], f"{subdivision} {kind}", subdivision)
    return Scope("division", T_DIV)


def from_entities(e: dict) -> Scope | None:
    """The single scope a question names, or None when it names several places or families (a comparison)."""
    subs = [s for s in e.get("subdivisions", []) if s != "Division_Office"]
    fams = e.get("families", [])
    if len(subs) > 1 or len(fams) > 1 or e.get("slab_codes") or e.get("voltage") == "both":
        return None
    return build(subs[0] if subs else None, fams[0] if fams else None, e.get("voltage"))


def availability(scope: Scope) -> tuple[str | None, str | None]:
    """(first, last) fiscal year the scope has data for, from the mart's entity lists."""
    if scope.subdivision:
        s = next((x for x in mart_layer.subdivisions() if x["name"] == scope.subdivision), None)
        first, last = (s["first_fy"], s["last_fy"]) if s else (None, None)
    else:
        first, last = mart_layer.fiscal_years()[0], mart_layer.latest_fy()
    if scope.family:
        f = mart_layer.families().get(scope.family)
        if f:
            first = max(first or f["first_fy"], f["first_fy"])
            last = min(last or f["last_fy"], f["last_fy"])
    return first, last
