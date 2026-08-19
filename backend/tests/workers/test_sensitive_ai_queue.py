"""Tests for sensitive AI Celery queue config, routes, and Task-1 stub."""

from __future__ import annotations

import importlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings, get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = BACKEND_ROOT / ".env.example"
SENSITIVE_TASK_NAME = "app.workers.ai_tasks.process_sensitive_ai_task"
WORKER_SRC = BACKEND_ROOT / "app" / "workers" / "ai_tasks.py"


class _FakeAsyncSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc):
        return None


class _FakeWorkerEngine:
    async def dispose(self) -> None:
        return None


def _patch_worker_db_session(monkeypatch, worker_mod, *, get_task, session=None):
    """Avoid real DB for Celery entry unit tests."""
    from unittest.mock import AsyncMock, MagicMock

    if session is None:
        session = MagicMock()
        session.commit = AsyncMock()

    monkeypatch.setattr(
        worker_mod, "create_database_engine", lambda *_a, **_k: _FakeWorkerEngine()
    )
    monkeypatch.setattr(
        worker_mod,
        "create_session_factory",
        lambda *_a, **_k: (lambda: _FakeAsyncSessionCM(session)),
    )
    monkeypatch.setattr(worker_mod, "get_ai_task_by_id", get_task)
    return session


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_celery_sensitive_queue_name_default(monkeypatch) -> None:
    monkeypatch.delenv("CELERY_SENSITIVE_QUEUE_NAME", raising=False)
    settings = Settings(_env_file=None)
    assert settings is not get_settings()
    assert settings.celery_sensitive_queue_name == "ai_sensitive"


def test_celery_sensitive_queue_name_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("CELERY_SENSITIVE_QUEUE_NAME", "uat_sensitive_q")
    get_settings.cache_clear()
    assert get_settings().celery_sensitive_queue_name == "uat_sensitive_q"


def test_env_example_sensitive_queue_var_empty() -> None:
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "CELERY_SENSITIVE_QUEUE_NAME=" in text
    for line in text.splitlines():
        if line.startswith("CELERY_SENSITIVE_QUEUE_NAME="):
            assert line == "CELERY_SENSITIVE_QUEUE_NAME="
            break
    else:
        raise AssertionError("CELERY_SENSITIVE_QUEUE_NAME= line missing")
    lower = text.lower()
    assert "ai_sensitive" in lower
    assert "task_routes" in lower or "task routes" in lower or "-q" in lower
    assert "重启" in text or "restart" in lower


def test_task_routes_sensitive_uses_settings_default(monkeypatch) -> None:
    monkeypatch.delenv("CELERY_SENSITIVE_QUEUE_NAME", raising=False)
    get_settings.cache_clear()
    from app.workers import celery_app as celery_mod

    importlib.reload(celery_mod)
    routes = celery_mod.celery_app.conf.task_routes or {}
    assert SENSITIVE_TASK_NAME in routes
    assert routes[SENSITIVE_TASK_NAME]["queue"] == get_settings().celery_sensitive_queue_name
    assert routes[SENSITIVE_TASK_NAME]["queue"] == "ai_sensitive"
    assert "app.workers.ai_tasks.process_ai_task" not in routes
    assert "app.workers.ai_tasks.purge_expired_ai_raw_payloads" not in routes


def test_task_routes_sensitive_follows_override(monkeypatch) -> None:
    monkeypatch.setenv("CELERY_SENSITIVE_QUEUE_NAME", "uat_sensitive_q")
    get_settings.cache_clear()
    from app.workers import celery_app as celery_mod

    importlib.reload(celery_mod)
    routes = celery_mod.celery_app.conf.task_routes or {}
    assert routes[SENSITIVE_TASK_NAME]["queue"] == "uat_sensitive_q"
    assert routes[SENSITIVE_TASK_NAME]["queue"] == get_settings().celery_sensitive_queue_name


def test_task_queues_sensitive_name_matches_settings_if_declared(monkeypatch) -> None:
    monkeypatch.delenv("CELERY_SENSITIVE_QUEUE_NAME", raising=False)
    get_settings.cache_clear()
    from app.workers import celery_app as celery_mod

    importlib.reload(celery_mod)
    queues = celery_mod.celery_app.conf.task_queues
    if not queues:
        pytest.skip("task_queues not declared; routes-only config is allowed")
    expected = get_settings().celery_sensitive_queue_name
    names = {getattr(q, "name", q) for q in queues}
    assert expected in names


