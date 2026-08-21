"""Static security review for Offer console delivery (Task 6)."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

OFFER_PATHS = [
    BACKEND_ROOT / "app" / "services" / "offers.py",
    BACKEND_ROOT / "app" / "services" / "mail_providers" / "console.py",
    BACKEND_ROOT / "app" / "services" / "mail_providers" / "base.py",
    BACKEND_ROOT / "app" / "workers" / "mail_tasks.py",
    BACKEND_ROOT / "app" / "api" / "v1" / "endpoints" / "offers.py",
    BACKEND_ROOT / "app" / "models" / "offer.py",
    BACKEND_ROOT / "app" / "core" / "config.py",
    BACKEND_ROOT / "app" / "workers" / "celery_app.py",
]


def _read(path: Path) -> str:
    assert path.is_file(), path
    return path.read_text(encoding="utf-8")


def test_offer_mail_sources_forbid_smtp_and_network_mail() -> None:
    for path in OFFER_PATHS:
        text = _read(path).lower()
        assert "smtplib" not in text
        assert "smtp" not in text or path.name == "config.py"
        if path.name == "config.py":
            assert "smtp" not in text
            assert "mail_host" not in text
            assert "mail_password" not in text


def test_console_provider_logs_only_safe_fields() -> None:
    text = _read(BACKEND_ROOT / "app" / "services" / "mail_providers" / "console.py")
    assert "recipient_email_masked" in text
    assert "content_hash" in text
    assert "safe_log" in text
    assert "_sanitize_error" in text
    # Structured log payload keys only — no plaintext body/email fields.
    block = text.split("safe_log = {", 1)[1].split("}", 1)[0]
    assert "subject" not in block
    assert "body_html" not in block
    assert "body_text" not in block
    assert "recipient_email_masked" in block
    assert '"recipient_email"' not in block


def test_mail_queue_isolated_from_ai_and_default() -> None:
    celery_src = _read(BACKEND_ROOT / "app" / "workers" / "celery_app.py")
    mail_src = _read(BACKEND_ROOT / "app" / "workers" / "mail_tasks.py")
    offers_src = _read(BACKEND_ROOT / "app" / "services" / "offers.py")
    assert "app.workers.mail_tasks" in celery_src
    assert "process_mail_send_attempt" in celery_src
    assert "celery_mail_queue_name" in celery_src
    assert "celery_sensitive_queue_name" in celery_src  # AI route preserved via Settings
    assert "process_ai_task" not in mail_src
    assert "process_sensitive_ai_task" not in mail_src
    assert "from app.models.ai_task" not in mail_src
    assert "ai_sensitive" not in mail_src.lower()
    assert "enqueue_ai_task" not in offers_src
    assert "process_ai_task" not in offers_src
    assert "APPLICATION_STATUS_HIRED" not in offers_src
    assert 'status = "hired"' not in offers_src
    assert "PIPELINE_HIRED" not in offers_src


def test_offer_api_module_has_no_smtp_dify_or_enqueue() -> None:
    text = _read(BACKEND_ROOT / "app" / "api" / "v1" / "endpoints" / "offers.py").lower()
    assert "smtp" not in text
    assert "dify" not in text
    assert "apply_async" not in text
    assert "enqueue_mail_send_attempt" not in text
    assert "attachment" not in text
    assert "esign" not in text or "esign" not in text
    assert "accept_offer" not in text
    assert "reject_offer" not in text


def test_offer_model_has_no_plaintext_email_or_body_columns() -> None:
    text = _read(BACKEND_ROOT / "app" / "models" / "offer.py")
    assert "recipient_email_masked" in text
    assert "subject_encrypted" in text
    assert "body_html_encrypted" in text
    assert "body_text_encrypted" in text
    assert 'mapped_column(String' in text
    assert "recipient_email:" not in text
    assert "subject:" not in text or "subject_encrypted" in text
    assert "MAIL_MAX_AUTO_ATTEMPTS = 4" in text
    assert "MAIL_RETRY_COUNTDOWNS_SECONDS = {1: 60, 2: 300, 3: 1800}" in text


def test_env_example_has_mail_queue_without_smtp() -> None:
    text = (BACKEND_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "CELERY_MAIL_QUEUE_NAME=" in text
    assert "smtp" not in text.lower()
    assert "SMTP" not in text


def test_protected_running_task_ids_not_referenced_in_offer_code() -> None:
    protected = (
        "dde1470f-d9ef-458c-a29d-e7a8c9f5bcca",
        "3556206d-138b-40f6-9b23-97fce178a32e",
    )
    for path in OFFER_PATHS:
        text = _read(path)
        for task_id in protected:
            assert task_id not in text
