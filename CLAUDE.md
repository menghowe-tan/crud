# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A simple pastebin webapp (Streamlit + SQLite) for storing temporary information. Items can be created, edited, deleted, marked as processed, and filtered by status. Items hold text and/or one file attachment (image or document).

## Commands

```bash
# Setup (one-time)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run the app
.venv/bin/streamlit run app.py

# Run tests
.venv/bin/pytest
# Single test
.venv/bin/pytest tests/test_db.py::test_update_item

# Run the triage agent (read-only analysis of stored items; needs ANTHROPIC_API_KEY
# or an `ant auth login` profile)
.venv/bin/python agent.py            # unprocessed items
.venv/bin/python agent.py --all      # include processed
.venv/bin/python agent.py --item 3   # one item
```

## Architecture

Three entry points over one data layer:

- `db.py` — SQLite data layer (stdlib `sqlite3`). Database file is `items.db`, overridable via the `PASTEBIN_DB` env var (tests point it at a temp file, re-read on every connect). Timestamps are stored as ISO-8601 UTC strings. Attachments live as BLOBs on the items row (`file_name`/`file_type`/`file_data`, all nullable — an item needs text or a file, enforced in the UI). `init_db()` migrates pre-attachment databases via `ALTER TABLE`; follow that pattern for future schema changes.
- `app.py` — Streamlit UI. Item **status is derived, not stored**: `db.status_of()` combines the `processed` flag and `expires_at` vs now into Active / Expired / Processed, so filtering happens in Python after `db.get_items()`, not in SQL. `main()` is guarded by `if __name__ == "__main__"` so importing `app` has no side effects.
- `agent.py` — standalone triage agent (Anthropic SDK, Claude Opus + adaptive thinking + server-side web_fetch/web_search). Reads items via `db`, sends text/URLs/inline images to the model, prints a markdown report of how to process each item, and saves that report back as a new pastebin item (`REPORT_PREFIX` title, 1-day expiry) so it appears in the app. It never modifies existing items, and `triage_candidates()` excludes past reports so the agent never analyzes its own output. Handles `pause_turn` (server-tool loop) by resending; must not import `app` (that would pull in Streamlit).

Item lifecycle: items expire by timestamp but are **never auto-deleted** — expiry only changes their display; deletion is manual. Processed is a user toggle.

Display precedence: **processed (gray) > expired (red) > active**. Cards are rendered as raw HTML via `st.markdown(unsafe_allow_html=True)` with colours from `CARD_STYLES` in `app.py` — paste content and file names must stay wrapped in `html.escape()` to prevent HTML injection. Image attachments are embedded in the card as base64 data URIs (faded when processed); non-image attachments get a 📎 row plus a download button in the action row. Upload size is capped in `.streamlit/config.toml` (`maxUploadSize`), which also pins the light theme because the card palette is light.

Streamlit's `AppTest` (`streamlit.testing.v1`) works for end-to-end UI checks, with two limits: `st.file_uploader` can't be scripted (seed attachments through `db` instead) and `st.dialog` contents aren't reachable (exercise `db.update_item` directly).
