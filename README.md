# 📋 Pastebin

A small pastebin webapp for parking temporary information — links, notes, screenshots,
files — with a Claude-powered agent that works out what to do with each item.

Built with **Streamlit + SQLite** (stdlib `sqlite3`, single `items.db` file, no server
to run) and the **Anthropic SDK** for the triage agent.

## What it does

- **Create, edit, delete items.** An item holds text and/or one file attachment
  (image or document). Images display inline; other files get a download button.
- **Everything runs on a timer.** Each item gets an expiry (10 minutes to 1 week).
  Expired items are never auto-deleted — they just change appearance and wait on the
  pile until you clear them.
- **Mark items processed** ("swept") when you're done with them, and filter the list
  by Active / Expired / Processed.
- **Triage agent** (`agent.py`): a standalone CLI that reads the same database,
  investigates each item — fetching URLs live and actually looking at image
  attachments — and stores a summary plus a concrete processing recommendation on the
  item's row, shown on its card in the app.

Item status is **derived, never stored**: the `processed` flag and `expires_at` vs now
produce Active / Expired / Processed, with display precedence processed › expired › active.

## The UI — "half-life"

Time-remaining is the primary signal. Every card carries a lifespan meter that drains
in proportion to the life the item has left, and shifts colour as it ages: **teal**
while alive, **amber** once nearly spent (under 20% of its lifespan left), **rust**
once expired, cool **slate** once processed. A census row in the header counts items
in each state.

## Quick start

```bash
# one-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# run the app
.venv/bin/streamlit run app.py
```

The app opens at `http://localhost:8501` and creates `items.db` on first run.

## The triage agent

```bash
# auth (one line, gitignored)
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

.venv/bin/python agent.py             # triage new items
.venv/bin/python agent.py --all       # include processed items
.venv/bin/python agent.py --item 3    # re-triage one item by id
.venv/bin/python agent.py --no-save   # print only, don't store notes
```

The agent uses Claude Opus 4.8 with adaptive thinking, server-side web fetch/search,
vision for image attachments, and structured JSON output. Get an API key at
[platform.claude.com](https://platform.claude.com/settings/keys); a shell-exported
`ANTHROPIC_API_KEY` takes precedence over `.env`.

Two rules it always follows:

- **Each item is triaged once.** Items that already have a triage note are skipped;
  `--item ID` is the explicit re-triage escape hatch.
- **It never modifies your items** beyond writing the `triage` / `triaged_at` columns —
  no deleting, no marking processed.

## Architecture

| File       | Role |
|------------|------|
| `db.py`    | SQLite data layer. Attachments live as BLOBs on the items row; timestamps are ISO-8601 UTC; `init_db()` migrates older databases via `ALTER TABLE`. `PASTEBIN_DB` env var overrides the database path. |
| `app.py`   | Streamlit UI. Derives status in Python after `db.get_items()`, renders cards as hand-built HTML (all content `html.escape()`d), pins a light theme in `.streamlit/config.toml`. |
| `agent.py` | Triage agent. Builds one multimodal request from the items, handles the server-tool `pause_turn` loop, writes results via `db.set_triage()`. Never imports `app`. |

## Tests

```bash
.venv/bin/pytest                               # full suite
.venv/bin/pytest tests/test_db.py::test_update_item   # single test
```

Covers the data layer (CRUD, attachments, triage columns, schema migration) and the
agent's item-selection and message-building logic. UI checks use Streamlit's `AppTest`;
the live API call is the only untested path.