def test_process_sensitive_ai_task_registered() -> None:
    from app.workers import ai_tasks as worker_mod
    from app.workers.celery_app import celery_app

    importlib.reload(worker_mod)
    registered = celery_app.tasks.get(SENSITIVE_TASK_NAME)
    assert registered is not None
    assert hasattr(worker_mod, "process_sensitive_ai_task")


def test_process_sensitive_ai_task_stub_never_runs_process_async_or_dify(
    monkeypatch,
) -> None:
    """Task 3+: non-question sensitive entry must not enter the process chain."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models.ai_task import TASK_TYPE_RESUME_SCORE
    from app.workers import ai_tasks as worker_mod

    calls: list[str] = []
    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_RESUME_SCORE,
        status="pending",
    )

    async def _track_async(*_a, **_k):
        calls.append("_process_ai_task_async")
        raise AssertionError("_process_ai_task_async must not run")

    def _track_handle(*_a, **_k):
        calls.append("_handle_process")
        raise AssertionError("_handle_process must not run")

    async def _track_dify(*_a, **_k):
        calls.append("run_dify")
        raise AssertionError("run_dify must not run")

    monkeypatch.setattr(worker_mod, "_process_ai_task_async", _track_async)
    monkeypatch.setattr(worker_mod, "_handle_process", _track_handle)
    monkeypatch.setattr(worker_mod, "run_dify", _track_dify, raising=False)
    _patch_worker_db_session(
        monkeypatch,
        worker_mod,
        get_task=AsyncMock(return_value=task),
    )

    result = worker_mod.process_sensitive_ai_task.run(str(task.id))
    assert result["status"] == "rejected"
    assert result["reason"] == "unsupported_task_type"
    assert calls == []


def test_config_override_requires_process_restart_documented() -> None:
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    celery_src = (BACKEND_ROOT / "app" / "workers" / "celery_app.py").read_text(
        encoding="utf-8"
    )
    combined = f"{text}\n{celery_src}".lower()
    assert "重启" in text or "restart" in combined
    assert "worker" in combined


# --- Task 2: service-layer sensitive enqueue / dispatch / retry ---


def test_enqueue_sensitive_question_task_targets_sensitive_celery_name(
    monkeypatch,
) -> None:
    from app.services import ai_tasks as ai_tasks_service
    from app.workers import ai_tasks as worker_mod

    called: list[tuple] = []

    def fake_apply_async(*, args, countdown=0, **_kwargs):
        called.append((args, countdown))

    monkeypatch.setattr(
        worker_mod.process_sensitive_ai_task, "apply_async", fake_apply_async
    )
    default_calls: list = []
    monkeypatch.setattr(
        worker_mod.process_ai_task,
        "apply_async",
        lambda **_k: default_calls.append(True),
    )

    task_id = uuid4()
    ai_tasks_service.enqueue_sensitive_question_task(task_id, countdown=7)
    assert called == [([str(task_id)], 7)]
    assert default_calls == []


def test_enqueue_ai_task_still_targets_default_process(monkeypatch) -> None:
    from app.services import ai_tasks as ai_tasks_service
    from app.workers import ai_tasks as worker_mod

    called: list[tuple] = []

    def fake_apply_async(*, args, countdown=0, **_kwargs):
        called.append((args, countdown))

    monkeypatch.setattr(worker_mod.process_ai_task, "apply_async", fake_apply_async)
    sensitive_calls: list = []
    monkeypatch.setattr(
        worker_mod.process_sensitive_ai_task,
        "apply_async",
        lambda **_k: sensitive_calls.append(True),
    )

    task_id = uuid4()
    ai_tasks_service.enqueue_ai_task(task_id, countdown=3)
    assert called == [([str(task_id)], 3)]
    assert sensitive_calls == []


def test_dispatch_persisted_question_generation_uses_sensitive_enqueue() -> None:
    import inspect

    from app.services.interview_questions import (
        dispatch_persisted_question_generation_task,
        request_question_generation,
    )

    request_source = inspect.getsource(request_question_generation)
    dispatch_source = inspect.getsource(dispatch_persisted_question_generation_task)
    assert "enqueue_ai_task" not in request_source
    assert "enqueue_sensitive_question_task" in dispatch_source
    assert "enqueue_ai_task" not in dispatch_source


@pytest.mark.asyncio
async def test_retry_ai_task_question_generate_uses_sensitive_enqueue(
    monkeypatch,
) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.models.ai_task import (
        AI_TASK_STATUS_FAILED,
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    )
    from app.services import ai_tasks as svc
    from app.services.audit import RequestContext

    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        status=AI_TASK_STATUS_FAILED,
        business_id=uuid4(),
        retry_cycle_no=0,
        cycle_attempt_count=2,
        attempt_count=2,
        error_code="x",
        error_message="y",
        error_category="retryable",
        result_payload={"a": 1},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    sensitive = MagicMock()
    default = MagicMock()
    monkeypatch.setattr(svc, "enqueue_sensitive_question_task", sensitive)
    monkeypatch.setattr(svc, "enqueue_ai_task", default)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=task))
    monkeypatch.setattr(
        svc,
        "to_ai_task_out",
        lambda t, **_k: SimpleNamespace(id=t.id, task_type=t.task_type, status=t.status),
    )

    actor = SimpleNamespace(id=uuid4())
    await svc.retry_ai_task(
        session,
        task_id=task.id,
        actor=actor,
        request_context=RequestContext(request_id="test-retry"),
    )
    sensitive.assert_called_once_with(task.id)
    default.assert_not_called()


@pytest.mark.asyncio
async def test_retry_ai_task_resume_or_jd_still_uses_default_enqueue(
    monkeypatch,
) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.models.ai_task import AI_TASK_STATUS_FAILED, TASK_TYPE_RESUME_SCORE
    from app.services import ai_tasks as svc
    from app.services.audit import RequestContext

    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_RESUME_SCORE,
        status=AI_TASK_STATUS_FAILED,
        business_id=uuid4(),
        retry_cycle_no=0,
        cycle_attempt_count=1,
        attempt_count=1,
        error_code=None,
        error_message=None,
        error_category=None,
        result_payload=None,
        started_at=None,
        finished_at=None,
        updated_at=datetime.now(UTC),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    sensitive = MagicMock()
    default = MagicMock()
    monkeypatch.setattr(svc, "enqueue_sensitive_question_task", sensitive)
    monkeypatch.setattr(svc, "enqueue_ai_task", default)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=task))
    monkeypatch.setattr(
        svc,
        "to_ai_task_out",
        lambda t, **_k: SimpleNamespace(id=t.id, task_type=t.task_type, status=t.status),
    )

    await svc.retry_ai_task(
        session,
        task_id=task.id,
        actor=SimpleNamespace(id=uuid4()),
        request_context=RequestContext(request_id="test-retry-default"),
    )
    default.assert_called_once_with(task.id)
    sensitive.assert_not_called()


def test_no_arbitrary_task_id_execute_endpoint_added() -> None:
    endpoints = BACKEND_ROOT / "app" / "api" / "v1" / "endpoints"
    forbidden_snippets = (
        "/{task_id}/execute",
        "/{task_id}/run",
        '"/execute"',
        "'/execute'",
        '"/run"',
        "'/run'",
        '"/dispatch"',
        "'/dispatch'",
    )
    for name in ("interview_ai.py", "admin_ai_tasks.py", "ai_tasks.py"):
        text = (endpoints / name).read_text(encoding="utf-8")
        for snippet in forbidden_snippets:
            assert snippet not in text, f"{name} must not add {snippet}"


# --- Task 3: worker gate / reroute / auto-retry ---


def test_process_sensitive_rejects_non_question_without_handle(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models.ai_task import TASK_TYPE_RESUME_SCORE
    from app.workers import ai_tasks as worker_mod

    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_RESUME_SCORE,
        status="pending",
    )
    process_calls: list = []
    handle_calls: list = []

    async def track_process(*_a, **_k):
        process_calls.append(True)
        raise AssertionError("must not process")

    async def track_handle(*_a, **_k):
        handle_calls.append(True)
        raise AssertionError("must not handle")

    monkeypatch.setattr(worker_mod, "_process_ai_task_async", track_process)
    monkeypatch.setattr(worker_mod, "_handle_process", track_handle)
    _patch_worker_db_session(
        monkeypatch, worker_mod, get_task=AsyncMock(return_value=task)
    )

    result = worker_mod.process_sensitive_ai_task.run(str(task.id))
    assert result == {
        "status": "rejected",
        "reason": "unsupported_task_type",
        "task_type": TASK_TYPE_RESUME_SCORE,
    }
    assert process_calls == []
    assert handle_calls == []


def test_process_sensitive_question_calls_process_async_once(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models.ai_task import TASK_TYPE_INTERVIEW_QUESTION_GENERATE
    from app.workers import ai_tasks as worker_mod

    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        status="pending",
    )
    calls: list = []

    async def fake_process(task_id):
        calls.append(task_id)
        return {"status": "ok", "via": "process_async"}

    monkeypatch.setattr(worker_mod, "_process_ai_task_async", fake_process)
    _patch_worker_db_session(
        monkeypatch, worker_mod, get_task=AsyncMock(return_value=task)
    )

    result = worker_mod.process_sensitive_ai_task.run(str(task.id))
    assert result == {"status": "ok", "via": "process_async"}
    assert calls == [task.id]


def test_process_ai_task_reroutes_question_once(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models.ai_task import (
        AI_TASK_STATUS_PENDING,
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    )
    from app.workers import ai_tasks as worker_mod

    task_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        status=AI_TASK_STATUS_PENDING,
    )
    apply_calls: list[tuple] = []
    handle_calls: list = []
    dify_calls: list = []
    enqueue_calls: list = []

    def fake_sensitive_apply(*, args, countdown=0, **_k):
        apply_calls.append((args, countdown))

    async def track_handle(*_a, **_k):
        handle_calls.append(True)
        raise AssertionError("_handle_process must not run on reroute")

    async def track_dify(*_a, **_k):
        dify_calls.append(True)
        raise AssertionError("run_dify must not run")

    def track_enqueue(*_a, **_k):
        enqueue_calls.append(True)
        raise AssertionError("services enqueue must not run")

    monkeypatch.setattr(
        worker_mod.process_sensitive_ai_task, "apply_async", fake_sensitive_apply
    )
    monkeypatch.setattr(worker_mod, "_handle_process", track_handle)
    monkeypatch.setattr(worker_mod, "run_dify", track_dify, raising=False)
    monkeypatch.setattr(
        "app.services.ai_tasks.enqueue_sensitive_question_task",
        track_enqueue,
        raising=False,
    )
    _patch_worker_db_session(
        monkeypatch, worker_mod, get_task=AsyncMock(return_value=task)
    )

    result = worker_mod.process_ai_task.run(str(task_id))
    assert result == {
        "status": "rerouted",
        "reason": "question_generate_requires_sensitive_queue",
        "task_id": str(task_id),
    }
    assert apply_calls == [([str(task_id)], 0)]
    assert task.status == AI_TASK_STATUS_PENDING
    assert handle_calls == []
    assert dify_calls == []
    assert enqueue_calls == []


def test_process_ai_task_reroute_failure_keeps_pending_and_audits(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models.ai_task import (
        AI_TASK_STATUS_PENDING,
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    )
    from app.workers import ai_tasks as worker_mod

    task_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        status=AI_TASK_STATUS_PENDING,
    )
    audits: list[dict] = []

    def boom(*, args, countdown=0, **_k):
        raise RuntimeError("broker unavailable")

    async def fake_audit(session, **kwargs):
        audits.append(kwargs)

    async def track_handle(*_a, **_k):
        raise AssertionError("_handle_process must not run")

    monkeypatch.setattr(worker_mod.process_sensitive_ai_task, "apply_async", boom)
    monkeypatch.setattr(worker_mod, "record_audit", fake_audit)
    monkeypatch.setattr(worker_mod, "_handle_process", track_handle)
    session = _patch_worker_db_session(
        monkeypatch, worker_mod, get_task=AsyncMock(return_value=task)
    )

    result = worker_mod.process_ai_task.run(str(task_id))
    assert result["status"] == "reroute_failed"
    assert task.status == AI_TASK_STATUS_PENDING
    session.commit.assert_awaited()
    assert len(audits) == 1
    audit = audits[0]
    assert audit["action"] == "ai_task.sensitive_reroute_failed"
    assert audit["result"] == "failure"
    assert audit["resource_type"] == "ai_task"
    assert audit["actor_user_id"] is None
    assert audit["resource_id"] == str(task_id)
    assert audit["request_context"].request_id == f"ai-task:{task_id}"
    assert set(audit["changes"].keys()) <= {"ai_task_id", "task_type", "error_type"}
    assert audit["changes"]["ai_task_id"] == str(task_id)
    assert audit["changes"]["task_type"] == TASK_TYPE_INTERVIEW_QUESTION_GENERATE
    assert audit["changes"]["error_type"] == "RuntimeError"


def test_process_sensitive_never_apply_async_back_to_default() -> None:
    import inspect

    from app.workers import ai_tasks as worker_mod

    helpers = [inspect.getsource(worker_mod.process_sensitive_ai_task)]
    helper = getattr(worker_mod, "_process_sensitive_ai_task_async", None)
    if helper is not None:
        helpers.append(inspect.getsource(helper))
    combined = "\n".join(helpers)
    assert "process_ai_task.apply_async" not in combined


def test_enqueue_retry_for_task_question_uses_sensitive(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.models.ai_task import (
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_RESUME_SCORE,
    )
    from app.workers import ai_tasks as worker_mod

    sensitive_calls: list[tuple] = []
    default_calls: list[tuple] = []

    monkeypatch.setattr(
        worker_mod.process_sensitive_ai_task,
        "apply_async",
        lambda *, args, countdown=0, **_k: sensitive_calls.append((args, countdown)),
    )
    monkeypatch.setattr(
        worker_mod.process_ai_task,
        "apply_async",
        lambda *, args, countdown=0, **_k: default_calls.append((args, countdown)),
    )

    q_task = SimpleNamespace(id=uuid4(), task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE)
    other = SimpleNamespace(id=uuid4(), task_type=TASK_TYPE_RESUME_SCORE)

    worker_mod._enqueue_retry_for_task(q_task, countdown=11)
    worker_mod._enqueue_retry_for_task(other, countdown=5)

    assert sensitive_calls == [([str(q_task.id)], 11)]
    assert default_calls == [([str(other.id)], 5)]


@pytest.mark.asyncio
async def test_handle_process_question_auto_retry_does_not_use_default_entry(
    monkeypatch,
) -> None:
    from app.models.ai_task import (
        AI_TASK_STATUS_PENDING,
        ERROR_CATEGORY_RETRYABLE,
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    )
    from app.services.ai_providers.base import ProviderOutcome
    from app.workers import ai_tasks as worker_mod
    from tests.workers.test_interview_ai_worker import (
        FakeWorkerSession,
        _bind_stage8_mocks,
        _frozen_question_snapshot,
        _make_task,
    )

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    sensitive_calls: list = []
    default_calls: list = []

    outcome = ProviderOutcome(
        ok=False,
        raw_request={"provider": "mock"},
        raw_response={"error": "flaky"},
        error_code="provider_5xx",
        error_message="flaky",
        error_category=ERROR_CATEGORY_RETRYABLE,
        http_status=502,
    )
    await _bind_stage8_mocks(monkeypatch, task=task, outcome=outcome)
    monkeypatch.setattr(
        worker_mod.process_sensitive_ai_task,
        "apply_async",
        lambda *, args, countdown=0, **_k: sensitive_calls.append((args, countdown)),
    )
    monkeypatch.setattr(
        worker_mod.process_ai_task,
        "apply_async",
        lambda *, args, countdown=0, **_k: default_calls.append((args, countdown)),
    )

    result = await worker_mod._handle_process(session, task.id)
    assert result["status"] == AI_TASK_STATUS_PENDING
    assert sensitive_calls
    assert default_calls == []


def test_worker_module_has_no_toplevel_services_ai_tasks_import() -> None:
    text = WORKER_SRC.read_text(encoding="utf-8")
    # Only inspect import region before first function/class (top-level imports).
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            break
        if stripped.startswith("class "):
            break
        lines.append(line)
    header = "\n".join(lines)
    assert "from app.services.ai_tasks import" not in header
    assert "import app.services.ai_tasks" not in header
