import sqlite3
from datetime import timedelta

import pytest

import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PASTEBIN_DB", str(tmp_path / "test.db"))
    db.init_db()


def make_item(content="hello", delta=timedelta(hours=1)):
    return db.create_item(content, db.now_utc() + delta)


def test_create_and_get_roundtrip():
    item_id = make_item("some paste")
    items = db.get_items()
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["content"] == "some paste"
    assert items[0]["processed"] == 0


def test_get_items_newest_first():
    first = make_item("first")
    second = make_item("second")
    assert [item["id"] for item in db.get_items()] == [second, first]


def test_update_item():
    item_id = make_item("before")
    new_expiry = db.now_utc() + timedelta(days=2)
    db.update_item(item_id, "after", new_expiry)
    item = db.get_item(item_id)
    assert item["content"] == "after"
    assert item["expires_at"] == new_expiry.isoformat()


def test_processed_toggle():
    item_id = make_item()
    db.set_processed(item_id, True)
    assert db.get_item(item_id)["processed"] == 1
    db.set_processed(item_id, False)
    assert db.get_item(item_id)["processed"] == 0


def test_delete_item():
    item_id = make_item()
    db.delete_item(item_id)
    assert db.get_item(item_id) is None
    assert db.get_items() == []


def test_attachment_roundtrip():
    item_id = db.create_item("with file", db.now_utc() + timedelta(hours=1),
                             file_name="pic.png", file_type="image/png", file_data=b"\x89PNG")
    item = db.get_item(item_id)
    assert item["file_name"] == "pic.png"
    assert item["file_type"] == "image/png"
    assert item["file_data"] == b"\x89PNG"


def test_set_and_remove_attachment():
    item_id = make_item()
    assert db.get_item(item_id)["file_data"] is None
    db.set_attachment(item_id, "doc.pdf", "application/pdf", b"%PDF")
    assert db.get_item(item_id)["file_data"] == b"%PDF"
    db.set_attachment(item_id, None, None, None)
    assert db.get_item(item_id)["file_data"] is None


def test_set_triage_roundtrip_and_clear():
    item_id = make_item()
    assert db.get_item(item_id)["triage"] is None
    db.set_triage(item_id, "A link.\n\nRecommended: bookmark it.")
    item = db.get_item(item_id)
    assert item["triage"] == "A link.\n\nRecommended: bookmark it."
    assert item["triaged_at"] is not None
    db.set_triage(item_id, None)
    item = db.get_item(item_id)
    assert item["triage"] is None
    assert item["triaged_at"] is None


def test_init_db_migrates_pre_attachment_schema(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("PASTEBIN_DB", str(path))
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " content TEXT NOT NULL, created_at TEXT NOT NULL,"
        " expires_at TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()
    db.init_db()
    item_id = db.create_item("migrated", db.now_utc() + timedelta(hours=1),
                             file_name="a.txt", file_type="text/plain", file_data=b"hi")
    item = db.get_item(item_id)
    assert item["file_data"] == b"hi"
    assert item["triage"] is None  # triage columns migrated in too


def test_is_expired():
    expired_id = db.create_item("old", db.now_utc() - timedelta(minutes=5))
    active_id = make_item("fresh")
    assert db.is_expired(db.get_item(expired_id)) is True
    assert db.is_expired(db.get_item(active_id)) is False
