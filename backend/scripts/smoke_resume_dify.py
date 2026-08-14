"""Smoke-test Dify RESUME_PARSE and RESUME_SCORE against live credentials."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.models.ai_task import TASK_TYPE_RESUME_PARSE, TASK_TYPE_RESUME_SCORE
from app.services.ai_providers.base import validate_ai_result
from app.services.ai_providers.dify import run_dify


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    print("AI_PROVIDER=", settings.AI_PROVIDER)
    print("PARSE_WF=", settings.DIFY_RESUME_PARSE_WORKFLOW_ID)
    print("SCORE_WF=", settings.DIFY_RESUME_SCORE_WORKFLOW_ID)

    resume_text = """
张三
手机：13800138000
邮箱：zhangsan@example.com

教育经历
厦门大学 计算机科学与技术 本科 2016-2020

工作经历
某科技有限公司 高级前端工程师 2020-2024
- 负责 B 端管理系统，技术栈 Vue3 / TypeScript
- 主导组件库建设与性能优化

技能
Vue, React, TypeScript, Node.js
""".strip()

    print("\n=== RESUME_PARSE ===")
    parse_out = await run_dify(
        task_type=TASK_TYPE_RESUME_PARSE,
        input_snapshot={
            "resume_text": resume_text,
            "candidate_id": "smoke-candidate-001",
        },
    )
    print("ok=", parse_out.ok)
    print("http_status=", parse_out.http_status)
    print("error=", parse_out.error_code, parse_out.error_message)
    if parse_out.ok and parse_out.result:
        validated = validate_ai_result(TASK_TYPE_RESUME_PARSE, parse_out.result)
        print("validated_keys=", sorted(validated.keys()))
        print("name=", validated.get("name"))
        print("skills=", validated.get("skills"))
        print("text_len=", len(str(validated.get("standardized_text") or "")))
    else:
        print("raw_response=", json.dumps(parse_out.raw_response, ensure_ascii=False)[:800])
        return 1

    print("\n=== RESUME_SCORE ===")
    dims = [
        {"name": "专业能力", "description": "前端专业深度与工程实践", "weight": 40},
        {"name": "沟通协作", "description": "跨团队协作与表达", "weight": 20},
        {"name": "问题解决", "description": "分析与落地能力", "weight": 20},
        {"name": "学习成长", "description": "持续学习与技术跟进", "weight": 20},
    ]
    score_out = await run_dify(
        task_type=TASK_TYPE_RESUME_SCORE,
        input_snapshot={
            "jd_content": "高级前端工程师：负责 Vue/TS 业务系统开发与组件库建设。",
            "dimensions_json": dims,
            "resume_text": validated.get("standardized_text") or resume_text,
            "candidate_id": "smoke-candidate-001",
            "job_title": "高级前端工程师",
        },
    )
    print("ok=", score_out.ok)
    print("http_status=", score_out.http_status)
    print("error=", score_out.error_code, score_out.error_message)
    if score_out.ok and score_out.result:
        scored = validate_ai_result(TASK_TYPE_RESUME_SCORE, score_out.result)
        print("dim_count=", len(scored.get("dimensions") or []))
        print("recommendation=", scored.get("recommendation"))
        print(
            "scores=",
            [
                (d.get("name"), d.get("score"))
                for d in (scored.get("dimensions") or [])
            ],
        )
        return 0

    print("raw_response=", json.dumps(score_out.raw_response, ensure_ascii=False)[:1200])
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
