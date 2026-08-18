from __future__ import annotations

import re
from typing import Any

from app.models.ai_task import (
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_RESUME_PARSE,
    TASK_TYPE_RESUME_SCORE,
    TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
)
from app.services.ai_providers.base import ProviderOutcome, validate_ai_result

SECTION_PATTERNS = (
    ("responsibilities", re.compile(r"^(岗位)?职责|工作内容|职责描述")),
    ("requirements", re.compile(r"任职要求|岗位要求|任职资格")),
    ("must_have", re.compile(r"必备|必须具备|硬性要求")),
    ("nice_to_have", re.compile(r"加分|优先|更好")),
    ("skills", re.compile(r"技能|能力要求|专业技能")),
)

DEFAULT_ANCHORS = ["不足", "一般", "达标", "良好", "优秀"]
DEFAULT_DIMENSION_TEMPLATES = (
    ("专业能力", "岗位相关专业知识与实践经验", DEFAULT_ANCHORS),
    ("沟通协作", "跨团队沟通与协作推动能力", DEFAULT_ANCHORS),
    ("问题解决", "分析问题并提出可行方案的能力", DEFAULT_ANCHORS),
    ("学习成长", "持续学习与适应变化的能力", DEFAULT_ANCHORS),
    ("责任担当", "结果导向与责任意识", DEFAULT_ANCHORS),
    ("业务理解", "对业务目标与用户价值的理解", DEFAULT_ANCHORS),
)


def _clean_line(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^[\-\*\u2022\d\.\)\、]+\s*", "", text)
    return text.strip()


