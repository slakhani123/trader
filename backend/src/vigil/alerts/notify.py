"""Notification delivery.

In-app is always on (the Alert row itself is the in-app notification; a
NotificationDelivery row records it). Webhook posts a JSON summary with a
deep link. Email sends the same via SMTP when configured. Mobile push is a
documented stub (requires an APNs/FCM account — see docs/LIMITATIONS.md).
"""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from vigil.config import Settings
from vigil.models import Alert, NotificationDelivery

log = logging.getLogger(__name__)


def deep_link(alert: Alert, settings: Settings) -> str:
    base = settings.cors_origins[0] if settings.cors_origins else "http://localhost:5173"
    return f"{base}/alerts/{alert.id}"


def _summary(alert: Alert, settings: Settings) -> dict:
    p = alert.payload
    return {
        "id": alert.id,
        "title": alert.title,
        "ticker": p["company"]["ticker"],
        "family": alert.family,
        "state": alert.lifecycle_state,
        "transition": alert.transition,
        "horizon": alert.horizon,
        "priority": alert.priority,
        "opportunity": p["scores"]["opportunity"],
        "confidence": p["scores"]["confidence"],
        "risk": p["scores"]["risk"],
        "thesis_summary": p["thesis_summary"],
        "link": deep_link(alert, settings),
        "disclaimer": p["disclaimer"],
    }


def deliver(session: Session, alert: Alert, settings: Settings) -> None:
    delivered: dict[str, str] = {"inapp": "sent"}
    session.add(
        NotificationDelivery(alert_id=alert.id, channel="inapp", status="sent")
    )

    if settings.webhook_url:
        status, detail = _send_webhook(alert, settings)
        delivered["webhook"] = status
        session.add(
            NotificationDelivery(
                alert_id=alert.id, channel="webhook", status=status, detail=detail[:400]
            )
        )
    if settings.smtp_host and settings.smtp_to and alert.priority == "high":
        status, detail = _send_email(alert, settings)
        delivered["email"] = status
        session.add(
            NotificationDelivery(
                alert_id=alert.id, channel="email", status=status, detail=detail[:400]
            )
        )
    alert.delivered = delivered


def _send_webhook(alert: Alert, settings: Settings) -> tuple[str, str]:
    try:
        resp = httpx.post(
            settings.webhook_url, json=_summary(alert, settings), timeout=10.0
        )
        if resp.status_code < 300:
            return "sent", f"HTTP {resp.status_code}"
        return "failed", f"HTTP {resp.status_code}"
    except Exception as exc:
        log.warning("webhook delivery failed for %s: %s", alert.id, exc)
        return "failed", str(exc)


def _send_email(alert: Alert, settings: Settings) -> tuple[str, str]:
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[Vigil] {alert.title}"
        msg["From"] = settings.smtp_from or settings.smtp_user
        msg["To"] = settings.smtp_to
        body = _summary(alert, settings)
        msg.set_content(
            f"{body['thesis_summary']}\n\nOpen: {body['link']}\n\n{body['disclaimer']}\n\n"
            + json.dumps(body, indent=2)
        )
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return "sent", "ok"
    except Exception as exc:
        log.warning("email delivery failed for %s: %s", alert.id, exc)
        return "failed", str(exc)


def notify_data_failure(session: Session, job_name: str, message: str, settings: Settings) -> None:
    """Data-failure notification: recorded in-app; webhook if configured."""
    session.add(
        NotificationDelivery(
            alert_id=None, channel="inapp", status="sent",
            detail=f"DATA FAILURE {job_name}: {message}"[:400],
        )
    )
    if settings.webhook_url:
        try:
            httpx.post(
                settings.webhook_url,
                json={"type": "data_failure", "job": job_name, "message": message},
                timeout=10.0,
            )
        except Exception as exc:
            log.warning("data-failure webhook failed: %s", exc)
