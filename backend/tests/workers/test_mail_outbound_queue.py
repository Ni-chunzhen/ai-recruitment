"""mail_outbound Celery route and Settings (Task 3)."""

from __future__ import annotations

import importlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings, get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MAIL_TASK_NAME = "app.workers.mail_tasks.process_mail_send_attempt"
SENSITIVE_TASK_NAME = "app.workers.ai_tasks.process_sensitive_ai_task"
MAIL_TASKS_SRC = BACKEND_ROOT / "app" / "workers" / "mail_tasks.py"
ENV_EXAMPLE = BACKEND_ROOT / ".env.example"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_celery_mail_queue_name_default_mail_outbound(monkeypatch) -> None:
    monkeypatch.delenv("CELERY_MAIL_QUEUE_NAME", raising=False)
    settings = Settings(_env_file=None)
    assert settings.celery_mail_queue_name == "mail_outbound"


def test_celery_mail_queue_name_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("CELERY_MAIL_QUEUE_NAME", "uat_mail_q")
    get_settings.cache_clear()
    assert get_settings().celery_mail_queue_name == "uat_mail_q"


def test_celery_mail_queue_name_empty_normalizes(monkeypatch) -> None:
    monkeypatch.setenv("CELERY_MAIL_QUEUE_NAME", "  ")
    get_settings.cache_clear()
    assert get_settings().celery_mail_queue_name == "mail_outbound"


def test_config_has_no_smtp_settings_fields() -> None:
    fields = set(Settings.model_fields.keys())
    lowered = {name.lower() for name in fields}
    for banned in ("smtp", "smtp_host", "smtp_password", "mail_host", "mail_password"):
        assert banned not in lowered
        assert not any(banned in name for name in lowered)


def test_task_routes_mail_to_mail_outbound_not_ai_sensitive(monkeypatch) -> None:
    monkeypatch.delenv("CELERY_MAIL_QUEUE_NAME", raising=False)
    monkeypatch.delenv("CELERY_SENSITIVE_QUEUE_NAME", raising=False)
    get_settings.cache_clear()
    from app.workers import celery_app as celery_mod

    importlib.reload(celery_mod)
    routes = celery_mod.celery_app.conf.task_routes or {}
    assert MAIL_TASK_NAME in routes
    assert routes[MAIL_TASK_NAME]["queue"] == get_settings().celery_mail_queue_name
    assert routes[MAIL_TASK_NAME]["queue"] == "mail_outbound"
    assert routes[MAIL_TASK_NAME]["queue"] != "ai_sensitive"
    assert routes[MAIL_TASK_NAME]["queue"] != "celery"
    assert SENSITIVE_TASK_NAME in routes
    assert routes[SENSITIVE_TASK_NAME]["queue"] == "ai_sensitive"


def test_celery_include_has_mail_tasks_module(monkeypatch) -> None:
    get_settings.cache_clear()
    from app.workers import celery_app as celery_mod

    importlib.reload(celery_mod)
    include = list(celery_mod.celery_app.conf.include or [])
    assert "app.workers.mail_tasks" in include
    assert "app.workers.ai_tasks" in include


def test_enqueue_targets_process_mail_send_attempt_only(monkeypatch) -> None:
    from app.services import offers as svc

    calls: list[dict] = []

    class _Task:
        @staticmethod
        def apply_async(*, args, countdown=0):
            calls.append({"args": args, "countdown": countdown})

    monkeypatch.setattr(svc, "process_mail_send_attempt", _Task)
    attempt_id = uuid4()
    svc.enqueue_mail_send_attempt(attempt_id, countdown=60)
    assert len(calls) == 1
    assert calls[0]["args"] == [str(attempt_id)]
    assert calls[0]["countdown"] == 60


def test_mail_tasks_module_has_no_ai_task_imports_for_send() -> None:
    assert MAIL_TASKS_SRC.is_file()
    text = MAIL_TASKS_SRC.read_text(encoding="utf-8")
    lower = text.lower()
    assert "process_ai_task" not in text
    assert "process_sensitive_ai_task" not in text
    assert "from app.models.ai_task" not in text
    assert "ai_tasks" not in lower or "mail_tasks" in lower
    assert "smtp" not in lower
    assert "ai_sensitive" not in lower


def test_env_example_mail_queue_var() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "CELERY_MAIL_QUEUE_NAME=" in text
    assert "smtp" not in text.lower()