def parse_raw_jd_text(raw_jd_text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        "responsibilities": [],
        "requirements": [],
        "must_have": [],
        "nice_to_have": [],
        "skills": [],
    }
    current = "responsibilities"
    for raw_line in (raw_jd_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched_section = None
        for key, pattern in SECTION_PATTERNS:
            if pattern.search(line) and len(line) <= 20:
                matched_section = key
                break
        if matched_section is not None:
            current = matched_section
            # title-only line; skip content
            remainder = pattern_remainder(line)
            if remainder:
                result[current].append(remainder)
            continue
        cleaned = _clean_line(line)
        if cleaned:
            result[current].append(cleaned)

    # Ensure valid non-empty-ish structure: keep lists even if empty
    if not any(result.values()) and (raw_jd_text or "").strip():
        result["responsibilities"] = [
            item for item in (_clean_line(x) for x in raw_jd_text.splitlines()) if item
        ][:20]
    return result


def pattern_remainder(line: str) -> str:
    for _, pattern in SECTION_PATTERNS:
        if pattern.search(line):
            cleaned = pattern.sub("", line).strip(" ：:|-")
            return _clean_line(cleaned) if cleaned else ""
    return ""


def recommend_dimensions(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    skills = [
        str(item).strip()
        for item in (input_snapshot.get("skills") or [])
        if str(item).strip()
    ]
    requirements = [
        str(item).strip()
        for item in (input_snapshot.get("requirements") or [])
        if str(item).strip()
    ]
    hints = skills + requirements

    count = min(6, max(3, len(hints) // 2 or 3))
    dimensions: list[dict[str, Any]] = []
    for index in range(count):
        if index < len(hints) and len(hints[index]) <= 16:
            name = hints[index][:32]
            description = f"与「{name}」相关的岗位能力评估"
        else:
            name, description, _ = DEFAULT_DIMENSION_TEMPLATES[index]
        _, _, anchors = DEFAULT_DIMENSION_TEMPLATES[
            index % len(DEFAULT_DIMENSION_TEMPLATES)
        ]
        dimensions.append(
            {
                "name": name,
                "weight": 0.0,
                "description": description,
                "anchors": list(anchors),
            }
        )

    # Distribute weights to sum ~100
    base = 100 // count
    remainder = 100 - base * count
    for index, item in enumerate(dimensions):
        item["weight"] = float(base + (1 if index < remainder else 0))
    return {"dimensions": dimensions}


def mock_resume_parse(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    text = str(input_snapshot.get("resume_text") or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = ""
    phone = ""
    email = ""
    for line in lines[:8]:
        if not name and 1 < len(line) <= 16 and "@" not in line and not any(
            ch.isdigit() for ch in line
        ):
            name = line
        digits = re.sub(r"\D", "", line)
        if not phone and len(digits) >= 11:
            phone = digits[-11:]
        if not email and "@" in line:
            email = line
    skills = []
    for token in ("Python", "Java", "Vue", "React", "TypeScript", "SQL", "Go"):
        if token.lower() in text.lower():
            skills.append(token)
    return {
        "name": name,
        "phone": phone,
        "email": email,
        "years_of_experience": None,
        "education": [],
        "work_experience": [],
        "projects": [],
        "skills": skills,
        "standardized_text": text or "（空简历文本）",
    }


def mock_resume_score(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    dims = list(input_snapshot.get("dimensions_json") or [])
    resume_text = str(input_snapshot.get("resume_text") or "")
    scored = []
    for item in dims:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        weight = float(item.get("weight") or 0)
        # naive keyword presence score
        hit = name and name.lower() in resume_text.lower()
        score = 78.0 if hit else 62.0
        scored.append(
            {
                "name": name,
                "description": str(item.get("description") or ""),
                "weight": weight,
                "score": score,
                "evidence": "基于简历文本的 mock 评分依据",
                "gap": "" if hit else "简历中相关表述不足",
                "risk": "",
            }
        )
    if not scored:
        raise ValueError("dimensions_json is required for RESUME_SCORE")
    return {
        "dimensions": scored,
        "recommendation": "建议面试",
        "score_band": "B",
        "must_have_check": [],
        "risks": [],
        "summary": "Mock 多维评分结果，仅用于联调",
        "information_insufficient": len(resume_text) < 80,
    }


def _snapshot_dimensions(input_snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    dims = [
        item
        for item in ((input_snapshot or {}).get("dimensions") or [])
        if isinstance(item, dict) and str(item.get("dimension_key") or "").strip()
    ]
    return dims or [{"dimension_key": "D001"}]


def mock_interview_question_generate(
    input_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    questions = []
    for index, item in enumerate(_snapshot_dimensions(input_snapshot), start=1):
        key = str(item["dimension_key"]).strip()
        questions.append(
            {
                "dimension_key": key,
                "question": f"请结合维度{key}描述一次具体实践。",
                "purpose": f"考察{key}",
                "evidence_source": "JOB_REQUIREMENT",
                "resume_evidence": None,
                "follow_up_prompts": ["请补充可量化结果。"],
                "risk_flags": ["可能缺少细节"],
                "display_order": index,
            }
        )
    return {"questions": questions}


def mock_interview_round_analyze(
    input_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snap = input_snapshot or {}
    segments = [item for item in (snap.get("segments") or []) if isinstance(item, dict)]
    first = segments[0] if segments else {}
    seg_id = str(
        first.get("id")
        or first.get("segment_id")
        or "11111111-1111-1111-1111-111111111111"
    )
    try:
        seg_no = int(first.get("segment_no") or 1)
    except (TypeError, ValueError):
        seg_no = 1
    quote = (
        str(first.get("text") or "我当时先对齐目标。").strip()[:80]
        or "我当时先对齐目标。"
    )
    dimensions = []
    for item in _snapshot_dimensions(snap):
        key = str(item["dimension_key"]).strip()
        dimensions.append(
            {
                "dimension_key": key,
                "score": 4,
                "evidence": [
                    {
                        "segment_id": seg_id,
                        "segment_no": seg_no,
                        "quote": quote,
                    }
                ],
                "analysis": f"{key}表现稳定。",
                "strengths": ["目标对齐"],
                "risks": ["细节偏少"],
                "insufficient_information": None,
                "suggested_follow_ups": ["请补充具体结果"],
            }
        )
    return {
        "dimensions": dimensions,
        "overall_summary": "整体表现稳定，建议深入追问结果指标。",
        "model_reported_overall_score": "4.00",
    }


async def run_mock(
    *,
    task_type: str,
    input_snapshot: dict[str, Any],
    sleep_seconds: float = 0.0,
) -> ProviderOutcome:
    if sleep_seconds > 0:
        import asyncio

        await asyncio.sleep(sleep_seconds)

    raw_request: dict[str, Any] = {"provider": "mock", "task_type": task_type}
    if task_type not in {
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    }:
        raw_request["input"] = input_snapshot
    try:
        if task_type == TASK_TYPE_JD_PARSE:
            parsed = parse_raw_jd_text(str(input_snapshot.get("raw_jd_text") or ""))
            dims = recommend_dimensions(
                {
                    **parsed,
                    "skills": parsed.get("skills") or [],
                    "requirements": parsed.get("requirements") or [],
                }
            )
            result = validate_ai_result(
                task_type, {**parsed, "dimensions": dims["dimensions"]}
            )
        elif task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
            recommended = recommend_dimensions(input_snapshot)
            result = validate_ai_result(task_type, recommended)
        elif task_type == TASK_TYPE_RESUME_PARSE:
            result = validate_ai_result(task_type, mock_resume_parse(input_snapshot))
        elif task_type == TASK_TYPE_RESUME_SCORE:
            result = validate_ai_result(task_type, mock_resume_score(input_snapshot))
        elif task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
            result = validate_ai_result(
                task_type, mock_interview_question_generate(input_snapshot)
            )
        elif task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
            result = validate_ai_result(
                task_type, mock_interview_round_analyze(input_snapshot)
            )
        else:
            return ProviderOutcome(
                ok=False,
                raw_request=raw_request,
                error_code="unsupported_task_type",
                error_message=f"unsupported task_type: {task_type}",
                error_category="non_retryable",
            )
    except Exception as exc:  # noqa: BLE001 — surface as non-retryable validation
        return ProviderOutcome(
            ok=False,
            raw_request=raw_request,
            raw_response={"error": str(exc)},
            error_code="output_validation_failed",
            error_message=str(exc),
            error_category="non_retryable",
        )

    return ProviderOutcome(
        ok=True,
        result=result,
        raw_request=raw_request,
        raw_response={"outputs": result},
    )
