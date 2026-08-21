"""Offer draft/version service tests (Task 2) — RED then GREEN."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    APPLICATION_STATUS_REJECTED,
)
from app.models.offer import (
    OFFER_STATUS_DRAFT,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_READY,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
    OFFER_STATUS_VOIDED,
    Offer,
    OfferSendAttempt,
    OfferVersion,
)
from app.models.resume import (
    HIRING_RECOMMEND_HIRE,
    HIRING_REJECT,
    PIPELINE_INTERVIEWING,
    PIPELINE_PENDING_OFFER,
    PIPELINE_REJECTED,
)
from app.services.audit import RequestContext
from app.services.crypto import CIPHER_PREFIX, encrypt_secret

MODULE = "app.services.offers"

ALLOWED_AUDIT_KEYS = frozenset(
    {
        "application_id",
        "offer_id",
        "hiring_decision_id",
        "lock_version",
        "version_no",
        "version_id",
        "content_hash",
        "status",
        "from_status",
        "to_status",
        "recipient_email_masked",
        "idempotency_key",
        "void_reason_code",
    }
)


def _actor(user_id=None):
    return SimpleNamespace(
        id=user_id or uuid4(),
        username="hr",
        permission_codes=["recruitment.manage"],
    )


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-offer-1", ip_address="127.0.0.1")


def _now() -> datetime:
    return datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def _candidate(*, email: str | None = "zhang@example.com", name: str = "张三"):
    return SimpleNamespace(id=uuid4(), name=name, email=email, phone="13800000000")


def _application(
    *,
    pipeline: str = PIPELINE_PENDING_OFFER,
    status: str = APPLICATION_STATUS_IN_PROGRESS,
    candidate=None,
    lock_version: int = 1,
):
    cand = candidate or _candidate()
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        pipeline_status=pipeline,
        lock_version=lock_version,
        candidate=cand,
        candidate_id=cand.id,
        close_action=None,
        close_reason=None,
        updated_at=_now(),
    )


def _decision(*, application_id, decision=HIRING_RECOMMEND_HIRE, created_at=None):
    return SimpleNamespace(
        id=uuid4(),
        application_id=application_id,
        decision=decision,
        created_at=created_at or _now(),
    )


def _content_hash(subject: str, body_html: str, body_text: str) -> str:
    payload = json.dumps(
        {"subject": subject, "body_html": body_html, "body_text": body_text},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _patch_common(monkeypatch, *, application, decisions, active_offer=None):
    from app.services import offers as svc

    audits: list[dict] = []
    added: list[object] = []
    idempotency_rows: list = []

    async def fake_add_offer(_session, row):
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        added.append(row)
        return row

    async def fake_add_version(_session, row):
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        added.append(row)
        return row

    async def fake_record_audit(_session, **kwargs):
        audits.append(kwargs)

    async def fake_add_idempotency(_session, row):
        idempotency_rows.append(row)
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        return row

    async def fake_find_idempotency(**_kwargs):
        return None

    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc, "get_application_by_id", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc,
        "list_recommend_hire_decisions",
        AsyncMock(return_value=decisions),
    )
    monkeypatch.setattr(
        svc, "find_active_offer_for_application", AsyncMock(return_value=active_offer)
    )
    monkeypatch.setattr(svc, "add_offer", fake_add_offer)
    monkeypatch.setattr(svc, "add_offer_version", fake_add_version)
    monkeypatch.setattr(svc, "record_audit", fake_record_audit)
    monkeypatch.setattr(svc, "add_idempotency", fake_add_idempotency)
    monkeypatch.setattr(svc, "find_idempotency", fake_find_idempotency)

    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session, added, audits, idempotency_rows, svc


@pytest.mark.asyncio
async def test_create_requires_pending_offer_in_progress_recommend_hire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    # interviewing → state error
    app_iv = _application(pipeline=PIPELINE_INTERVIEWING)
    session, *_rest, _svc = _patch_common(
        monkeypatch, application=app_iv, decisions=[_decision(application_id=app_iv.id)]
    )
    with pytest.raises(svc.OfferStateError):
        await svc.create_offer(
            session, application_id=app_iv.id, actor=_actor(), request_context=_ctx()
        )

    # no recommend_hire → state/validation error
    app_po = _application()
    session, added, audits, _, svc = _patch_common(
        monkeypatch,
        application=app_po,
        decisions=[_decision(application_id=app_po.id, decision=HIRING_REJECT)],
    )
    monkeypatch.setattr(
        svc, "list_recommend_hire_decisions", AsyncMock(return_value=[])
    )
    with pytest.raises((svc.OfferStateError, svc.OfferValidationError)):
        await svc.create_offer(
            session, application_id=app_po.id, actor=_actor(), request_context=_ctx()
        )

    # rejected application
    app_rej = _application(status=APPLICATION_STATUS_REJECTED, pipeline=PIPELINE_REJECTED)
    session, *_r, svc = _patch_common(
        monkeypatch,
        application=app_rej,
        decisions=[_decision(application_id=app_rej.id)],
    )
    with pytest.raises(svc.OfferStateError):
        await svc.create_offer(
            session, application_id=app_rej.id, actor=_actor(), request_context=_ctx()
        )

    # happy path
    app_ok = _application()
    decision = _decision(application_id=app_ok.id)
    session, added, audits, _, svc = _patch_common(
        monkeypatch, application=app_ok, decisions=[decision]
    )
    result = await svc.create_offer(
        session, application_id=app_ok.id, actor=_actor(), request_context=_ctx()
    )
    assert result.status == OFFER_STATUS_DRAFT
    assert result.hiring_decision_id == decision.id
    offers = [x for x in added if isinstance(x, Offer)]
    versions = [x for x in added if isinstance(x, OfferVersion)]
    attempts = [x for x in added if isinstance(x, OfferSendAttempt)]
    assert len(offers) == 1
    assert len(versions) == 1
    assert attempts == []
    assert versions[0].frozen is False
    assert audits and audits[0]["action"] == "offer.created"
    assert set(audits[0]["changes"].keys()) <= ALLOWED_AUDIT_KEYS


@pytest.mark.asyncio
async def test_create_requires_maskable_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    app = _application(candidate=_candidate(email=None))
    session, added, _, _, svc = _patch_common(
        monkeypatch,
        application=app,
        decisions=[_decision(application_id=app.id)],
    )
    with pytest.raises(svc.OfferValidationError, match="(?i)email"):
        await svc.create_offer(
            session, application_id=app.id, actor=_actor(), request_context=_ctx()
        )
    assert added == []

    app2 = _application(candidate=_candidate(email="alice@corp.example"))
    session, added, audits, _, svc = _patch_common(
        monkeypatch,
        application=app2,
        decisions=[_decision(application_id=app2.id)],
    )
    result = await svc.create_offer(
        session, application_id=app2.id, actor=_actor(), request_context=_ctx()
    )
    assert result.recipient_email_masked == "a***@corp.example"
    assert "alice@corp.example" not in str(audits)
    assert "alice@corp.example" not in str(result)


@pytest.mark.asyncio
async def test_create_binds_latest_recommend_hire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    app = _application()
    older = _decision(
        application_id=app.id,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    newer = _decision(
        application_id=app.id,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    session, _, _, _, svc = _patch_common(
        monkeypatch, application=app, decisions=[older, newer]
    )
    # service should pick latest by created_at desc; repo returns sorted
    monkeypatch.setattr(
        svc,
        "list_recommend_hire_decisions",
        AsyncMock(return_value=[newer, older]),
    )
    result = await svc.create_offer(
        session, application_id=app.id, actor=_actor(), request_context=_ctx()
    )
    assert result.hiring_decision_id == newer.id


@pytest.mark.asyncio
async def test_second_active_offer_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    app = _application()
    existing = SimpleNamespace(
        id=uuid4(),
        application_id=app.id,
        status=OFFER_STATUS_DRAFT,
    )
    session, added, _, _, svc = _patch_common(
        monkeypatch,
        application=app,
        decisions=[_decision(application_id=app.id)],
        active_offer=existing,
    )
    with pytest.raises(svc.OfferConflictError):
        await svc.create_offer(
            session, application_id=app.id, actor=_actor(), request_context=_ctx()
        )
    assert added == []


@pytest.mark.asyncio
async def test_update_encrypts_body_and_sets_content_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_DRAFT,
        lock_version=1,
        current_version_id=version_id,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        hiring_decision_id=uuid4(),
        updated_by=None,
        updated_at=_now(),
        created_at=_now(),
    )
    old_version = SimpleNamespace(
        id=version_id,
        offer_id=offer_id,
        version_no=1,
        frozen=False,
        subject_encrypted=encrypt_secret("old"),
        body_html_encrypted=encrypt_secret("<p>old</p>"),
        body_text_encrypted=encrypt_secret("old"),
        content_hash="abc",
    )
    added: list = []

    async def fake_add_version(_session, row):
        row.id = uuid4()
        added.append(row)
        return row

    audits: list = []
    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(
        svc, "get_offer_version", AsyncMock(return_value=old_version)
    )
    monkeypatch.setattr(
        svc,
        "get_application_by_id_for_update",
        AsyncMock(
            return_value=_application(
                candidate=_candidate(email="zhang@example.com")
            )
        ),
    )
    monkeypatch.setattr(svc, "add_offer_version", fake_add_version)
    monkeypatch.setattr(
        svc, "record_audit", AsyncMock(side_effect=lambda *a, **k: audits.append(k))
    )
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())
    monkeypatch.setattr(svc, "next_version_no", AsyncMock(return_value=2))

    session = AsyncMock()
    session.commit = AsyncMock()
    subject = "录用通知"
    body_html = "<p>欢迎加入</p>"
    body_text = "欢迎加入"
    result = await svc.update_offer_draft(
        session,
        offer_id=offer_id,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        lock_version=1,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="upd-1",
    )
    assert len(added) == 1
    ver = added[0]
    assert isinstance(ver, OfferVersion)
    assert ver.subject_encrypted != subject
    assert ver.subject_encrypted.startswith(CIPHER_PREFIX)
    assert subject not in ver.subject_encrypted
    assert body_html not in ver.body_html_encrypted
    assert ver.content_hash == _content_hash(subject, body_html, body_text)
    assert ver.frozen is False
    assert ver.version_no == 2
    assert offer.current_version_id == ver.id
    assert offer.lock_version == 2
    assert result.content_hash == ver.content_hash
    assert set(audits[0]["changes"].keys()) <= ALLOWED_AUDIT_KEYS
    assert subject not in str(audits)


@pytest.mark.asyncio
async def test_list_summary_has_no_body_or_plaintext_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    app_id = uuid4()
    row = SimpleNamespace(
        id=uuid4(),
        application_id=app_id,
        status=OFFER_STATUS_DRAFT,
        recipient_email_masked="a***@example.com",
        recipient_name="A",
        lock_version=1,
        hiring_decision_id=uuid4(),
        current_version_id=uuid4(),
        created_at=_now(),
        updated_at=_now(),
    )
    version = SimpleNamespace(
        id=row.current_version_id,
        version_no=1,
        content_hash="hash1",
        frozen=False,
        subject_encrypted=encrypt_secret("SECRET SUBJECT"),
        body_html_encrypted=encrypt_secret("<p>SECRET</p>"),
        body_text_encrypted=encrypt_secret("SECRET"),
    )
    monkeypatch.setattr(svc, "get_application_by_id", AsyncMock(return_value=_application()))
    monkeypatch.setattr(
        svc, "list_offers_by_application", AsyncMock(return_value=[row])
    )
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))

    items = await svc.list_offers_for_application(AsyncMock(), application_id=app_id)
    assert len(items) == 1
    summary = items[0]
    data = summary.__dict__ if hasattr(summary, "__dict__") else dict(summary)
    blob = str(data).lower()
    assert "subject" not in data or data.get("subject") is None
    assert "body_html" not in data
    assert "body_text" not in data
    assert "secret" not in blob
    assert "@example.com" in (summary.recipient_email_masked or "")
    assert "alice@" not in blob and "a***@" in (summary.recipient_email_masked or "")


@pytest.mark.asyncio
async def test_detail_decrypts_for_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_DRAFT,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        lock_version=2,
        hiring_decision_id=uuid4(),
        current_version_id=version_id,
        created_at=_now(),
        updated_at=_now(),
        voided_at=None,
        void_reason_code=None,
    )
    version = SimpleNamespace(
        id=version_id,
        offer_id=offer_id,
        version_no=2,
        frozen=False,
        subject_encrypted=encrypt_secret("预览主题"),
        body_html_encrypted=encrypt_secret("<p>预览正文</p>"),
        body_text_encrypted=encrypt_secret("预览正文"),
        content_hash="h",
        template_code="offer_console_v1",
        template_version="1",
        created_at=_now(),
    )
    monkeypatch.setattr(svc, "get_offer_by_id", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))

    detail = await svc.get_offer_detail(AsyncMock(), offer_id=offer_id)
    assert detail.subject == "预览主题"
    assert detail.body_html == "<p>预览正文</p>"
    assert detail.body_text == "预览正文"
    assert detail.recipient_email_masked == "z***@example.com"


@pytest.mark.asyncio
async def test_frozen_version_rejects_inplace_update_creates_new_when_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen current version must not be mutated; draft update inserts new version."""
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_DRAFT,
        lock_version=1,
        current_version_id=version_id,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        hiring_decision_id=uuid4(),
        updated_by=None,
        updated_at=_now(),
        created_at=_now(),
    )
    frozen_subject = encrypt_secret("frozen")
    frozen = SimpleNamespace(
        id=version_id,
        offer_id=offer_id,
        version_no=1,
        frozen=True,
        subject_encrypted=frozen_subject,
        body_html_encrypted=encrypt_secret("<p>f</p>"),
        body_text_encrypted=encrypt_secret("f"),
        content_hash="old",
    )
    added: list = []

    async def fake_add_version(_session, row):
        row.id = uuid4()
        added.append(row)
        return row

    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=frozen))
    monkeypatch.setattr(
        svc,
        "get_application_by_id_for_update",
        AsyncMock(return_value=_application()),
    )
    monkeypatch.setattr(svc, "add_offer_version", fake_add_version)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())
    monkeypatch.setattr(svc, "next_version_no", AsyncMock(return_value=2))

    session = AsyncMock()
    session.commit = AsyncMock()
    await svc.update_offer_draft(
        session,
        offer_id=offer_id,
        subject="new",
        body_html="<p>new</p>",
        body_text="new",
        lock_version=1,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="upd-frozen",
    )
    assert frozen.subject_encrypted is frozen_subject
    assert frozen.frozen is True
    assert len(added) == 1
    assert added[0].version_no == 2
    assert added[0].frozen is False


