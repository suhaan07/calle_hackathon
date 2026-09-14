"""Best-effort outbound WhatsApp sends, all through the Twilio REST API
directly rather than app/channels/ (that registry is for escalation
dispatch — spoc/manager/skip_level/voice). Two purposes:

1. A payment-received alert to whoever last WhatsApp-queried that
   customer's report (Customer.last_whatsapp_query_from, set in
   app/webhooks/whatsapp_webhook.py) — falling back to a fixed
   PAYMENT_NOTIFY_WHATSAPP_TO only when nobody has queried that account yet.
2. The actual payment link, sent straight to the CUSTOMER'S own WhatsApp
   the instant a CALL-E voice call ends with a "pay now" commitment (see
   app/cases/engine.py::_handle_voice_outcome) — the one exception to
   "WhatsApp stays inbound-only for the customer side."

Every function here must never be able to fail the payment-processing (or
voice-dispatch) flow it's attached to."""

from __future__ import annotations

import logging
import os

from app.integrations import twilio_client
from app.reports.format_utils import format_inr

logger = logging.getLogger("recovery.notifications")

_SANDBOX_FROM = "whatsapp:+14155238886"


def _as_whatsapp_address(raw: str) -> str:
    raw = raw.strip()
    return raw if raw.startswith("whatsapp:") else f"whatsapp:{raw}"


def notify_payment_received(
    *,
    customer_name: str,
    total_amount: float,
    invoice_numbers: list[str],
    to_number: str | None = None,
) -> None:
    to = to_number or os.getenv("PAYMENT_NOTIFY_WHATSAPP_TO")
    if not to:
        return  # nobody has queried this account on WhatsApp yet, and no fallback configured — silent no-op, not an error

    if not invoice_numbers:
        return  # nothing actually closed (shouldn't happen, but never send an empty confirmation)

    if not twilio_client.is_configured():
        logger.info("PAYMENT_NOTIFY_WHATSAPP_TO is set but Twilio credentials are missing — skipping notification")
        return

    invoices_text = ", ".join(invoice_numbers)
    plural = "invoice" if len(invoice_numbers) == 1 else "invoices"
    body = (
        f"Payment received: {format_inr(total_amount)} from {customer_name} — "
        f"clearing {len(invoice_numbers)} {plural}: {invoices_text}."
    )

    from_number = os.getenv("TWILIO_WHATSAPP_FROM") or _SANDBOX_FROM

    try:
        client = twilio_client.get_client()
        client.messages.create(from_=_as_whatsapp_address(from_number), to=_as_whatsapp_address(to), body=body)
    except Exception:  # noqa: BLE001 — a notification failure must never block a payment from closing out
        logger.exception("payment-received WhatsApp notification failed (the payment itself was still recorded correctly)")


def send_payment_link_to_customer(
    *,
    customer_phone: str | None,
    customer_name: str,
    invoice_no: str,
    amount_rupees: float,
    pay_link_url: str | None,
) -> None:
    """Fires the instant CALL-E just promised on the call: the moment a
    voice call ends with payment_commitment="paid_now", this sends the real
    Razorpay link to the customer's own WhatsApp — closing the loop within
    seconds, not the next batch run."""
    if not customer_phone or not pay_link_url:
        return  # no number to reach them on, or the link itself is unavailable (oversized-stub case)

    if not twilio_client.is_configured():
        logger.info("voice call promised a payment link but Twilio credentials are missing — skipping WhatsApp send")
        return

    body = (
        f"Hi {customer_name}, as discussed on the call — here's your payment link for "
        f"invoice {invoice_no} ({format_inr(amount_rupees)}): {pay_link_url}"
    )
    from_number = os.getenv("TWILIO_WHATSAPP_FROM") or _SANDBOX_FROM

    try:
        client = twilio_client.get_client()
        client.messages.create(from_=_as_whatsapp_address(from_number), to=_as_whatsapp_address(customer_phone), body=body)
    except Exception:  # noqa: BLE001 — must never break the voice-dispatch path that triggered this
        logger.exception("post-call payment-link WhatsApp send failed")


def send_ops_digest(*, broken_promise_customer_names: list[str], exhausted_case_summaries: list[str]) -> None:
    """Proactive alert for the two things that otherwise sit silently in
    the DB until someone happens to open the portal: a promise-to-pay date
    passing unpaid, or a case exhausting the full escalation chain unpaid.
    Called from run_batch() with only what's NEWLY resolved THIS run (see
    app/cases/engine.py::resolve_promises), so a long-broken promise never
    gets re-reported on every subsequent batch run. Same fixed ops number
    as the payment-received alert — best-effort, never raises."""
    if not broken_promise_customer_names and not exhausted_case_summaries:
        return  # nothing new to report — the common case, stay silent

    to = os.getenv("PAYMENT_NOTIFY_WHATSAPP_TO")
    if not to:
        return

    if not twilio_client.is_configured():
        logger.info("PAYMENT_NOTIFY_WHATSAPP_TO is set but Twilio credentials are missing — skipping ops digest")
        return

    lines = ["Recovery digest:"]
    if broken_promise_customer_names:
        plural = "promise" if len(broken_promise_customer_names) == 1 else "promises"
        lines.append(f"Broken {plural} ({len(broken_promise_customer_names)}): {', '.join(broken_promise_customer_names)}")
    if exhausted_case_summaries:
        plural = "case" if len(exhausted_case_summaries) == 1 else "cases"
        lines.append(f"Exhausted, unpaid despite full chain ({len(exhausted_case_summaries)} {plural}): {', '.join(exhausted_case_summaries)}")
    body = "\n".join(lines)

    from_number = os.getenv("TWILIO_WHATSAPP_FROM") or _SANDBOX_FROM

    try:
        client = twilio_client.get_client()
        client.messages.create(from_=_as_whatsapp_address(from_number), to=_as_whatsapp_address(to), body=body)
    except Exception:  # noqa: BLE001 — a notification failure must never block a batch run from completing
        logger.exception("ops digest WhatsApp send failed")
