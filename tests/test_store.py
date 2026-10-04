import numpy as np
import pytest

from tutanak.store import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "t.db")
    yield db
    db.close()


def test_meeting_round_trip(store):
    meeting_id = store.create_meeting("Planlama", "live")
    store.update_meeting(meeting_id, duration=12.5, summary={"tldr": "özet"}, title_locked=True)
    meeting = store.get_meeting(meeting_id)
    assert meeting["title"] == "Planlama"
    assert meeting["status"] == "recording"
    assert meeting["summary"] == {"tldr": "özet"}
    assert meeting["title_locked"] is True
    assert store.get_meeting("missing") is None


def test_update_rejects_unknown_fields(store):
    meeting_id = store.create_meeting("x", "live")
    with pytest.raises(ValueError):
        store.update_meeting(meeting_id, nonsense=1)


def test_segments_are_ordered_and_filterable_by_time(store):
    meeting_id = store.create_meeting("x", "live")
    store.add_segment(meeting_id, 20, 30, "ikinci", "others")
    store.add_segment(meeting_id, 0, 10, "birinci", "me")
    store.add_segment(meeting_id, 40, 50, "üçüncü")
    assert [s["text"] for s in store.segments(meeting_id)] == ["birinci", "ikinci", "üçüncü"]
    # Overlap semantics: a segment that straddles the window edge is included.
    assert [s["text"] for s in store.segments(meeting_id, 25, 45)] == ["ikinci", "üçüncü"]


def test_list_counts_segments_and_slides(store):
    meeting_id = store.create_meeting("x", "live")
    store.add_segment(meeting_id, 0, 1, "a")
    store.add_slide(meeting_id, 0.0, "0001.jpg", "metin")
    listed = store.list_meetings()[0]
    assert (listed["segment_count"], listed["slide_count"]) == (1, 1)


def test_delete_removes_everything(store):
    meeting_id = store.create_meeting("x", "live")
    store.add_segment(meeting_id, 0, 1, "a")
    store.add_note(meeting_id, 0, 1, {"topic": "t"})
    store.add_bookmark(meeting_id, 0.5, "b")
    store.replace_passages(meeting_id, [{"kind": "transcript", "start": 0, "end": 1, "text": "bütçe"}])
    store.delete_meeting(meeting_id)
    assert store.segments(meeting_id) == []
    assert store.notes(meeting_id) == []
    assert store.bookmarks(meeting_id) == []
    assert store.search_fts(["butce"]) == []


def test_fts_matches_turkish_inflections_and_ignores_diacritics(store):
    meeting_id = store.create_meeting("x", "live")
    ids = store.replace_passages(
        meeting_id,
        [
            {"kind": "transcript", "start": 0, "end": 5, "text": "Bütçeyi gelecek hafta onaylayacağız."},
            {"kind": "transcript", "start": 5, "end": 9, "text": "ISI sensörü arızalı."},
            {"kind": "slide", "start": 9, "end": 9, "text": "Mobil uygulama takvimi"},
        ],
    )
    assert store.search_fts(["butce"]) == [ids[0]]  # prefix of "bütçeyi", no diacritics typed
    assert store.search_fts(["isi"]) == [ids[1]]  # "ısı" typed as "isi" finds "ISI"
    assert store.search_fts(["mobil", "takvim"]) == [ids[2]]
    assert store.search_fts(["mobil", "butce"]) == []
    assert set(store.search_fts(["mobil", "butce"], any_term=True)) == {ids[0], ids[2]}


def test_replace_passages_drops_the_old_index(store):
    meeting_id = store.create_meeting("x", "live")
    store.replace_passages(meeting_id, [{"kind": "note", "start": 0, "end": 1, "text": "eski içerik"}])
    store.replace_passages(meeting_id, [{"kind": "note", "start": 0, "end": 1, "text": "yeni içerik"}])
    assert store.search_fts(["eski"]) == []
    assert len(store.search_fts(["yeni"])) == 1


def test_search_can_be_scoped_to_one_meeting(store):
    first = store.create_meeting("a", "live")
    second = store.create_meeting("b", "live")
    store.replace_passages(first, [{"kind": "note", "start": 0, "end": 1, "text": "bütçe"}])
    ids = store.replace_passages(second, [{"kind": "note", "start": 0, "end": 1, "text": "bütçe"}])
    assert store.search_fts(["butce"], meeting_id=second) == ids


def test_embeddings_round_trip(store):
    meeting_id = store.create_meeting("x", "live")
    ids = store.replace_passages(
        meeting_id,
        [{"kind": "note", "start": i, "end": i + 1, "text": f"p{i}"} for i in range(3)],
    )
    vectors = np.eye(3, 8, dtype=np.float32)
    store.set_embeddings(ids, vectors)
    got_ids, matrix = store.embeddings()
    assert got_ids == ids
    assert np.array_equal(matrix, vectors)
    assert store.embeddings("other") == ([], None)
