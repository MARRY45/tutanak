import pytest

from tutanak.textutil import (
    content_terms,
    extract_json,
    fmt_ts,
    fold,
    fts_query,
    query_terms,
    similar,
    snippet,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bütçe", "butce"),
        ("IŞIK", "isik"),
        ("ışık", "isik"),
        ("İSTANBUL", "istanbul"),
        ("istanbul", "istanbul"),
        ("Çağrı Öğüt", "cagri ogut"),
        ("Über Café", "uber cafe"),
    ],
)
def test_fold_is_case_and_diacritic_insensitive(text, expected):
    assert fold(text) == expected


def test_dotted_and_dotless_i_match_each_other():
    assert fold("ISI") == fold("ısı") == fold("isi")


def test_query_terms_are_unique_and_ordered():
    assert query_terms("Bütçe bütçe  KARAR") == ["butce", "karar"]


def test_fts_query_uses_prefixes_only_for_longer_terms():
    assert fts_query(["butce", "ne"]) == '"butce"* "ne"'
    assert fts_query(["butce", "karar"], any_term=True) == '"butce"* OR "karar"*'


def test_content_terms_drops_question_words():
    terms = query_terms("Kart taşıma ne zaman yapılacak?")
    assert content_terms(terms) == ["kart", "tasima", "yapilacak"]
    assert content_terms(["ne", "zaman"]) == ["ne", "zaman"]  # nothing else left: keep


def test_snippet_centres_on_the_match():
    text = "giriş " * 60 + "bütçeyi onayladık " + "kapanış " * 60
    cut = snippet(text, ["butce"], width=80)
    assert "bütçeyi" in cut
    assert cut.startswith("…") and cut.endswith("…")
    assert len(cut) < 100


def test_snippet_keeps_short_text():
    assert snippet("kısa  metin", ["x"]) == "kısa metin"


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0, "00:00"), (65.9, "01:05"), (3723, "1:02:03"), (-5, "00:00")]
)
def test_fmt_ts(seconds, expected):
    assert fmt_ts(seconds) == expected


def test_similar_detects_rewordings_only():
    assert similar("Geçişe 20 Ekim'de başlanacak.", "geçişe 20 ekim'de başlanacak")
    assert not similar("Geçişe 20 Ekim'de başlanacak.", "Bütçe 2,4 milyon TL.")
    assert not similar("", "")


@pytest.mark.parametrize(
    "reply",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Here you go:\n{"a": 1}\nHope that helps.',
        '<think>the user wants {"b": 2}</think>{"a": 1}',
    ],
)
def test_extract_json_tolerates_wrapping(reply):
    assert extract_json(reply) == {"a": 1}


@pytest.mark.parametrize("reply", ["no json here", "[1, 2]", '{"a": '])
def test_extract_json_rejects_non_objects(reply):
    with pytest.raises(ValueError):
        extract_json(reply)
