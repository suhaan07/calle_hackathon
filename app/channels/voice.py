from __future__ import annotations

import logging
import os

from app.channels.base import Channel, ChannelResult
from app.channels.log_channel import LogChannel
from app.integrations import calle_client

logger = logging.getLogger("recovery.channels.voice")

_RESULT_SCHEMA = {
    "type": "object",
    "required": ["payment_commitment"],
    "properties": {
        "payment_commitment": {
            "type": "string",
            "enum": ["paid_now", "partial", "promised_date", "refused", "no_answer", "unknown"],
            "description": (
                "paid_now: will pay the full outstanding amount right away. partial: will pay part of "
                "it now and the rest later. promised_date: will pay the full amount by a specific future "
                "date, no partial payment now. refused: disputes the invoice, says it's already paid, or "
                "flatly refuses. no_answer: not picked up or went to voicemail. unknown: call connected "
                "but no clear commitment was given."
            ),
        },
        "partial_amount_rupees": {
            "type": "number",
            "description": "Only when payment_commitment is partial: the rupee amount they said they'll pay right now. Omit this field entirely otherwise.",
        },
        "promised_date": {
            "type": "string",
            "description": (
                "ISO date YYYY-MM-DD. Set when payment_commitment is promised_date (date for the full "
                "amount), or when payment_commitment is partial and they also gave a date for the "
                "remaining balance. Omit this field entirely otherwise."
            ),
        },
        "preferred_channel": {
            "type": "string",
            "enum": ["whatsapp", "email", "unspecified"],
            "description": (
                "Only meaningful when payment_commitment is paid_now or partial: which channel they said "
                "is easier for receiving the payment link. unspecified if not asked or not answered."
            ),
        },
        "callback_requested_at": {
            "type": "string",
            "description": (
                "ISO 8601 datetime with the +05:30 offset (e.g. 2026-09-15T18:00:00+05:30) — only if they "
                "genuinely could not decide anything right now and asked to be called back at a specific "
                "day/time instead of giving any commitment. Omit this field entirely otherwise."
            ),
        },
        "escalate_to_human": {
            "type": "boolean",
            "description": "True only if they explicitly asked to speak to a person or account manager instead of continuing with you.",
        },
        "notes": {"type": "string", "description": "Any other relevant detail, e.g. the specific dispute reason. Omit this field entirely if there's nothing to add."},
    },
}


def _task_for(body: str) -> str:
    return (
        "Call the customer about an overdue invoice. Deliver this reminder in your own "
        f'natural words rather than reading it verbatim (it may mix Hindi and English): "{body}" '
        "Then have a real conversation to find out what they can actually do:\n"
        "- If they can pay the full amount right now, or part of it now with the rest later, ask "
        "whether WhatsApp or email is easier for them to receive the payment link on.\n"
        "- If they can't pay now but can commit to a specific future date, get that exact date.\n"
        "- If they dispute the invoice, say they've already paid, or flatly refuse, note the reason.\n"
        "- If they explicitly ask to speak to a person instead of you, say so and stop negotiating.\n"
        "- Only if they genuinely can't decide anything right now and ask to be called back at a "
        "specific day/time, capture that instead of forcing a decision."
    )


class VoiceChannel(Channel):
    """Goal-driven call via CALL-E — it holds the actual conversation (adapting
    to what the customer says) rather than reading a fixed TTS script, and
    returns a structured payment_commitment instead of just a delivery status.
    A future implementation only needs to replace this class — nothing
    upstream changes."""

    name = "voice"

    def send(self, *, to: str, cc: str | None, subject: str, body: str, html: str | None = None) -> ChannelResult:
        override = os.getenv("TEST_VOICE_OVERRIDE")
        if override:
            # Keeps every synthetic customer phone in the demo sheet from
            # actually being dialed. Mirrors EmailChannel's TEST_EMAIL_OVERRIDE:
            # redirect the destination, keep the script itself real (the case's
            # timeline still shows who it would really have gone to — see
            # engine.py's dispatch event, which logs the pre-override `to`).
            to = override

        if not calle_client.is_configured():
            return LogChannel().send(to=to, cc=cc, subject=subject, body=body, html=html)

        try:
            client = calle_client.get_client()
            call = client.calls.create_and_wait(
                task=_task_for(body),
                recipient={"phone": to, "region": "IN", "locale": "hi-IN"},
                result_schema=_RESULT_SCHEMA,
            )
            return ChannelResult(
                status="sent",
                detail=f"calle status={call['status']} result={call.get('structured_result')}",
                structured=call.get("structured_result"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Voice call failed")
            return ChannelResult(status="failed", detail=str(exc))
