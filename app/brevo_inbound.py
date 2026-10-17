"""Match Brevo inbound-parsed replies to known outreach without auto-replying."""
from __future__ import annotations

import re
from typing import Any
from email.utils import parseaddr

from bs4 import BeautifulSoup

from app.lead_engine import normalize_email
from app.reply_processor import process_reply


def _mailbox(value: Any) -> str:
    if isinstance(value, str):
        raw = value.strip()
    elif isinstance(value, dict):
        raw = str(value.get("Address") or value.get("address") or value.get("Email") or value.get("email") or "").strip()
    else:
        raw = ""
    _, address = parseaddr(raw)
    return normalize_email(address or raw)


def _message_reference(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw.strip(" <>\t\r\n")


def _plain_body(item: dict[str, Any]) -> str:
    for key in ("ExtractedMarkdownMessage", "ExtractedTextBody", "TextBody", "RawTextBody", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            body = value.strip()
            break
    else:
        html = item.get("RawHtmlBody") or item.get("HtmlBody")
        if not isinstance(html, str) or not html.strip():
            return ""
        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        body = soup.get_text("\n", strip=True)

    lines: list[str] = []
    for line in body.splitlines():
        text = line.strip()
        if re.match(r"^(On .{1,180}wrote:|[-_ ]{2,}Original Message[-_ ]{2,})$", text, re.IGNORECASE):
            break
        if text.startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:12000]


def _match_lead(conn, sender: str, in_reply_to: str | None) -> int | None:
    if in_reply_to:
        row = conn.execute(
            """SELECT lead_id FROM outreach
               WHERE lower(COALESCE(provider,'')) = 'brevo'
                 AND status = 'SENT'
                 AND lower(replace(replace(trim(COALESCE(provider_message_id,'')), '<', ''), '>', '')) = ?
               ORDER BY id DESC LIMIT 1""",
            (in_reply_to,),
        ).fetchone()
        if row is not None:
            return int(row["lead_id"])
    if not sender:
        return None
    rows = conn.execute(
        """SELECT l.id
           FROM leads l
           WHERE lower(trim(COALESCE(l.email,''))) = ?
             AND EXISTS (
               SELECT 1 FROM outreach o
               WHERE o.lead_id = l.id AND o.status = 'SENT'
             )
           ORDER BY l.updated_at DESC, l.id DESC
           LIMIT 2""",
        (sender,),
    ).fetchall()
    # An address must resolve to exactly one historical lead.
    if len(rows) != 1:
        return None
    return int(rows[0]["id"])


def process_brevo_inbound(conn, payload: dict[str, Any]) -> dict[str, int]:
    """Process a Brevo inbound-parse payload and return counts only.

    Unknown/ambiguous senders are not attached to a lead. Every matched reply
    is handled by the deterministic classifier, saved idempotently, and
    cancels any reminders. This function never sends an automatic response.
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = [payload] if isinstance(payload, dict) else []
    stats = {"received": len(items), "matched": 0, "unmatched": 0, "duplicates": 0, "processed": 0}
    for item in items:
        if not isinstance(item, dict):
            stats["unmatched"] += 1
            continue
        sender = _mailbox(item.get("From") or item.get("from"))
        subject = str(item.get("Subject") or item.get("subject") or "").strip()[:500]
        body = _plain_body(item)
        ref = _message_reference(item.get("InReplyTo") or item.get("In-Reply-To"))
        if not sender or not body:
            stats["unmatched"] += 1
            continue
        lead_id = _match_lead(conn, sender, ref or None)
        if lead_id is None:
            stats["unmatched"] += 1
            continue
        result = process_reply(
            conn,
            lead_id=lead_id,
            from_email=sender,
            subject=subject,
            body=body,
            source="brevo_inbound_parse",
        )
        if not result.get("accepted"):
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1
        if result.get("duplicate"):
            stats["duplicates"] += 1
        else:
            stats["processed"] += 1
    return stats
