"""Streamlit UI for the pastebin app."""

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

# status -> (card background, card border, text colour, badge text, badge background)
CARD_STYLES = {
    "Active": ("#FFFFFF", "#E2E8F0", "#1A202C", "#16A34A", "#F0FDF4"),
    "Expired": ("#FEF2F2", "#FECACA", "#1A202C", "#DC2626", "#FEE2E2"),
    "Processed": ("#F1F5F9", "#E2E8F0", "#94A3B8", "#64748B", "#E2E8F0"),
}


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


def attachment_html(item: dict, status: str) -> str:
    if not item["file_data"]:
        return ""
    if (item["file_type"] or "").startswith("image/"):
        b64 = base64.b64encode(item["file_data"]).decode()
        faded = "opacity:0.45;" if status == "Processed" else ""
        return (f'<img src="data:{item["file_type"]};base64,{b64}" '
                f'style="max-width:100%; border-radius:6px; margin-top:0.5rem; {faded}">')
    return (f'<div style="margin-top:0.5rem; font-size:0.85rem;">'
            f'📎 {html.escape(item["file_name"] or "attachment")}</div>')


def triage_html(item: dict) -> str:
    if not item["triage"]:
        return ""
    when = ""
    if item["triaged_at"]:
        when = datetime.fromisoformat(item["triaged_at"]).astimezone().strftime("%d %b %H:%M")
    return (f'<div style="margin-top:0.75rem; padding:0.5rem 0.75rem;'
            f' background:rgba(37,99,235,0.07); border-left:3px solid #2563EB;'
            f' border-radius:6px; font-size:0.85rem;">'
            f'<div style="font-weight:600; color:#2563EB;'
            f' margin-bottom:0.25rem;">🤖 Triage · {when}</div>'
            f'<div style="white-space:pre-wrap; word-break:break-word;">'
            f'{html.escape(item["triage"])}</div></div>')


def render_card(item: dict, status: str, now: datetime) -> None:
    bg, border, text, badge_fg, badge_bg = CARD_STYLES[status]
    created = datetime.fromisoformat(item["created_at"]).astimezone().strftime("%d %b %Y %H:%M")
    content_html = ""
    if item["content"].strip():
        content_html = (f'<div style="font-family:monospace; white-space:pre-wrap;'
                        f' word-break:break-word;">{html.escape(item["content"])}</div>')
    st.markdown(
        f"""
        <div style="background:{bg}; border:1px solid {border}; border-radius:10px;
                    padding:1rem; margin-bottom:0.25rem; color:{text};">
          {content_html}
          {attachment_html(item, status)}
          {triage_html(item)}
          <div style="display:flex; justify-content:space-between; align-items:center;
                      margin-top:0.75rem; font-size:0.8rem; color:#64748B;">
            <span>created {created} · {expiry_label(item, now)}</span>
            <span style="color:{badge_fg}; background:{badge_bg}; border-radius:999px;
                         padding:0.1rem 0.6rem; font-weight:600;">{status}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
    if st.button("Save", type="primary"):
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


def main() -> None:
    st.set_page_config(page_title="Pastebin", page_icon="📋", layout="centered")
    _init()

    st.title("📋 Pastebin")
    st.caption("Store temporary information — items expire but stick around until you delete them.")

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

    now = db.now_utc()
    items = [(item, db.status_of(item, now)) for item in db.get_items()]
    visible = [(item, status) for item, status in items if status in selected]

    if not items:
        st.info("No items yet — add one above.")
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
        if cols[2].button("🗑 Delete", key=f"delete-{item['id']}"):
            db.delete_item(item["id"])
            st.rerun()
        if has_file:
            cols[3].download_button("⬇️ File", data=item["file_data"],
                                    file_name=item["file_name"] or "attachment",
                                    mime=item["file_type"] or "application/octet-stream",
                                    key=f"dl-{item['id']}")


if __name__ == "__main__":
    main()
