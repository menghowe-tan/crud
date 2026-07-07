"""Triage agent: reads the pastebin items and figures out how to process them.

Runs standalone against the same database as the app (PASTEBIN_DB / items.db).
It never modifies existing items; it prints a per-item triage report with
recommended processing actions and saves the report back into the pastebin as a
new item (1-day expiry) so it shows up in the app's display. Past reports are
never re-triaged.

Usage:
    .venv/bin/python agent.py             # analyze unprocessed items
    .venv/bin/python agent.py --all       # include processed items
    .venv/bin/python agent.py --item 3    # analyze one item by id
    .venv/bin/python agent.py --no-save   # print only, don't save to the pastebin

Auth: uses ANTHROPIC_API_KEY or an `ant auth login` profile.
"""

import argparse
import base64
import sys
from datetime import datetime, timedelta

import anthropic

import db

MODEL = "claude-opus-4-8"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # API limit per image
MAX_TEXT_ATTACHMENT_CHARS = 20_000
REPORT_PREFIX = "📋 Triage report"
REPORT_TTL = timedelta(days=1)

SYSTEM = """You are the triage agent for a personal pastebin app. Items hold temporary
information: text notes, URLs, and file attachments (images or documents). Items expire
by timestamp but are never auto-deleted; the user manually deletes items and can mark
them processed.

Your job: for each item, figure out how it should be processed.

For every item:
1. Identify what it is. If the content is or contains a URL, fetch it to see what it
   actually points to. Images are provided inline — look at them.
2. Summarize in 1-3 sentences what it actually contains.
3. Recommend concrete processing action(s) and why — e.g. bookmark or archive the link,
   extract key information, save the attachment somewhere permanent, follow up on a task
   it implies, safe to mark processed, safe to delete.

Then finish with a short overall section: patterns across items, anything that expired
unprocessed, and what the user should do first.

Format the whole answer as a markdown report with one section per item
("## Item <id> — <short title>"). Be specific and grounded in what you actually
fetched or saw; say so plainly when a URL could not be fetched."""


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


def is_report(item: dict) -> bool:
    return item["content"].startswith(REPORT_PREFIX)


def triage_candidates(items: list[dict], include_processed: bool = False,
                      item_id: int | None = None) -> list[dict]:
    """Items the agent should look at — never its own past reports."""
    items = [item for item in items if not is_report(item)]
    if item_id is not None:
        return [item for item in items if item["id"] == item_id]
    if not include_processed:
        items = [item for item in items if not item["processed"]]
    return items


def save_report(report: str, now: datetime) -> int:
    """Store the report as a pastebin item so it shows up in the app."""
    title = f"{REPORT_PREFIX} — {now.strftime('%Y-%m-%d %H:%M UTC')}"
    return db.create_item(f"{title}\n\n{report.strip()}", now + REPORT_TTL)


def run(items: list[dict], now: datetime, model: str = MODEL) -> str:
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
    report_parts: list[str] = []

    while True:
        try:
            with client.messages.stream(
                model=model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=SYSTEM,
                tools=tools,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                    report_parts.append(text)
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
            print("\n[The model declined to analyze these items.]", file=sys.stderr)
        elif response.stop_reason == "max_tokens":
            print("\n[Report truncated at the output-token limit.]", file=sys.stderr)
        print()
        return "".join(report_parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage the pastebin items.")
    parser.add_argument("--all", action="store_true",
                        help="include items already marked processed")
    parser.add_argument("--item", type=int, metavar="ID",
                        help="analyze a single item by id")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--no-save", action="store_true",
                        help="print the report only; don't save it to the pastebin")
    args = parser.parse_args()

    db.init_db()
    now = db.now_utc()
    items = triage_candidates(db.get_items(), include_processed=args.all,
                              item_id=args.item)
    if not items:
        if args.item is not None:
            sys.exit(f"No triageable item with id {args.item}.")
        print("No items to triage.")
        return

    report = run(items, now, model=args.model)
    if report.strip() and not args.no_save:
        item_id = save_report(report, db.now_utc())
        print(f"\nSaved report to the pastebin (item {item_id}, "
              f"expires in {REPORT_TTL.days} day).", file=sys.stderr)


if __name__ == "__main__":
    main()
