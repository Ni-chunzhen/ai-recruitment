"""HTTP end-to-end: upload -> parse -> confirm -> score -> screening.

Requires local API (8000) + Celery worker + Dify resume keys.
Temporarily sets admin password for smoke login only.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import create_database_engine, create_session_factory
from app.models import User

BASE = "http://127.0.0.1:8000/api/v1"
SMOKE_USER = "admin"
SMOKE_PASS = "E2eSmoke!2026Aa"


async def prepare_admin() -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    sf = create_session_factory(engine)
    async with sf() as session:
        user = (
            await session.execute(select(User).where(User.username == SMOKE_USER))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit("admin user missing; run bootstrap_admin first")
        user.password_hash = hash_password(SMOKE_PASS)
        user.must_change_password = False
        user.is_active = True
        await session.commit()
    await engine.dispose()


def _data(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, dict) and "data" in body and "code" in body:
        if body.get("code") not in (0, None, "0"):
            raise RuntimeError(f"api code={body.get('code')} msg={body.get('message')} body={body}")
        return body.get("data") or {}
    return body


async def wait_until(fn, *, timeout: float = 180, interval: float = 2.0, label: str = " cond"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(interval)
    raise TimeoutError(f"timeout waiting for {label}: last={last}")


async def main() -> int:
    get_settings.cache_clear()
    print("1) prepare admin login")
    await prepare_admin()

    resume_text = """张三
手机：13800138000
邮箱：zhangsan.e2e@example.com

教育经历
厦门大学 计算机科学与技术 本科 2016-2020

工作经历
某科技有限公司 高级前端工程师 2020-2024
- 负责 B 端管理系统，技术栈 Vue3 / TypeScript
- 主导组件库建设与性能优化，首屏耗时下降约 30%
- 与产品、后端协作完成多个业务迭代