@pytest.mark.asyncio
async def test_no_send_attempt_created_in_draft_flows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    app = _application()
    session, added, _, _, svc_mod = _patch_common(
        monkeypatch,
        application=app,
        decisions=[_decision(application_id=app.id)],
    )
    await svc_mod.create_offer(
        session, application_id=app.id, actor=_actor(), request_context=_ctx()
    )
    assert not any(isinstance(x, OfferSendAttempt) for x in added)
    # Draft create path must not instantiate send attempts (confirm/send is separate).
    create_src = open(svc.__file__, encoding="utf-8").read()
    assert "async def create_offer" in create_src
    assert "OfferSendAttempt(" in create_src  # used by confirm/retry only
    # Ensure create_offer body before confirm does not add attempts: covered by added==[]


@pytest.mark.asyncio
async def test_ready_and_sent_versions_require_new_version_not_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    # sending/sent → reject edit
    for status in (OFFER_STATUS_SENDING, OFFER_STATUS_SENT):
        offer = SimpleNamespace(
            id=uuid4(),
            application_id=uuid4(),
            status=status,
            lock_version=1,
            current_version_id=uuid4(),
            recipient_email_masked="z***@example.com",
            recipient_name="张三",
            hiring_decision_id=uuid4(),
        )
        monkeypatch.setattr(
            svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer)
        )
        monkeypatch.setattr(
            svc,
            "get_application_by_id_for_update",
            AsyncMock(return_value=_application()),
        )
        monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
        with pytest.raises(svc.OfferStateError):
            await svc.update_offer_draft(
                AsyncMock(),
                offer_id=offer.id,
                subject="x",
                body_html="<p>x</p>",
                body_text="x",
                lock_version=1,
                actor=_actor(),
                request_context=_ctx(),
                idempotency_key="bad",
            )

    # ready → new version + back to draft
    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_READY,
        lock_version=3,
        current_version_id=version_id,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        hiring_decision_id=uuid4(),
        updated_by=None,
        updated_at=_now(),
        created_at=_now(),
    )
    ready_ver = SimpleNamespace(
        id=version_id,
        offer_id=offer_id,
        version_no=1,
        frozen=True,
        subject_encrypted=encrypt_secret("ready"),
        body_html_encrypted=encrypt_secret("<p>r</p>"),
        body_text_encrypted=encrypt_secret("r"),
        content_hash="h",
    )
    added: list = []

    async def fake_add_version(_session, row):
        row.id = uuid4()
        added.append(row)
        return row

    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=ready_ver))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=_application())
    )
    monkeypatch.setattr(svc, "add_offer_version", fake_add_version)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())
    monkeypatch.setattr(svc, "next_version_no", AsyncMock(return_value=2))
    session = AsyncMock()
    session.commit = AsyncMock()
    await svc.update_offer_draft(
        session,
        offer_id=offer_id,
        subject="rev",
        body_html="<p>rev</p>",
        body_text="rev",
        lock_version=3,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="ready-edit",
    )
    assert offer.status == OFFER_STATUS_DRAFT
    assert ready_ver.frozen is True
    assert len(added) == 1


