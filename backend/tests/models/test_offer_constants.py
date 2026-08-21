"""Offer constants and immutable model shape (Task 1)."""

from __future__ import annotations

from pathlib import Path

from app.models.offer import (
    MAIL_MAX_AUTO_ATTEMPTS,
    MAIL_PROVIDER_CONSOLE,
    MAIL_RETRY_COUNTDOWNS_SECONDS,
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_FAILED,
    OFFER_ATTEMPT_STATUS_PENDING,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_ATTEMPT_STATUS_SUCCEEDED,
    OFFER_STATUSES,
    OFFER_STATUS_DRAFT,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_READY,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
    OFFER_STATUS_VOIDED,
    OFFER_TEMPLATE_CODE,
    OFFER_TEMPLATE_VERSION,
    Offer,
    OfferSendAttempt,
    OfferVersion,
)

OFFER_MODULE = Path(__file__).resolve().parents[2] / "app" / "models" / "offer.py"


def test_offer_statuses_locked_six() -> None:
    assert OFFER_STATUSES == frozenset(
        {
            OFFER_STATUS_DRAFT,
            OFFER_STATUS_READY,
            OFFER_STATUS_SENDING,
            OFFER_STATUS_SENT,
            OFFER_STATUS_FAILED,
            OFFER_STATUS_VOIDED,
        }
    )
    assert OFFER_STATUSES == frozenset(
        {"draft", "ready", "sending", "sent", "failed", "voided"}
    )
    assert len(OFFER_STATUSES) == 6


def test_mail_retry_countdowns_1_5_30_minutes() -> None:
    assert MAIL_RETRY_COUNTDOWNS_SECONDS == {1: 60, 2: 300, 3: 1800}
    assert MAIL_MAX_AUTO_ATTEMPTS == 4


def test_only_console_provider_constant() -> None:
    assert MAIL_PROVIDER_CONSOLE == "console"
    source = OFFER_MODULE.read_text(encoding="utf-8")
    assert "smtp" not in source.lower()


def test_offer_attempt_statuses_include_dead() -> None:
    assert OFFER_ATTEMPT_STATUS_PENDING == "pending"
    assert OFFER_ATTEMPT_STATUS_RUNNING == "running"
    assert OFFER_ATTEMPT_STATUS_SUCCEEDED == "succeeded"
    assert OFFER_ATTEMPT_STATUS_FAILED == "failed"
    assert OFFER_ATTEMPT_STATUS_DEAD == "dead"


def test_offer_template_constants() -> None:
    assert OFFER_TEMPLATE_CODE == "offer_console_v1"
    assert OFFER_TEMPLATE_VERSION == "1"


def test_offer_models_forbid_plaintext_email_and_attachment_columns() -> None:
    for model in (Offer, OfferVersion, OfferSendAttempt):
        columns = set(model.__table__.c.keys())
        assert "recipient_email" not in columns
        for name in columns:
            lower = name.lower()
            assert "attachment" not in lower, f"{model.__name__}.{name}"
            assert "smtp" not in lower, f"{model.__name__}.{name}"
            assert not (
                lower in {"subject", "body_html", "body_text", "email"}
            ), f"plaintext column forbidden: {model.__name__}.{name}"

    offer_cols = set(Offer.__table__.c.keys())
    assert "recipient_email_masked" in offer_cols
    assert "hiring_decision_id" in offer_cols
    assert "lock_version" in offer_cols

    version_cols = set(OfferVersion.__table__.c.keys())
    assert {
        "subject_encrypted",
        "body_html_encrypted",
        "body_text_encrypted",
        "content_hash",
        "frozen",
    } <= version_cols

    attempt_cols = set(OfferSendAttempt.__table__.c.keys())
    assert {
        "provider",
        "attempt_no",
        "idempotency_key",
        "error_code",
        "error_message_safe",
    } <= attempt_cols
    assert OfferSendAttempt.__table__.c["error_message_safe"].type.length == 512

    offer_index_names = {idx.name for idx in Offer.__table__.indexes}
    assert "uq_offers_application_active" in offer_index_names
    assert "ix_offers_application_id" in offer_index_names

    version_uq = {
        uq.name for uq in OfferVersion.__table__.constraints if uq.name
    }
    assert "uq_offer_versions_offer_version_no" in version_uq

    attempt_constraint_names = {
        c.name for c in OfferSendAttempt.__table__.constraints if c.name
    }
    assert "uq_offer_send_attempts_idempotency" in attempt_constraint_names
