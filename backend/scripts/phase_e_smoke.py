"""Phase E live DB smoke: A→D happy path against local Postgres."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.session import create_database_engine, create_session_factory
from app.core.config import get_settings
from app.models import User
from app.models.ai_task import TASK_TYPE_JD_PARSE
from app.schemas.candidate import CreateCandidateRequest, ResolveCloseRequest
from app.schemas.job import (
    CreateJobRequest,
    PublishJobRequest,
    SaveDraftRequest,
    ScoreDimension,
    StructuredJd,
)
from app.services import candidates as cand_svc
from app.services import jobs as jobs_svc
from app.services.ai_providers.mock import run_mock
from app.services.audit import RequestContext

CTX = RequestContext(request_id="phase-e", ip_address="127.0.0.1")


async def main() -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    results: list[tuple] = []

    async with session_factory() as session:
        user = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if user is None:
            raise SystemExit("FAIL: no user; run bootstrap_admin first")

        created = await jobs_svc.create_job(
            session,
            payload=CreateJobRequest(
                name="PhaseE回归岗位",
                department="软件组",
                level="P5",
                headcount=1,
                location="深圳",
                owner_name=user.display_name or user.username,
                urgency="normal",
                raw_jd_text="岗位职责\n- 做回归\n任职要求\n- 会测试",
                structured_jd=StructuredJd(
                    responsibilities=["职责A", "职责B", "职责C", "职责D"],
                    requirements=["要求1", "要求2", "要求3"],
                    must_have=["本科"],
                    nice_to_have=["英语"],
                    skills=["Python", "测试"],
                ),
                score_dimensions=[
                    ScoreDimension(
                        name="专业",
                        weight=60,
                        description="专业能力",
                        anchors=["1", "2", "3", "4", "5"],
                    ),
                    ScoreDimension(
                        name="协作",
                        weight=40,
                        description="协作",
                        anchors=["1", "2", "3", "4", "5"],
                    ),
                ],
            ),
            actor=user,
            request_context=CTX,
        )
        job_id = created.id
        results.append(("create", created.status, created.code))

        published = await jobs_svc.publish_job(
            session,
            job_id=job_id,
            payload=PublishJobRequest(change_summary="phase-e first"),
            actor=user,
            request_context=CTX,
        )
        v1 = published.current_version.version_label if published.current_version else None
        results.append(("publish", published.status, v1))

        await jobs_svc.save_job_draft(
            session,
            job_id=job_id,
            payload=SaveDraftRequest(headcount=2, change_summary="仅人数"),
            actor=user,
            request_context=CTX,
        )
        pub2 = await jobs_svc.publish_job(
            session,
            job_id=job_id,
            payload=PublishJobRequest(change_summary="修订"),
            actor=user,
            request_context=CTX,
        )
        v2 = pub2.current_version.version_label if pub2.current_version else None
        results.append(("minor_publish", v2))
        assert v2 and v2 != v1

        outcome = await run_mock(
            task_type=TASK_TYPE_JD_PARSE,
            input_snapshot={
                "raw_jd_text": "岗位职责\n- AI回归职责\n技能\n- pytest",
                "job_title": "PhaseE回归岗位",
                "department": "软件组",
            },
        )
        assert outcome.ok and outcome.result
        assert outcome.result.get("dimensions")
        results.append(
            ("mock_jd_parse", True, len(outcome.result.get("dimensions") or []))
        )

        app = await cand_svc.create_job_candidate(
            session,
            job_id=job_id,
            payload=CreateCandidateRequest(name="回归候选人", interview_started=False),
            actor=user,
            request_context=CTX,
        )
        results.append(("add_candidate", app.status))

        preview = await cand_svc.get_close_preview(session, job_id=job_id)
        results.append(("close_preview_blocked", preview.can_close, preview.in_flight_count))
        assert preview.can_close is False

        blocked = False
        try:
            await jobs_svc.close_job(
                session,
                job_id=job_id,
                reason="should fail",
                actor=user,
                request_context=CTX,
            )
        except Exception as exc:  # noqa: BLE001
            blocked = True
            results.append(("close_while_inflight", type(exc).__name__))
        assert blocked

        await cand_svc.resolve_close_application(
            session,
            job_id=job_id,
            application_id=app.id,
            payload=ResolveCloseRequest(action="reject", reason="回归淘汰"),
            actor=user,
            request_context=CTX,
        )
        preview2 = await cand_svc.get_close_preview(session, job_id=job_id)
        results.append(("after_reject", preview2.can_close, preview2.in_flight_count))
        assert preview2.can_close is True

        closed = await jobs_svc.close_job(
            session,
            job_id=job_id,
            reason="PhaseE关闭",
            actor=user,
            request_context=CTX,
        )
        results.append(("close", closed.status, closed.close_reason))
        assert closed.status == "closed"

        copied = await jobs_svc.copy_job(
            session, job_id=job_id, actor=user, request_context=CTX
        )
        results.append(("copy_closed", copied.status, copied.code))
        assert copied.status == "draft"

    await engine.dispose()
    print("=== PHASE E SMOKE ===")
    for row in results:
        print(row)
    print("ALL_OK")


if __name__ == "__main__":
    asyncio.run(main())