@pytest.mark.asyncio
async def test_mark_ready_and_void_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_DRAFT,
        lock_version=2,
        current_version_id=version_id,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        hiring_decision_id=uuid4(),
        updated_by=None,
        updated_at=_now(),
        created_at=_now(),
        voided_at=None,
        void_reason_code=None,
    )
    version = SimpleNamespace(
        id=version_id,
        offer_id=offer_id,
        version_no=2,
        frozen=False,
        subject_encrypted=encrypt_secret("主题"),
        body_html_encrypted=encrypt_secret("<p>正文</p>"),
        body_text_encrypted=encrypt_secret("正文"),
        content_hash=_content_hash("主题", "<p>正文</p>", "正文"),
    )
    audits: list = []
    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=_application())
    )
    monkeypatch.setattr(
        svc, "record_audit", AsyncMock(side_effect=lambda *a, **k: audits.append(k))
    )
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())
    session = AsyncMock()
    session.commit = AsyncMock()

    ready = await svc.mark_offer_ready(
        session,
        offer_id=offer_id,
        lock_version=2,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="ready-1",
    )
    assert ready.status == OFFER_STATUS_READY
    assert version.frozen is True
    assert offer.lock_version == 3
    assert audits[-1]["action"] == "offer.marked_ready"
    assert set(audits[-1]["changes"].keys()) <= ALLOWED_AUDIT_KEYS
    assert "主题" not in str(audits)

    # void ready
    offer.status = OFFER_STATUS_READY
    offer.lock_version = 3
    voided = await svc.void_offer(
        session,
        offer_id=offer_id,
        void_reason_code="withdrawn",
        lock_version=3,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="void-1",
    )
    assert voided.status == OFFER_STATUS_VOIDED
    assert offer.status == OFFER_STATUS_VOIDED

    # sending cannot void
    offer.status = OFFER_STATUS_SENDING
    offer.lock_version = 4
    with pytest.raises(svc.OfferStateError):
        await svc.void_offer(
            session,
            offer_id=offer_id,
            void_reason_code="x",
            lock_version=4,
            actor=_actor(),
            request_context=_ctx(),
            idempotency_key="void-bad",
        )

    # failed can void
    offer.status = OFFER_STATUS_FAILED
    offer.lock_version = 5
    await svc.void_offer(
        session,
        offer_id=offer_id,
        void_reason_code="give_up",
        lock_version=5,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="void-failed",
    )
    assert offer.status == OFFER_STATUS_VOIDED


