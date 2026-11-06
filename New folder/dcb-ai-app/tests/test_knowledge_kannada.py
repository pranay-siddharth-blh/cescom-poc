"""Knowledge base (DCB_Mart/metadata/knowledge_base.yaml, app/knowledge.py) and Kannada questions (app/kannada.py):
deterministic, no model; data questions never answered from the knowledge base."""
from unittest.mock import AsyncMock, patch

import pytest

from app import kannada, knowledge, pipeline
from tests.conftest import run


def ask(db, router_index, q):
    with patch("app.pipeline.generate_query_plan", AsyncMock(side_effect=AssertionError("model called"))):
        return run(pipeline.answer_question(q, db, router_index, 200, 10))


@pytest.mark.parametrize("q,needle", [
    ("What is CESC?", "five electricity supply companies"), ("Who regulates CESC?", "KERC"),
    ("Which districts does CESC cover?", "Kodagu"), ("What is Gruha Jyothi?", "200 units"),
    ("What are IP sets?", "LT-4"), ("What is DC/MNR?", "meter not recording"), ("What is KERC?", "tariff orders"),
    ("Why did Saligrama close?", "2017-18"), ("What is collection efficiency?", "collection divided by demand"),
    ("What are the sub-divisions of Holenarasipura division?", "Konanur"),
])
def test_general_questions_come_from_the_knowledge_base(db, router_index, q, needle):
    r = ask(db, router_index, q)
    assert r.route == "knowledge" and needle in r.answer, (q, r.route, r.answer[:200])


@pytest.mark.parametrize("q,route", [
    ("tell me about Arkalgud", "report:summary"), ("Konanur arrears", "metric:closing_balance"),
    ("CESC demand in 2024-25", "metric:demand"), ("What is LT-4?", "tariff:explain:lt4"),
    ("What is the collection efficiency in 2024-25?", "metric:collection_efficiency"),
])
def test_data_questions_stay_with_the_data(db, router_index, q, route):
    assert ask(db, router_index, q).route == route


def test_glossary_entries_are_not_shadowed_by_partial_aliases(db, router_index):
    # a tariff code is never captured by a knowledge-base alias ("lt and ht"); since the CESC tariff booklet it is answered
    # from the booklet (app/tariff_booklet.py), which superseded the locked glossary's tariff-code text
    assert ask(db, router_index, "What is HT-2?").route == "tariff:explain:ht2"


@pytest.mark.parametrize("kn,en", [
    ("೨೦೨೪-೨೫ರಲ್ಲಿ ಅರಕಲಗೂಡಿನ ಸಂಗ್ರಹಣಾ ದಕ್ಷತೆ ಎಷ್ಟು?", "2024-25 Arkalgud collection efficiency how much?"),
    ("ಕೊಣನೂರಿನ ಬಾಕಿ ಎಷ್ಟು?", "Konanur arrears how much?"),
    ("ಯಾವ ಉಪವಿಭಾಗದಲ್ಲಿ ಅತಿ ಹೆಚ್ಚು ಬಾಕಿ ಇದೆ?", "which sub-division highest arrears?"),
    ("ಸೆಸ್ಕ್ ಎಂದರೇನು?", "what is CESC?"),
])
def test_kannada_rewrite(kn, en):
    assert kannada.is_kannada(kn) and kannada.to_english(kn) == en


def test_kannada_data_question_gets_the_same_figure(db, router_index):
    en = ask(db, router_index, "What was the collection efficiency in Arkalgud in 2024-25?")
    kn = ask(db, router_index, "೨೦೨೪-೨೫ರಲ್ಲಿ ಅರಕಲಗೂಡಿನ ಸಂಗ್ರಹಣಾ ದಕ್ಷತೆ ಎಷ್ಟು?")
    assert kn.route == en.route and "93.62%" in kn.answer and kn.answer.startswith("ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು")
    assert kn.question.startswith("೨೦೨೪")          # the question is reported as typed


def test_kannada_general_question_is_answered_in_kannada(db, router_index):
    r = ask(db, router_index, "ಗೃಹಜ್ಯೋತಿ ಎಂದರೇನು?")
    assert r.route == "knowledge" and kannada.is_kannada(r.answer.split("\n\n", 1)[1])


def test_unknown_kannada_goes_through_unchanged(db, router_index):
    # nothing recognisable: the original text is kept (the model path, offline here, says so)
    r = run(pipeline.answer_question("ನಮಸ್ಕಾರ ಹೇಗಿದ್ದೀರಿ", db, router_index, 200, 10))
    assert r.question == "ನಮಸ್ಕಾರ ಹೇಗಿದ್ದೀರಿ"


def test_every_entry_has_english_and_kannada():
    kb = knowledge._kb()
    for e in kb["places"] + kb["entries"]:
        assert e["en"].strip() and e["kn"].strip() and kannada.is_kannada(e["kn"]), e["key"]
    for f in kb["organisation"]["facets"].values():
        assert f["en"].strip() and kannada.is_kannada(f["kn"])
