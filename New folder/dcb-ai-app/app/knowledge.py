"""
General-knowledge answers from DCB_Mart/metadata/knowledge_base.yaml (pattern ported from the KPCL POC): what CESC is,
the area it serves, who regulates it, the Holenarasipura Division and its sub-divisions, the subsidy schemes behind some
tariff categories (Bhagyajyothi, Gruha Jyothi, IP sets), and how the DCB terms are meant. English and Kannada.

Two entry points, both deterministic (no LLM):
  * primary(question, kannada)  a general question with no data intent ("What is CESC?", "Who regulates CESC?",
                                "Tell me about Konanur section", "What is Gruha Jyothi?"). A question that names a
                                measure, a period, a ranking or a trend is left to the data routes.
  * fallback(question, kannada) used when the data path cannot answer (model offline / declined): any clear match,
                                unless the question asks for data.
Answers read like any other answer: no sources or "from the knowledge base" notes. Figures never come from here.
"""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from app import router
from app.config import MART_DIR

KNOWLEDGE_PATH = MART_DIR / "metadata" / "knowledge_base.yaml"

# Data intent: these questions belong to the data routes, not the knowledge base.
_DATA_INTENT_RE = re.compile(
    r"\b(how many|number of|count|how much|percentage|percent|share of|ratio|average|rank\w*|highest|lowest|top|bottom|"
    r"best|worst|trend|compare|versus|vs\.?|figures?|numbers?|statistics|list|show me|give me)\b", re.I)
_GENERAL_RE = re.compile(
    r"\b(what is|what's|whats|what are|what does|who is|who are|who owns|who runs|who regulates|tell me about|explain|"
    r"describe|about|when was|founded|formed|history|purpose|why|role|meaning|mean|stand for|define|overview|where is|"
    r"what sub-?divisions|which sections|sub-?divisions (?:does|do)|"
    r"located|headquarters|head office|owner|owned|ownership|regulat\w*|laws?|rules?|act|districts?|area|cover\w*|"
    r"serve\w*|how is|how are|how does|scheme)\b", re.I)


@lru_cache(maxsize=1)
def _kb() -> dict:
    return yaml.safe_load(KNOWLEDGE_PATH.read_text(encoding="utf-8"))


def _singular(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")) else w


def _norm(s: str) -> str:
    """Lower-case words, plural-blind ("a DC installation" matches the alias "dc installations")."""
    words = re.sub(r"[^a-z0-9.]+", " ", str(s or "").lower()).split()
    return " " + " ".join(_singular(w) for w in words) + " "


def _text(block: dict, kannada: bool) -> str:
    return ((block.get("kn") if kannada else None) or block.get("en") or "").strip()


def _card(title: str, body: str) -> str:
    return f"**{title}**\n\n{body.strip()}"


# A place is explained only when the question asks what / where / why it is; "tell me about Arkalgud" or
# "Arkalgud overview" stays with the data routes (the key figures of that sub-division).
_PLACE_RE = re.compile(r"\b(what is|what's|whats|where is|explain|describe|why|clos\w*|history|part of|belong\w*|"
                       r"which sub-?divisions|what are the sub-?divisions|sub-?divisions of|made up of|consist\w*|"
                       r"what sub-?divisions|sub-?divisions (?:does|do|are there|in)|which sections|sections (?:are there|in))\b", re.I)


def _best_entry(q: str, places: bool) -> dict | None:
    """The place or entry whose alias matches the longest part of the question ("Konanur section" beats "section")."""
    best, best_len = None, 0
    kb = _kb()
    for e in (kb.get("places", []) if places else []) + kb.get("entries", []):
        for a in e["aliases"]:
            n = _norm(a)
            if n in q and len(n) > best_len:
                best, best_len = e, len(n)
    return best


def _org_answer(question: str, kannada: bool) -> str:
    org = _kb()["organisation"]
    facets = [(k, f) for k, f in org["facets"].items() if re.search(f["match"], question, re.I)]
    if not facets:
        extra = _text(org["facets"]["area"], kannada) if not kannada else ""
        return _card(org["name"], _text(org["summary"], kannada) + (f"\n\n{extra}" if extra else ""))
    parts = [_text(f, kannada) for _, f in facets]
    title = org["name"].split(" (")[0] + ": " + ", ".join(f["label"].lower() if i else f["label"] for i, (_, f) in enumerate(facets))
    return _card(title, "\n\n".join(parts))


def _match(question: str, kannada: bool) -> str | None:
    q = _norm(question)
    e = _best_entry(q, places=bool(_PLACE_RE.search(question)))
    if e is not None:
        return _card(e["name"], _text(e, kannada))
    org = _kb()["organisation"]
    if any(_norm(a) in q for a in org["aliases"]):
        return _org_answer(question, kannada)
    return None


def asks_for_data(question: str) -> bool:
    e = router.extract(question)
    if e.get("metrics") or e.get("fiscal_years") or e.get("out_of_range") or e.get("families") or e.get("slab_codes"):
        return True
    if set(e.get("intents", [])) & {"ranking", "trend", "comparison", "breakdown", "share", "change"}:
        return True
    return bool(_DATA_INTENT_RE.search(question or ""))


def _is_bare_name(question: str) -> bool:
    """The whole question is just a name the knowledge base knows ("Gruha Jyothi", "KERC?")."""
    q = _norm(question.rstrip("?. "))
    kb = _kb()
    names = [a for e in kb.get("places", []) + kb.get("entries", []) for a in e["aliases"]] + kb["organisation"]["aliases"]
    return any(q == _norm(a) for a in names)


def primary(question: str, kannada: bool = False) -> str | None:
    """A general question with no data intent, answered from the knowledge base."""
    q = (question or "").strip()
    if not q or asks_for_data(q):
        return None
    if not _GENERAL_RE.search(q) and not _is_bare_name(q):
        return None
    return _match(q, kannada)


_DEFINITION_RE = re.compile(r"^\s*(what is|what's|whats|what are|define|explain|meaning of|what does)\s+(the |an |a )?"
                            r"(?P<subject>.+?)\s*(mean|stand for)?\s*\??\s*$", re.I)


def is_definition(question: str) -> bool:
    """"What is collection efficiency?" -- a plain request to explain exactly one thing the knowledge base names (with
    nothing else: no place, year or measure qualifier), which gets the explanation rather than a figure."""
    m = _DEFINITION_RE.match(question or "")
    if not m:
        return False
    subject = _norm(m.group("subject"))
    subjects = {subject, _norm(re.sub(r"\s+(installations?|connections?|consumers?|meters?|category|scheme)$", "", subject))}
    kb = _kb()
    names = [a for e in kb.get("entries", []) for a in e["aliases"]] + kb["organisation"]["aliases"]
    return any(s == _norm(n) for n in names for s in subjects)


def define(question: str, kannada: bool = False) -> str | None:
    """A plain "what is X" for a term the glossary also knows: the knowledge-base explanation where there is one, even
    when X is also a measure name ("What is DC/MNR?")."""
    q = (question or "").strip()
    return _match(q, kannada) if q else None


def fallback(question: str, kannada: bool = False) -> str | None:
    """When the data path cannot answer: any clear match, never for a question that asks for data."""
    q = (question or "").strip()
    if not q or asks_for_data(q):
        return None
    return _match(q, kannada)