@pytest.mark.asyncio
async def test_lock_version_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer = SimpleNamespace(
        id=uuid4(),
        application_id=uuid4(),
        status=OFFER_STATUS_DRAFT,
        lock_version=5,
        current_version_id=uuid4(),
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        hiring_decision_id=uuid4(),
    )
    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=_application())
    )
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    with pytest.raises(svc.OfferConflictError):
        await svc.update_offer_draft(
            AsyncMock(),
            offer_id=offer.id,
            subject="s",
            body_html="<p>s</p>",
            body_text="s",
            lock_version=4,
            actor=_actor(),
            request_context=_ctx(),
            idempotency_key="lock-bad",
        )


@pytest.mark.asyncio
async def test_create_idempotency_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc
    from app.models.interview import InterviewIdempotencyKey

    app = _application()
    decision = _decision(application_id=app.id)
    existing_offer_id = uuid4()
    existing = SimpleNamespace(
        id=existing_offer_id,
        application_id=app.id,
        status=OFFER_STATUS_DRAFT,
        hiring_decision_id=decision.id,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        lock_version=1,
        current_version_id=uuid4(),
        created_at=_now(),
        updated_at=_now(),
    )
    version = SimpleNamespace(
        id=existing.current_version_id,
        version_no=1,
        content_hash="h",
        frozen=False,
    )

    session, added, _, _, svc = _patch_common(
        monkeypatch, application=app, decisions=[decision]
    )
    idem = InterviewIdempotencyKey(
        actor_id=uuid4(),
        action="offer.create",
        scope_id=app.id,
        idempotency_key="create-1",
        request_hash="will-be-ignored-if-we-match",
    )
    # First call stores; second hits idempotency — simulate hit returning existing
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=idem))
    monkeypatch.setattr(
        svc, "find_active_offer_for_application", AsyncMock(return_value=existing)
    )
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    # Force hash match by patching consume path: set request_hash after first compute
    # Easier: make find return None first path via side_effect

    # Re-test properly: first create succeeds, second with same key returns same id
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(
        svc, "find_active_offer_for_application", AsyncMock(return_value=None)
    )
    first = await svc.create_offer(
        session,
        application_id=app.id,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="create-1",
    )
    created_id = first.id

    # second: idempotency hit + active offer
    idem.request_hash = None  # service will compare — patch _canonical or consume

    async def consume_hit(*args, **kwargs):
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(svc, "_consume_idempotency", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(
        svc, "find_active_offer_for_application", AsyncMock(return_value=existing)
    )
    monkeypatch.setattr(svc, "get_offer_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    second = await svc.create_offer(
        session,
        application_id=app.id,
        actor=_actor(),
        request_context=_ctx(),
        idempotency_key="create-1",
    )
    assert second.id == existing_offer_id
