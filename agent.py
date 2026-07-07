"""Triage agent: reads the pastebin items and figures out how to process them.

Runs standalone against the same database as the app (PASTEBIN_DB / items.db).
Each item's triage result (summary + recommended processing) is stored on that
item's own row (`triage` / `triaged_at` columns) and shown on its card in the
app. Items are triaged once: anything that already has a triage note is skipped
on later runs.

Usage:
    .venv/bin/python agent.py             # triage unprocessed, un-triaged items
    .venv/bin/python agent.py --all       # also include processed items
    .venv/bin/python agent.py --item 3    # one item by id (re-triages it)
    .venv/bin/python agent.py --no-save   # print only, don't store on the items

Auth: uses ANTHROPIC_API_KEY or an `ant auth login` profile.
"""

import argparse
import base64
import json
import sys
from datetime import datetime

import anthropic

import db

MODEL = "claude-opus-4-8"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # API limit per image
MAX_TEXT_ATTACHMENT_CHARS = 20_000

SYSTEM = """You are the triage agent for a personal pastebin app. Items hold temporary
information: text notes, URLs, and file attachments (images or documents). Items expire
by timestamp but are never auto-deleted; the user manually deletes items and can mark
them processed.

Your job: for each item, figure out how it should be processed.

For every item:
1. Identify what it is. If the content is or contains a URL, fetch it to see what it
   actually points to. Images are provided inline — look at them.
2. Write a 1-3 sentence summary of what it actually contains, grounded in what you
   fetched or saw. Say plainly when a URL could not be fetched.
3. Recommend concrete processing action(s) and why — e.g. bookmark or archive the link,
   extract key information, save the attachment somewhere permanent, follow up on a task
   it implies, safe to mark processed, safe to delete.

Also write a short overall note: patterns across items, anything that expired
unprocessed, and what the user should do first."""

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer"},
                    "summary": {
                        "type": "string",
                        "description": "1-3 sentences on what the item actually contains",
                    },
                    "recommendation": {
                        "type": "string",
                        "description": "Concrete processing action(s) and why",
                    },
                },
                "required": ["item_id", "summary", "recommendation"],
                "additionalProperties": False,
            },
        },
        "overall": {
            "type": "string",
            "description": "Cross-item observations and what the user should do first",
        },
    },
    "required": ["items", "overall"],
    "additionalProperties": False,
}


def fmt_ts(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def item_blocks(item: dict, now: datetime) -> list[dict]:
    """Content blocks describing one item: a text header plus any attachment."""
    status = db.status_of(item, now)
    lines = [
        f"### Item {item['id']} — status: {status}",
        f"created: {fmt_ts(item['created_at'])} · expires: {fmt_ts(item['expires_at'])}"
        f" · processed: {'yes' if item['processed'] else 'no'}",
        "",
        item["content"].strip() or "(no text content)",
    ]
    blocks = [{"type": "text", "text": "\n".join(lines)}]

    if not item["file_data"]:
        return blocks

    name = item["file_name"] or "attachment"
    ftype = item["file_type"] or "application/octet-stream"
    size = len(item["file_data"])
    meta = f"[attachment: {name}, {ftype}, {size:,} bytes]"

    if ftype.startswith("image/") and size <= MAX_IMAGE_BYTES:
        blocks.append({"type": "text", "text": meta})
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": ftype,
                "data": base64.standard_b64encode(item["file_data"]).decode(),
            },
        })
    elif ftype.startswith("text/"):
        text = item["file_data"].decode("utf-8", errors="replace")
        if len(text) > MAX_TEXT_ATTACHMENT_CHARS:
            text = text[:MAX_TEXT_ATTACHMENT_CHARS] + "\n[...truncated for length...]"
        blocks.append({"type": "text", "text": f"{meta}\ncontents:\n{text}"})
    else:
        blocks.append({"type": "text",
                       "text": f"{meta} (binary contents not included — judge from "
                               f"the file name and type)"})
    return blocks


def build_user_content(items: list[dict], now: datetime) -> list[dict]:
    content = [{
        "type": "text",
        "text": f"Current time: {now.isoformat()}. Here are the {len(items)} pastebin "
                f"item(s) to triage:",
    }]
    for item in items:
        content.extend(item_blocks(item, now))
    return content


def triage_candidates(items: list[dict], include_processed: bool = False,
                      item_id: int | None = None) -> list[dict]:
    """Items the agent should look at. Each item is triaged only once —
    already-triaged items are skipped unless requested explicitly via item_id."""
    if item_id is not None:
        return [item for item in items if item["id"] == item_id]
    items = [item for item in items if item["triage"] is None]
    if not include_processed:
        items = [item for item in items if not item["processed"]]
    return items


def triage_text(entry: dict) -> str:
    """The note stored on the item row and shown on its card."""
    return f"{entry['summary'].strip()}\n\nRecommended: {entry['recommendation'].strip()}"


def run(items: list[dict], now: datetime, model: str = MODEL) -> dict:
    """Ask the model to triage the items; returns {'items': [...], 'overall': str}."""
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as exc:
        sys.exit(f"Anthropic client error: {exc}")
    tools = [
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10},
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    ]
    user_content = build_user_content(items, now)
    messages = [{"role": "user", "content": user_content}]
    print(f"Triaging {len(items)} item(s) with {model}…", file=sys.stderr)

    while True:
        try:
            with client.messages.stream(
                model=model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=SYSTEM,
                tools=tools,
                output_config={"format": {"type": "json_schema",
                                          "schema": REPORT_SCHEMA}},
                messages=messages,
            ) as stream:
                response = stream.get_final_message()
        except anthropic.AuthenticationError:
            sys.exit("Invalid Anthropic API credentials.")
        except TypeError as exc:
            if "authentication" in str(exc).lower():
                sys.exit("No Anthropic credentials found — set ANTHROPIC_API_KEY "
                         "or run `ant auth login`.")
            raise

        if response.stop_reason == "pause_turn":
            # Server-side tool loop hit its iteration limit; resume where it left off.
            messages = [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": response.content},
            ]
            continue
        if response.stop_reason == "refusal":
            sys.exit("The model declined to analyze these items.")
        if response.stop_reason == "max_tokens":
            sys.exit("Output hit the token limit before the report was complete.")

        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            sys.exit(f"Could not parse the model's report as JSON:\n{text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage the pastebin items.")
    parser.add_argument("--all", action="store_true",
                        help="include items already marked processed")
    parser.add_argument("--item", type=int, metavar="ID",
                        help="triage a single item by id, even if already triaged")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--no-save", action="store_true",
                        help="print the report only; don't store triage on the items")
    args = parser.parse_args()

    db.init_db()
    now = db.now_utc()
    items = triage_candidates(db.get_items(), include_processed=args.all,
                              item_id=args.item)
    if not items:
        if args.item is not None:
            sys.exit(f"No item with id {args.item}.")
        print("No items to triage (all remaining items are already triaged).")
        return

    report = run(items, now, model=args.model)

    valid_ids = {item["id"] for item in items}
    saved = 0
    for entry in report.get("items", []):
        if entry["item_id"] not in valid_ids:
            continue
        print(f"## Item {entry['item_id']}\n{triage_text(entry)}\n")
        if not args.no_save:
            db.set_triage(entry["item_id"], triage_text(entry))
            saved += 1
    print(f"## Overall\n{report.get('overall', '').strip()}")
    if saved:
        print(f"\nStored triage notes on {saved} item(s) — visible on their cards "
              f"in the app.", file=sys.stderr)


if __name__ == "__main__":
    main()