技能
Vue, React, TypeScript, Node.js, Vite, Element Plus
""".strip()

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, trust_env=False) as client:
        print("2) login")
        login_body = None
        for attempt in range(8):
            resp = await client.post(
                f"{BASE}/auth/login",
                json={"username": SMOKE_USER, "password": SMOKE_PASS},
            )
            if resp.status_code == 502:
                await asyncio.sleep(1.5)
                continue
            login_body = _data(resp)
            break
        if login_body is None:
            raise RuntimeError("login failed after retries (502)")
        login = login_body
        token = login.get("access_token") or login.get("accessToken")
        if not token and isinstance(login.get("tokens"), dict):
            token = login["tokens"].get("access_token")
        if not token:
            print("login payload keys=", list(login.keys()))
            token = login.get("token")
        if not token:
            raise RuntimeError(f"no access token in login: {login}")
        headers = {"Authorization": f"Bearer {token}"}

        print("3) create+publish job")
        job = _data(
            await client.post(
                f"{BASE}/jobs",
                headers=headers,
                json={
                    "name": f"E2E前端评分-{uuid4().hex[:6]}",
                    "department": "研发中心",
                    "level": "P5",
                    "headcount": 1,
                    "location": "厦门",
                    "owner_name": "系统管理员",
                    "urgency": "normal",
                    "raw_jd_text": (
                        "岗位：高级前端工程师\n"
                        "职责：负责 Vue/TS 中后台开发与组件库建设\n"
                        "要求：3年以上前端经验，熟练 Vue3、TypeScript、Vite"
                    ),
                    "structured_jd": {
                        "responsibilities": [
                            "负责中后台前端开发",
                            "建设组件库",
                            "性能优化",
                            "跨角色协作",
                        ],
                        "requirements": [
                            "3年以上前端经验",
                            "熟练 Vue3/TypeScript",
                            "熟悉工程化",
                        ],
                        "must_have": ["Vue3", "TypeScript"],
                        "nice_to_have": ["组件库经验"],
                        "skills": ["Vue", "TypeScript", "Vite"],
                    },
                    "score_dimensions": [
                        {
                            "name": "专业能力",
                            "weight": 40,
                            "description": "前端硬技能与工程实践",
                            "anchors": ["1", "2", "3", "4", "5"],
                        },
                        {
                            "name": "沟通协作",
                            "weight": 30,
                            "description": "跨团队协作",
                            "anchors": ["1", "2", "3", "4", "5"],
                        },
                        {
                            "name": "成长潜力",
                            "weight": 30,
                            "description": "学习与成长",
                            "anchors": ["1", "2", "3", "4", "5"],
                        },
                    ],
                },
            )
        )
        job_id = job["id"]
        published = _data(
            await client.post(
                f"{BASE}/jobs/{job_id}/publish",
                headers=headers,
                json={"change_summary": "e2e initial publish"},
            )
        )
        print("   job=", published.get("name"), "status=", published.get("status"))

        print("4) upload resume (bind job)")
        files = {
            "files": ("zhangsan-e2e.txt", resume_text.encode("utf-8"), "text/plain"),
        }
        data = {"job_id": str(job_id)}
        upload = _data(
            await client.post(
                f"{BASE}/resumes",
                headers=headers,
                data=data,
                files=files,
            )
        )
        items = upload.get("items") or upload.get("versions") or []
        if not items and upload.get("id"):
            items = [upload]
        if not items:
            raise RuntimeError(f"unexpected upload response: {upload}")
        version_id = (
            items[0].get("resume_version_id")
            or items[0].get("version_id")
            or items[0].get("id")
        )
        application_id = items[0].get("application_id")
        print("   version_id=", version_id, "application_id=", application_id)
        print("   upload item=", {k: items[0].get(k) for k in ("id", "status", "parse_task_id")})

        print("5) wait RESUME_PARSE via celery")

        async def parsed():
            ver = _data(
                await client.get(f"{BASE}/resume-versions/{version_id}", headers=headers)
            )
            status = ver.get("status")
            print("   parse status=", status)
            if status == "pending_review":
                return ver
            if status == "parse_failed":
                raise RuntimeError(f"parse failed: {ver}")
            return None

        version = await wait_until(parsed, timeout=180, label="resume parse")
        draft = version.get("draft_content") or version.get("ai_structured") or {}
        print(
            "   parsed name=",
            draft.get("name"),
            "skills=",
            draft.get("skills"),
        )

        print("6) confirm resume")
        content = {
            "name": draft.get("name") or "张三",
            "name_pending": False,
            "phone": draft.get("phone") or "13800138000",
            "email": draft.get("email") or "zhangsan.e2e@example.com",
            "years_of_experience": draft.get("years_of_experience"),
            "education": draft.get("education") or [],
            "work_experience": draft.get("work_experience")
            or draft.get("experiences")
            or [],
            "projects": draft.get("projects") or [],
            "skills": draft.get("skills") or ["Vue", "TypeScript"],
            "standardized_text": draft.get("standardized_text") or resume_text,
            "field_sources": draft.get("field_sources") or {},
        }
        confirmed = _data(
            await client.put(
                f"{BASE}/resume-versions/{version_id}/confirmed-content",
                headers=headers,
                json={"content": content},
            )
        )
        confirmed_id = confirmed["id"]
        candidate_id = confirmed["candidate_id"]
        print("   confirmed_id=", confirmed_id, "status=", confirmed.get("status"))

        if not application_id:
            print("6b) create application")
            app = _data(
                await client.post(
                    f"{BASE}/applications",
                    headers=headers,
                    json={
                        "candidate_id": str(candidate_id),
                        "job_id": str(job_id),
                        "resume_version_id": str(confirmed_id),
                        "idempotency_key": f"e2e-{uuid4().hex[:8]}",
                    },
                )
            )
            application_id = app["id"]
        print("   application_id=", application_id)

        print("7) create RESUME_SCORE task")
        score_task = _data(
            await client.post(
                f"{BASE}/applications/{application_id}/resume-score-tasks",
                headers=headers,
                json={
                    "resume_version_id": str(confirmed_id),
                    "idempotency_key": f"score-{uuid4().hex[:8]}",
                },
            )
        )
        task_id = score_task["task_id"]
        print("   task_id=", task_id, "status=", score_task.get("status"))

        print("8) wait score report")

        async def scored():
            # poll AI task
            task = _data(await client.get(f"{BASE}/ai-tasks/{task_id}", headers=headers))
            st = task.get("status")
            print("   task status=", st)
            if st in {"failed", "output_invalid"}:
                raise RuntimeError(f"score task failed: {json.dumps(task, ensure_ascii=False)[:800]}")
            if st != "succeeded":
                return None
            try:
                report = _data(
                    await client.get(
                        f"{BASE}/applications/{application_id}/resume-score-report",
                        headers=headers,
                    )
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                raise
            return report

        report = await wait_until(scored, timeout=180, label="score report")
        dims = report.get("dimensions") or []
        scores = [(d.get("name"), d.get("score")) for d in dims]
        print("   total=", report.get("total_score"), "recommendation=", report.get("recommendation"))
        print("   scores=", scores)
        if not dims or all(float(d.get("score") or 0) == 0 for d in dims):
            raise RuntimeError("score dimensions empty or all zero")

        print("9) screening decision")
        decision = _data(
            await client.post(
                f"{BASE}/applications/{application_id}/screening-decisions",
                headers=headers,
                json={
                    "decision": "enter_interview",
                    "reason": "e2e smoke: scores look good",
                    "lock_version": report.get("lock_version") or 1,
                },
            )
        )
        print(
            "   decision=",
            decision.get("decision"),
            "pipeline=",
            decision.get("from_pipeline_status"),
            "->",
            decision.get("to_pipeline_status"),
        )

    print("\nALL_OK e2e resume scoring")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", exc)
        raise
