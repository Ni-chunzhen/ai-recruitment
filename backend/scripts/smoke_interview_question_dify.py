"""In-memory smoke for INTERVIEW_QUESTION_GENERATE via existing run_dify.

This script never sets environment variables and never opens the live switch.
HTTP is sent only when a human already configured the gitignored local `.env`
with the dedicated Key and Workflow ID, ENVIRONMENT=development, and
DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=true. With the default switch, run_dify
stays on mock.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.models.ai_task import TASK_TYPE_INTERVIEW_QUESTION_GENERATE
from app.services.ai_providers.dify import run_dify

# Provider input uses `dimensions` (list). Do not pass `dimensions_json` here.
INPUT_SNAPSHOT = {
    "job_title": "FICTIONAL-LIVE-20260818 示例岗位-虚构仓储接口工程师",
    "jd_text": (
        "FICTIONAL-LIVE-20260818 "
        "本岗位为完全虚构的演示说明，不对应真实招聘。"
        "职责：维护虚构仓储系统的库存查询接口。"
    ),
    "resume_text": (
        "FICTIONAL-LIVE-20260818 "
        "候选人档案为完全虚构样本，不含真实姓名、电话或邮箱。"
        "曾在虚构项目「北区演示仓」编写库存查询接口。"
    ),
    "dimensions": [
        {
            "dimension_key": "D001",
            "display_order": 1,
            "name": "接口实现",
            "weight": "100.00",
            "description": "考察接口实现",
            "anchors": ["1", "2", "3", "4", "5"],
        }
    ],
}


async def main() -> int:
    get_settings.cache_clear()
    get_settings()
    outcome = await run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=INPUT_SNAPSHOT,
    )
    print("ok=", outcome.ok)
    print("http_status=", outcome.http_status)
    print("error_code=", outcome.error_code)
    if outcome.ok and isinstance(outcome.result, dict):
        questions = outcome.result.get("questions") or []
        print("question_count=", len(questions))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
