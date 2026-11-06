"""
Kannada questions (deterministic, no model, no translation service). Before this, a question typed in Kannada was only
let through the screen and left to the model. Now:

  * is_kannada(q)    the question contains Kannada script;
  * to_english(q)    rewrites it into the English the deterministic routes understand: Kannada digits -> 0-9, place
                     names (with any case suffix: "ಅರಕಲಗೂಡಿನಲ್ಲಿ" -> "Arkalgud"), DCB terms (ಬೇಡಿಕೆ -> demand, ಸಂಗ್ರಹಣಾ ದಕ್ಷತೆ ->
                     collection efficiency, ಬಾಕಿ -> arrears ...), years ("2024-25ರಲ್ಲಿ" -> "2024-25"), tariff words
                     (ಗೃಹ -> domestic, ಕೈಗಾರಿಕೆ -> industries ...) and question words (ಯಾವ ಉಪವಿಭಾಗ -> which sub-division,
                     "X ಎಂದರೇನು" -> "what is X"). Kannada words it does not know are dropped from the rewrite.
The pipeline answers the rewritten question; the answer says how the question was understood (in Kannada), and
knowledge-base answers come back in Kannada (app/knowledge.py).
"""
from __future__ import annotations

import re

_KN = r"ಀ-೿"
_ZW = re.compile(r"[​-‍﻿]")
_DIGITS = str.maketrans("೦೧೨೩೪೫೬೭೮೯", "0123456789")

# Place-name stems; any Kannada case suffix that follows is swallowed ("ಅರಕಲಗೂಡಿನ", "ಕೊಣನೂರಿನಲ್ಲಿ").
_PLACES = [
    ("ಹೊಳೆನರಸೀಪುರ ವಿಭಾಗ", "Holenarasipura division"), ("ಹೊಳೆನರಸಿಪುರ ವಿಭಾಗ", "Holenarasipura division"),
    ("ಹೊಳೆನರಸೀಪುರ", "Holenarasipura"), ("ಹೊಳೆನರಸಿಪುರ", "Holenarasipura"), ("ಅರಕಲಗೂಡ", "Arkalgud"),
    ("ಅರಕಲಗೋಡ", "Arkalgud"), ("ಹಂಗರಹಳ್ಳಿ", "Hangarahalli"), ("ಹಂಗರಹಳ್ಳ", "Hangarahalli"), ("ರಾಮನಾಥಪುರ", "Ramanathapura"),
    ("ಕೊಣನೂರ", "Konanur"), ("ಕೊಣನೂರು", "Konanur"), ("ಸಾಲಿಗ್ರಾಮ", "Saligrama"), ("ಮೈಸೂರ", "Mysuru"),
    # the English names written phonetically in Kannada script ("ಆರ್ಕಲ್‌ಗುಡ್")
    ("ಆರ್ಕಲ್ಗುಡ", "Arkalgud"), ("ಅರ್ಕಲ್ಗುಡ", "Arkalgud"), ("ಆರ್ಕಲ್ ಗುಡ", "Arkalgud"), ("ಕೋಣನೂರ", "Konanur"),
    ("ಕೊನನೂರ", "Konanur"), ("ರಾಮನಾಥ್ಪುರ", "Ramanathapura"), ("ಹೊಳೆನರಸಿಪುರ್", "Holenarasipura"),
]

