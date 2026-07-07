"""Streamlit UI for the pastebin app.

Visual language — "half-life": every item is on a clock, so time-remaining is the
primary signal. Each card carries a lifespan meter that drains in proportion to the
life actually left ((expires - now) / (expires - created)) and shifts colour as the
item ages: teal while alive, amber once nearly spent, rust once expired. Status is
also written into the left spine and a fade on the content, so it reads without the
generic coloured pill.
"""

import base64
import html
from datetime import datetime, timedelta

import streamlit as st

import db

DURATIONS = {
    "10 minutes": timedelta(minutes=10),
    "1 hour": timedelta(hours=1),
    "1 day": timedelta(days=1),
    "1 week": timedelta(weeks=1),
}

STATUSES = ["Active", "Expired", "Processed"]

# Palette — cool archival paper + ink, with one living→dying→dead accent spectrum.
INK = "#1E241E"
MUTED = "#78837A"
SURFACE = "#FCFDFB"
LINE = "#E1E5DC"
ALIVE = "#0E7C6B"   # healthy
DYING = "#C2691A"   # active but nearly expired
DEAD = "#B04B2F"    # expired
PROC = "#5B6B75"    # processed / swept

# When an active item has less than this fraction of its life left, it is "expiring".
DYING_FRACTION = 0.2

CSS = """
<style>
  .block-container { padding-top: 2.4rem; padding-bottom: 4rem; max-width: 46rem; }

  .hl-eyebrow {
    font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; letter-spacing: 0.3em;
    text-transform: uppercase; color: #8b968c; margin-bottom: 0.4rem;
  }
  .hl-title {
    font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 2.7rem;
    line-height: 1; letter-spacing: -0.025em; color: #1E241E; margin: 0;
  }
  .hl-sub { color: #5c665d; margin: 0.55rem 0 0; font-size: 0.95rem; }
  .hl-census {
    font-family: 'JetBrains Mono', monospace; font-size: 0.76rem; color: #78837A;
    margin: 0.95rem 0 0.4rem; display: flex; gap: 1.1rem; flex-wrap: wrap; align-items: center;
  }
  .hl-census .dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 2px;
    margin-right: 0.4rem; vertical-align: middle;
  }

  /* Delete gets a danger affordance only on hover — quiet until you reach for it. */
  [class*="st-key-delete-"] button:hover {
    color: #B04B2F !important; border-color: #B04B2F !important;
  }
</style>
"""


@st.cache_resource
def _init() -> bool:
    db.init_db()
    return True


