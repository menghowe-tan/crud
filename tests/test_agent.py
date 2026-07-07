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


def test_triage_candidates_skips_processed_and_reports():
    plain = db.create_item("note", db.now_utc() + timedelta(hours=1))
    done = db.create_item("done", db.now_utc() + timedelta(hours=1))
    db.set_processed(done, True)
    agent.save_report("nothing to do", db.now_utc())

    ids = [i["id"] for i in agent.triage_candidates(db.get_items())]
    assert ids == [plain]
    ids_all = [i["id"] for i in agent.triage_candidates(db.get_items(),
                                                        include_processed=True)]
    assert sorted(ids_all) == sorted([plain, done])


def test_triage_candidates_single_item_never_a_report():
    report_id = agent.save_report("some findings", db.now_utc())
    assert agent.triage_candidates(db.get_items(), item_id=report_id) == []


def test_save_report_creates_pastebin_item():
    now = db.now_utc()
    item_id = agent.save_report("## Item 1\nBookmark it.", now)
    item = db.get_item(item_id)
    assert item["content"].startswith(agent.REPORT_PREFIX)
    assert "Bookmark it." in item["content"]
    assert item["expires_at"] == (now + agent.REPORT_TTL).isoformat()
    assert db.status_of(item, now) == "Active"


def test_build_user_content_covers_all_items():
    first = db.create_item("one", db.now_utc() + timedelta(hours=1))
    second = db.create_item("two", db.now_utc() + timedelta(hours=1))
    content = agent.build_user_content(db.get_items(), db.now_utc())
    joined = "\n".join(b["text"] for b in content if b["type"] == "text")
    assert f"Item {first}" in joined and f"Item {second}" in joined
    assert "2 pastebin item(s)" in joined