# Terms, longest first (a phrase is consumed before its parts). Values are what app.router / the registry understand.
_TERMS = [
    ("ಎಫ್ ?ಪಿ ?ಪಿ ?ಸಿ ?ಎ", "fppca"), ("ಎಂ ?ಎನ್ ?ಆರ್", "mnr"), ("ಮೀಟರ್ ದಾಖಲಿಸದ", "meter not recording"),
    ("ಅಂದಾಜು ಬಳಕೆ", "assessed consumption"), ("ಪ್ರತಿ ಗ್ರಾಹಕ", "per consumer"),
    ("ಸಂಗ್ರಹಣಾ ದಕ್ಷತೆ", "collection efficiency"), ("ಸಂಗ್ರಹಣೆ ದಕ್ಷತೆ", "collection efficiency"),
    ("ವಸೂಲಾತಿ ದಕ್ಷತೆ", "collection efficiency"), ("ವಸೂಲಿ ದಕ್ಷತೆ", "collection efficiency"),
    ("ಸಂಗ್ರಹ ದಕ್ಷತೆ", "collection efficiency"), ("ಆರಂಭಿಕ ಬಾಕಿ", "opening balance"), ("ಅಂತಿಮ ಬಾಕಿ", "closing balance"),
    ("ಮುಕ್ತಾಯದ ಬಾಕಿ", "closing balance"), ("ಸಂಪರ್ಕಿತ ಹೊರೆ", "connected load"), ("ವಿದ್ಯುತ್ ಬಳಕೆ", "consumption"),
    ("ಚಾಲ್ತಿ ಸ್ಥಾವರ", "live installations"), ("ಚಾಲ್ತಿಯಲ್ಲಿರುವ ಸ್ಥಾವರ", "live installations"),
    ("ನೀರಾವರಿ ಪಂಪ್ ?ಸೆಟ್", "ip sets"), ("ಪಂಪ್ ?ಸೆಟ್", "ip sets"), ("ಐಪಿ ?ಸೆಟ್", "ip sets"),
    ("ಬೀದಿ ದೀಪ", "street lights"), ("ಕುಡಿಯುವ ನೀರು", "water supply"), ("ನೀರು ಸರಬರಾಜು", "water supply"),
    ("ಏತ ನೀರಾವರಿ", "lift irrigation"), ("ಉಚಿತ ದೀಪ", "free lighting"), ("ಮೀಟರ್ ರಹಿತ", "unmetered"),
    ("ಇವಿ ಚಾರ್ಜಿಂಗ್", "ev charging"), ("ವಿದ್ಯುತ್ ವಾಹನ", "ev charging"),
    ("ಭಾಗ್ಯಜ್ಯೋತಿ", "bhagyajyothi"), ("ಭಾಗ್ಯ ಜ್ಯೋತಿ", "bhagyajyothi"), ("ಕುಟೀರಜ್ಯೋತಿ", "kutirajyothi"),
    ("ಗೃಹಜ್ಯೋತಿ", "gruha jyothi"), ("ಗೃಹ ಜ್ಯೋತಿ", "gruha jyothi"),
    ("ಸಂಗ್ರಹಣೆ", "collection"), ("ಸಂಗ್ರಹ", "collection"), ("ವಸೂಲಾತಿ", "collection"), ("ವಸೂಲಿ", "collection"),
    ("ಬೇಡಿಕೆ", "demand"), ("ಬಿಲ್ಲಿಂಗ್", "demand"), ("ಬಾಕಿ", "arrears"), ("ಸ್ಥಾವರ", "installations"),
    ("ಸಂಪರ್ಕ", "connections"), ("ಗ್ರಾಹಕ", "consumers"), ("ಬಳಕೆ", "consumption"), ("ಯೂನಿಟ್", "units"),
    ("ಎಲ್ ?ಟಿ", "LT"), ("ಎಚ್ ?ಟಿ", "HT"), ("ಹೆಚ್ ?ಟಿ", "HT"),
    ("ಗೃಹ", "domestic"), ("ಮನೆ", "domestic"), ("ಕೈಗಾರಿಕೆ", "industries"), ("ವಾಣಿಜ್ಯ", "commercial"),
    ("ತಾತ್ಕಾಲಿಕ", "temporary"), ("ಕೃಷಿ", "agriculture"), ("ರೈತ", "farmers"),
    ("ಸ್ಲ್ಯಾಬ್", "slab"), ("ಸುಂಕ ವರ್ಗ", "tariff category"), ("ಸುಂಕ", "tariff"),
    ("ಯಾವ ಉಪ ?-?ವಿಭಾಗ", "which sub-division"), ("ಉಪ ?-?ವಿಭಾಗ", "sub-division"), ("ಶಾಖೆ", "section"),
    ("ಇಡೀ ವಿಭಾಗ", "whole division"), ("ವಿಭಾಗ", "division"),
    ("ಈ ವರ್ಷ", "this year"), ("ಪ್ರಸ್ತುತ ವರ್ಷ", "this year"), ("ಕಳೆದ ವರ್ಷ", "last year"), ("ಹಿಂದಿನ ವರ್ಷ", "last year"),
    ("ವರ್ಷವಾರು", "year-wise"), ("ಪ್ರತಿ ವರ್ಷ", "each year"), ("ಪ್ರವೃತ್ತಿ", "trend"), ("ಬೆಳವಣಿಗೆ", "growth"),
    ("ಎಲ್ಲಾ ವರ್ಷ", "all years"), ("ಹೋಲಿಕೆ", "compare"), ("ಹೋಲಿಸ", "compare"),
    ("ಅತಿ ಹೆಚ್ಚು", "highest"), ("ಅತ್ಯಧಿಕ", "highest"), ("ಗರಿಷ್ಠ", "highest"), ("ಹೆಚ್ಚು", "highest"),
    ("ಅತಿ ಕಡಿಮೆ", "lowest"), ("ಕನಿಷ್ಠ", "lowest"), ("ಕಡಿಮೆ", "lowest"),
    ("ಸಾರಾಂಶ", "summary"), ("ಒಟ್ಟು", "total"), ("ಎಷ್ಟು", "how much"), ("ಮತ್ತು", "and"),
    ("ಸೆಸ್ಕ್", "CESC"), ("ಸೆಸ್ಕಾಂ", "CESC"), ("ಚೆಸ್ಕಾಂ", "CESC"), ("ಚಾಮುಂಡೇಶ್ವರಿ ವಿದ್ಯುತ್ ಸರಬರಾಜು ನಿಗಮ", "CESC"),
    ("ಕೆಇಆರ್ ?ಸಿ", "KERC"), ("ಡಿಸಿಬಿ", "DCB"), ("ಎಸ್ಕಾಂ", "escom"),
]
_WHAT_IS_RE = re.compile(r"^(?P<x>.+?)\s*(ಎಂದರೇನು|ಅಂದರೆ ಏನು|ಎಂದರೆ ಏನು|ಏನು)\s*\??\s*$")
_ABOUT_RE = re.compile(r"(ಬಗ್ಗೆ (ಹೇಳಿ|ತಿಳಿಸಿ|ಮಾಹಿತಿ))")