def humanize(delta: timedelta) -> str:
    seconds = int(abs(delta.total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size} {unit}"
    return f"{seconds} s"


def expiry_label(item: dict, now: datetime) -> str:
    expires_at = datetime.fromisoformat(item["expires_at"])
    if expires_at < now:
        return f"expired {humanize(now - expires_at)} ago"
    return f"expires in {humanize(expires_at - now)}"


def lifespan_fraction(item: dict, now: datetime) -> float:
    """How much of the item's total lifespan is still ahead of it, in [0, 1]."""
    created = datetime.fromisoformat(item["created_at"])
    expires = datetime.fromisoformat(item["expires_at"])
    total = (expires - created).total_seconds()
    if total <= 0:
        return 0.0
    remaining = (expires - now).total_seconds()
    return max(0.0, min(1.0, remaining / total))


def card_style(status: str, frac: float) -> tuple[str, float, float]:
    """(accent colour, meter fill fraction, content opacity) for the item's state."""
    if status == "Processed":
        return PROC, frac, 0.5
    if status == "Expired":
        return DEAD, 0.0, 0.7
    if frac <= DYING_FRACTION:
        return DYING, frac, 1.0
    return ALIVE, frac, 1.0


def attachment_html(item: dict, faded: bool) -> str:
    if not item["file_data"]:
        return ""
    if (item["file_type"] or "").startswith("image/"):
        b64 = base64.b64encode(item["file_data"]).decode()
        dim = "opacity:0.45;" if faded else ""
        return (f'<img src="data:{item["file_type"]};base64,{b64}" '
                f'style="max-width:100%; border-radius:4px; margin-top:0.6rem;'
                f' border:1px solid {LINE}; {dim}">')
    return (f'<div style="margin-top:0.6rem; font-family:\'JetBrains Mono\',monospace;'
            f' font-size:0.82rem; color:{MUTED};">'
            f'\U0001F4CE {html.escape(item["file_name"] or "attachment")}</div>')


def triage_html(item: dict) -> str:
    if not item["triage"]:
        return ""
    when = ""
    if item["triaged_at"]:
        when = datetime.fromisoformat(item["triaged_at"]).astimezone().strftime("%d %b %H:%M")
    return (f'<div style="margin-top:0.8rem; padding:0.55rem 0.75rem;'
            f' background:rgba(14,124,107,0.06); border-left:2px solid {ALIVE};'
            f' border-radius:4px; font-size:0.85rem;">'
            f'<div style="font-family:\'Space Grotesk\',sans-serif; font-weight:600;'
            f' color:{ALIVE}; letter-spacing:0.02em; margin-bottom:0.3rem;">'
            f'\U0001F916 Triage · {when}</div>'
            f'<div style="white-space:pre-wrap; word-break:break-word; color:{INK};">'
            f'{html.escape(item["triage"])}</div></div>')


def render_card(item: dict, status: str, now: datetime) -> None:
    frac = lifespan_fraction(item, now)
    accent, meter_frac, opacity = card_style(status, frac)
    faded = status in ("Processed", "Expired")
    created = datetime.fromisoformat(item["created_at"]).astimezone().strftime("%d %b %Y · %H:%M")
    label = status if status != "Active" else ("expiring" if frac <= DYING_FRACTION else "active")

    content_html = ""
    if item["content"].strip():
        content_html = (f'<div style="font-family:\'JetBrains Mono\',monospace; font-size:0.9rem;'
                        f' line-height:1.5; white-space:pre-wrap; word-break:break-word;'
                        f' color:{INK};">{html.escape(item["content"])}</div>')

    meter = (
        f'<div style="display:flex; align-items:center; gap:0.7rem; margin-top:0.9rem;">'
        f'<div style="flex:1; height:4px; border-radius:999px; background:{LINE}; overflow:hidden;">'
        f'<div style="width:{meter_frac * 100:.1f}%; height:100%; background:{accent};"></div></div>'
        f'<span style="font-family:\'Space Grotesk\',sans-serif; font-size:0.68rem; font-weight:600;'
        f' letter-spacing:0.12em; text-transform:uppercase; color:{accent};'
        f' white-space:nowrap;">{label}</span></div>'
    )
    meta = (
        f'<div style="display:flex; justify-content:space-between; margin-top:0.45rem;'
        f' font-family:\'JetBrains Mono\',monospace; font-size:0.72rem; color:{MUTED};">'
        f'<span>{created}</span><span>{expiry_label(item, now)}</span></div>'
    )

    # One compact, unindented string — Streamlit's Markdown turns any 4-space-indented
    # line into a code block, so the whole card must stay flush-left with no newlines.
    card = (
        f'<div style="background:{SURFACE}; border:1px solid {LINE}; border-left:3px solid {accent};'
        f' border-radius:5px; padding:0.9rem 1.05rem 0.8rem; margin-bottom:0.3rem; opacity:{opacity};">'
        f'{content_html}{attachment_html(item, faded)}{triage_html(item)}{meter}{meta}</div>'
    )
    st.markdown(card, unsafe_allow_html=True)


@st.dialog("Edit item")
def edit_dialog(item: dict) -> None:
    content = st.text_area("Content", value=item["content"], height=150)
    expiry_choice = st.selectbox("Expiry", ["Keep current expiry", *DURATIONS])
    if item["file_data"]:
        action = st.radio("Attachment", ["Keep current", "Replace", "Remove"],
                          horizontal=True, help=f"Current: {item['file_name']}")
    else:
        action = "Replace"
    new_file = None
    if action == "Replace":
        new_file = st.file_uploader("Attach an image or document")
    if st.button("Save changes", type="primary"):
        keeps_attachment = (item["file_data"] and action == "Keep current") or new_file
        if not content.strip() and not keeps_attachment:
            st.warning("Add some content or attach a file.")
            return
        if expiry_choice == "Keep current expiry":
            expires_at = datetime.fromisoformat(item["expires_at"])
        else:
            expires_at = db.now_utc() + DURATIONS[expiry_choice]
        db.update_item(item["id"], content, expires_at)
        if action == "Remove":
            db.set_attachment(item["id"], None, None, None)
        elif new_file:
            db.set_attachment(item["id"], new_file.name,
                              new_file.type or "application/octet-stream",
                              new_file.getvalue())
        st.rerun()


def render_header(items: list[tuple[dict, str]], now: datetime) -> None:
    active = expiring = expired = processed = 0
    for item, status in items:
        if status == "Processed":
            processed += 1
        elif status == "Expired":
            expired += 1
        elif lifespan_fraction(item, now) <= DYING_FRACTION:
            expiring += 1
        else:
            active += 1

    def stat(color: str, count: int, word: str) -> str:
        return (f'<span><span class="dot" style="background:{color};"></span>'
                f'{count} {word}</span>')

    census = ""
    if items:
        census = (f'<div class="hl-census">'
                  f'{stat(ALIVE, active, "active")}{stat(DYING, expiring, "expiring")}'
                  f'{stat(DEAD, expired, "expired")}{stat(PROC, processed, "swept")}</div>')

    st.markdown(
        f'<div class="hl-eyebrow">Ephemeral store</div>'
        f'<h1 class="hl-title">Pastebin</h1>'
        f'<p class="hl-sub">Drop text or a file. Everything here runs on a timer, then waits'
        f' on the pile until you clear it.</p>{census}',
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Pastebin", page_icon="\U0001F4CB", layout="centered")
    _init()
    st.markdown(CSS, unsafe_allow_html=True)

    now = db.now_utc()
    items = [(item, db.status_of(item, now)) for item in db.get_items()]

    render_header(items, now)

    with st.container(border=True):
        with st.form("create", clear_on_submit=True):
            content = st.text_area("Content", placeholder="Paste something…", height=120)
            uploaded = st.file_uploader("Attach an image or document (optional)")
            col_expiry, col_submit = st.columns([3, 1], vertical_alignment="bottom")
            duration = col_expiry.selectbox("Expires after", list(DURATIONS))
            submitted = col_submit.form_submit_button("Add item", type="primary",
                                                      use_container_width=True)
        if submitted:
            if content.strip() or uploaded:
                db.create_item(
                    content,
                    db.now_utc() + DURATIONS[duration],
                    file_name=uploaded.name if uploaded else None,
                    file_type=(uploaded.type or "application/octet-stream") if uploaded else None,
                    file_data=uploaded.getvalue() if uploaded else None,
                )
                st.rerun()
            else:
                st.warning("Add some content or attach a file.")

    selected = st.pills("Filter", STATUSES, selection_mode="multi", default=STATUSES)
    visible = [(item, status) for item, status in items if status in selected]

    if not items:
        st.info("Nothing here yet — paste something above to start the clock.")
    elif not visible:
        st.info("No items match the current filter.")

    for item, status in visible:
        render_card(item, status, now)
        has_file = item["file_data"] is not None
        cols = st.columns([1, 1.4, 1, 1.2, 2.4] if has_file else [1, 1.4, 1, 3.6])
        if cols[0].button("✏️ Edit", key=f"edit-{item['id']}"):
            edit_dialog(item)
        processed_label = "↩️ Unprocess" if item["processed"] else "✓ Processed"
        if cols[1].button(processed_label, key=f"processed-{item['id']}"):
            db.set_processed(item["id"], not item["processed"])
            st.rerun()
        if cols[2].button("\U0001F5D1 Delete", key=f"delete-{item['id']}"):
            db.delete_item(item["id"])
            st.rerun()
        if has_file:
            cols[3].download_button("⬇️ File", data=item["file_data"],
                                    file_name=item["file_name"] or "attachment",
                                    mime=item["file_type"] or "application/octet-stream",
                                    key=f"dl-{item['id']}")


if __name__ == "__main__":
    main()
