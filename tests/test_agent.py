from datetime import timedelta

import pytest

import agent
import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PASTEBIN_DB", str(tmp_path / "test.db"))
    db.init_db()


def get(item_id):
    return db.get_item(item_id)


def test_url_item_becomes_text_block():
    item_id = db.create_item("https://example.com", db.now_utc() + timedelta(hours=1))
    blocks = agent.item_blocks(get(item_id), db.now_utc())
    assert len(blocks) == 1
    assert "https://example.com" in blocks[0]["text"]
    assert "status: Active" in blocks[0]["text"]


def test_expired_status_in_header():
    item_id = db.create_item("old", db.now_utc() - timedelta(hours=1))
    blocks = agent.item_blocks(get(item_id), db.now_utc())
    assert "status: Expired" in blocks[0]["text"]


def test_image_attachment_becomes_image_block():
    item_id = db.create_item("", db.now_utc() + timedelta(hours=1),
                             file_name="pic.png", file_type="image/png",
                             file_data=b"\x89PNG fake")
    blocks = agent.item_blocks(get(item_id), db.now_utc())
    types = [b["type"] for b in blocks]
    assert types == ["text", "text", "image"]
    assert blocks[2]["source"]["media_type"] == "image/png"


def test_oversized_image_falls_back_to_metadata(monkeypatch):
    monkeypatch.setattr(agent, "MAX_IMAGE_BYTES", 4)
    item_id = db.create_item("", db.now_utc() + timedelta(hours=1),
                             file_name="big.png", file_type="image/png",
                             file_data=b"12345")
    blocks = agent.item_blocks(get(item_id), db.now_utc())
    assert all(b["type"] == "text" for b in blocks)
    assert "big.png" in blocks[-1]["text"]


def test_text_attachment_inlined():
    item_id = db.create_item("", db.now_utc() + timedelta(hours=1),
                             file_name="notes.txt", file_type="text/plain",
                             file_data=b"remember the milk")
    blocks = agent.item_blocks(get(item_id), db.now_utc())
    assert "remember the milk" in blocks[1]["text"]


def test_binary_attachment_metadata_only():
    item_id = db.create_item("", db.now_utc() + timedelta(hours=1),
                             file_name="report.pdf", file_type="application/pdf",
                             file_data=b"%PDF-1.4")
    blocks = agent.item_blocks(get(item_id), db.now_utc())
    assert all(b["type"] == "text" for b in blocks)
    assert "report.pdf" in blocks[1]["text"]


def test_triage_candidates_each_item_only_once():
    fresh = db.create_item("new note", db.now_utc() + timedelta(hours=1))
    triaged = db.create_item("seen before", db.now_utc() + timedelta(hours=1))
    db.set_triage(triaged, "A note.\n\nRecommended: delete it.")
    done = db.create_item("done", db.now_utc() + timedelta(hours=1))
    db.set_processed(done, True)

    ids = [i["id"] for i in agent.triage_candidates(db.get_items())]
    assert ids == [fresh]

    ids_all = [i["id"] for i in agent.triage_candidates(db.get_items(),
                                                        include_processed=True)]
    assert sorted(ids_all) == sorted([fresh, done])


def test_triage_candidates_explicit_item_allows_retriage():
    triaged = db.create_item("seen before", db.now_utc() + timedelta(hours=1))
    db.set_triage(triaged, "old note")
    ids = [i["id"] for i in agent.triage_candidates(db.get_items(), item_id=triaged)]
    assert ids == [triaged]


def test_triage_text_format():
    entry = {"item_id": 1, "summary": " A course link. ",
             "recommendation": "Bookmark it, then mark processed. "}
    assert agent.triage_text(entry) == (
        "A course link.\n\nRecommended: Bookmark it, then mark processed."
    )


def test_build_user_content_covers_all_items():
    first = db.create_item("one", db.now_utc() + timedelta(hours=1))
    second = db.create_item("two", db.now_utc() + timedelta(hours=1))
    content = agent.build_user_content(db.get_items(), db.now_utc())
    joined = "\n".join(b["text"] for b in content if b["type"] == "text")
    assert f"Item {first}" in joined and f"Item {second}" in joined
    assert "2 pastebin item(s)" in joined