def is_kannada(text: str) -> bool:
    return bool(re.search(f"[{_KN}]", text or ""))


def _sub(pattern: str, repl: str, text: str) -> str:
    # a term followed by any Kannada suffix ("ಬೇಡಿಕೆಯ", "ಸ್ಥಾವರಗಳು") is the term
    return re.sub(pattern + f"[{_KN}]*", f" {repl} ", text)


def to_english(question: str) -> str:
    q = _ZW.sub("", question or "").translate(_DIGITS)
    q = re.sub(r"\s+", " ", q).strip()
    what_is = _WHAT_IS_RE.match(q)
    about = bool(_ABOUT_RE.search(q))
    if what_is and not re.search(r"ಎಷ್ಟು|[0-9]", q):
        q = what_is.group("x")
    for kn, en in _PLACES:
        q = _sub(re.escape(kn), en, q)
    for kn, en in _TERMS:
        q = _sub(kn, en, q)
    q = re.sub(f"([0-9])[{_KN}]+", r"\1 ", q)          # 2024-25ರಲ್ಲಿ -> 2024-25
    q = re.sub(f"[{_KN}]+", " ", q)                      # unknown Kannada words
    q = re.sub(r"\s+", " ", q).strip(" ?.,")
    if what_is and not re.search(r"\b(how much|[0-9])", q):
        q = f"what is {q}"
    elif about:
        q = f"tell me about {q}"
    return q + "?"


def understood_note(english: str) -> str:
    """The line shown above an answer to a Kannada question: how it was understood."""
    return f"ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಹೀಗೆ ಅರ್ಥೈಸಲಾಗಿದೆ: “{english}”"
