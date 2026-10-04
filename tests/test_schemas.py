import pytest

from tutanak.schemas import Action, BlockNotes, CatchUp, Overview, Point


def test_a_bare_string_becomes_a_point():
    point = Point.model_validate("Bütçe 2,4 milyon TL.")
    assert (point.text, point.ref) == ("Bütçe 2,4 milyon TL.", None)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (12, 12),
        ("12", 12),
        ("[12]", 12),
        ("satır 12", 12),
        ([12, 13], 12),
        (["[12]"], 12),
        (12.0, 12),
        (None, None),
        ("abc", None),
        ([], None),
        (True, None),
    ],
)
def test_ref_accepts_the_ways_models_write_a_line_number(ref, expected):
    assert Point.model_validate({"text": "x", "ref": ref}).ref == expected
    assert Action.model_validate({"task": "x", "ref": ref}).ref == expected


def test_ref_is_optional():
    assert Point.model_validate({"text": "x"}).ref is None


def test_a_bare_string_becomes_an_action():
    action = Action.model_validate("Raporu hazırlamak")
    assert (action.task, action.owner, action.due, action.ref) == ("Raporu hazırlamak", None, None, None)


def test_action_accepts_text_in_place_of_task():
    action = Action.model_validate({"text": "Raporu hazırlamak", "owner": "Mehmet", "ref": "3"})
    assert (action.task, action.owner, action.ref) == ("Raporu hazırlamak", "Mehmet", 3)


@pytest.mark.parametrize("blank", ["", "  ", "null", "None", "N/A", "-", "yok", "Yok", None])
def test_blank_owner_and_due_become_none(blank):
    action = Action.model_validate({"task": "x", "owner": blank, "due": blank})
    assert action.owner is None
    assert action.due is None


def test_owner_and_due_are_kept_as_text():
    action = Action.model_validate({"task": "x", "owner": "Zeynep", "due": 15})
    assert (action.owner, action.due) == ("Zeynep", "15")


def test_block_notes_tolerate_missing_and_null_lists():
    assert BlockNotes.model_validate({"topic": "Bütçe"}).points == []
    notes = BlockNotes.model_validate(
        {"topic": "Bütçe", "points": None, "decisions": None, "actions": None, "questions": None}
    )
    assert (notes.points, notes.decisions, notes.actions, notes.questions) == ([], [], [], [])


def test_block_notes_accept_bare_strings_as_items():
    notes = BlockNotes.model_validate(
        {
            "topic": "Bütçe",
            "points": ["Bütçe açıklandı.", {"text": "İki kişi alınacak.", "ref": "[2]"}],
            "decisions": ["Geçiş 20 Ekim'de."],
            "actions": ["Raporu hazırlamak", {"text": "Teklif almak", "owner": "yok"}],
            "questions": ["Kod kimde kalacak?"],
        }
    )
    assert [(p.text, p.ref) for p in notes.points] == [("Bütçe açıklandı.", None), ("İki kişi alınacak.", 2)]
    assert notes.decisions[0].text == "Geçiş 20 Ekim'de."
    assert [(a.task, a.owner) for a in notes.actions] == [("Raporu hazırlamak", None), ("Teklif almak", None)]
    assert notes.questions[0].text == "Kod kimde kalacak?"


def test_overview_key_points_accept_objects_and_null():
    overview = Overview.model_validate(
        {"title": "t", "tldr": "özet", "key_points": [{"text": "Bütçe 2,4 milyon TL."}, "Geçiş 20 Ekim'de."]}
    )
    assert overview.key_points == ["Bütçe 2,4 milyon TL.", "Geçiş 20 Ekim'de."]
    assert Overview.model_validate({"title": "t", "tldr": "özet", "key_points": None}).key_points == []
    assert Overview.model_validate({"title": "t", "tldr": "özet"}).key_points == []


def test_catch_up_points_are_lenient_too():
    assert CatchUp.model_validate({"headline": "Bütçe konuşuluyor.", "points": None}).points == []
    reply = CatchUp.model_validate({"headline": "h", "points": ["Bütçe açıklandı.", {"text": "x", "ref": "4"}]})
    assert [(p.text, p.ref) for p in reply.points] == [("Bütçe açıklandı.", None), ("x", 4)]
