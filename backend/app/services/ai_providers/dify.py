from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.models.ai_task import (
    ERROR_CATEGORY_NON_RETRYABLE,
    ERROR_CATEGORY_RETRYABLE,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_RESUME_PARSE,
    TASK_TYPE_RESUME_SCORE,
    TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
)
from app.services.ai_providers.base import (
    ProviderOutcome,
    classify_http_error,
    validate_ai_result,
)

INTERVIEW_QUESTION_LIVE_UAT_PREFIX = "UAT-CC-20260818"
INTERVIEW_QUESTION_LIVE_FICTIONAL_PREFIX = "FICTIONAL-LIVE-20260818"
_QUESTION_LIVE_INPUT_KEYS = frozenset(
    {"job_title", "jd_text", "resume_text", "dimensions_json"}
)
_QUESTION_LIVE_FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "token",
        "api_key",
        "Authorization",
        "cookie",
        "meeting_password",
        "segments",
        "segments_json",
        "quote",
        "candidate_id",
        "raw_request",
        "raw_response",
        "result_payload",
    }
)


@dataclass(frozen=True)
class LiveGateDecision:
    allow_http: bool
    fallback_mock: bool
    error_code: str | None
    reason: str | None


def _question_live_contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        if _QUESTION_LIVE_FORBIDDEN_KEYS.intersection(value):
            return True
        if any(str(key).startswith("sensitive_") for key in value):
            return True
        return any(_question_live_contains_forbidden(item) for item in value.values())
    if isinstance(value, list):
        return any(_question_live_contains_forbidden(item) for item in value)
    return False


def _question_live_shared_prefix(
    job_title: str, jd_text: str, resume_text: str
) -> str | None:
    for prefix in (
        INTERVIEW_QUESTION_LIVE_UAT_PREFIX,
        INTERVIEW_QUESTION_LIVE_FICTIONAL_PREFIX,
    ):
        if (
            job_title.startswith(prefix)
            and jd_text.startswith(prefix)
            and resume_text.startswith(prefix)
        ):
            return prefix
    return None


def interview_question_live_http_allowed(
    settings: Settings, inputs: dict[str, Any]
) -> LiveGateDecision:
    """Decide whether INTERVIEW_QUESTION_GENERATE may call Dify HTTP."""
    if str(settings.ENVIRONMENT or "").strip().lower() != "development":
        return LiveGateDecision(
            allow_http=False,
            fallback_mock=True,
            error_code=None,
            reason="environment",
        )
    if settings.dify_interview_question_live_enabled is not True:
        return LiveGateDecision(
            allow_http=False,
            fallback_mock=True,
            error_code=None,
            reason="switch_off",
        )

    key = (
        settings.dify_interview_question_generate_api_key_secret.get_secret_value()
    ).strip()
    workflow_id = settings.dify_interview_question_generate_workflow_id.strip()
    base_url = settings.DIFY_API_BASE_URL.strip()
    if not key or not workflow_id or not base_url:
        return LiveGateDecision(
            allow_http=False,
            fallback_mock=False,
            error_code="interview_question_live_not_configured",
            reason="not_configured",
        )

    if not isinstance(inputs, dict) or set(inputs) != _QUESTION_LIVE_INPUT_KEYS:
        return LiveGateDecision(
            allow_http=False,
            fallback_mock=False,
            error_code="interview_question_live_unauthorized",
            reason="input_keys",
        )

    dims_raw = inputs.get("dimensions_json")
    dims_parsed = _parse_jsonish(dims_raw) if isinstance(dims_raw, str) else dims_raw
    if _question_live_contains_forbidden(dims_parsed):
        return LiveGateDecision(
            allow_http=False,
            fallback_mock=False,
            error_code="interview_question_live_unauthorized",
            reason="forbidden_keys",
        )

    job_title = str(inputs.get("job_title") or "")
    jd_text = str(inputs.get("jd_text") or "")
    resume_text = str(inputs.get("resume_text") or "")
    if _question_live_shared_prefix(job_title, jd_text, resume_text) is None:
        return LiveGateDecision(
            allow_http=False,
            fallback_mock=False,
            error_code="interview_question_live_unauthorized",
            reason="unauthorized_prefix",
        )

    return LiveGateDecision(
        allow_http=True,
        fallback_mock=False,
        error_code=None,
        reason=None,
    )


def _workflow_id_for(task_type: str) -> str:
    settings = get_settings()
    if task_type == TASK_TYPE_JD_PARSE:
        return settings.DIFY_JD_PARSE_WORKFLOW_ID
    if task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
        return settings.DIFY_SCORE_DIMENSION_WORKFLOW_ID
    if task_type == TASK_TYPE_RESUME_PARSE:
        return settings.DIFY_RESUME_PARSE_WORKFLOW_ID
    if task_type == TASK_TYPE_RESUME_SCORE:
        return settings.DIFY_RESUME_SCORE_WORKFLOW_ID
    if task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        return settings.dify_interview_question_generate_workflow_id.strip()
    return ""


def _resume_dify_configured(task_type: str) -> bool:
    settings = get_settings()
    key = settings.dify_api_key_for(task_type).strip()
    workflow = _workflow_id_for(task_type).strip()
    # workflow id is documentation/marker; API key is required for auth
    if task_type == TASK_TYPE_RESUME_PARSE:
        return bool(
            settings.dify_resume_parse_api_key_secret.get_secret_value().strip()
            or (key and workflow)
        )
    if task_type == TASK_TYPE_RESUME_SCORE:
        return bool(
            settings.dify_resume_score_api_key_secret.get_secret_value().strip()
            and workflow
        )
    return bool(key)


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def build_dify_inputs(task_type: str, input_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Map canonical input_snapshot → Dify workflow start variables."""
    job_title = str(input_snapshot.get("job_title") or "").strip() or "未命名岗位"
    department = str(input_snapshot.get("department") or "").strip()

    if task_type == TASK_TYPE_JD_PARSE:
        # JD结构化.yml: job_title, department, hr_manual_override, jd_file(optional)
        return {
            "job_title": job_title,
            "department": department,
            "hr_manual_override": str(input_snapshot.get("raw_jd_text") or ""),
        }

    if task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
        # 能力维度生成.yml start vars + structured_jd JSON string
        structured = input_snapshot.get("structured_jd")
        if isinstance(structured, str):
            structured_obj = _parse_jsonish(structured) or {}
        elif isinstance(structured, dict):
            structured_obj = structured
        else:
            structured_obj = {
                "responsibilities": list(input_snapshot.get("responsibilities") or []),
                "requirements": list(input_snapshot.get("requirements") or []),
                "must_have": list(input_snapshot.get("must_have") or []),
                "nice_to_have": list(input_snapshot.get("nice_to_have") or []),
                "skills": list(input_snapshot.get("skills") or []),
            }

        dify_structured = {
            "responsibilities": list(structured_obj.get("responsibilities") or []),
            "requirements": list(structured_obj.get("requirements") or []),
            "must_have": list(structured_obj.get("must_have") or []),
            "nice_to_have": list(structured_obj.get("nice_to_have") or []),
            "plus_items_for_diff": str(
                structured_obj.get("plus_items_for_diff") or ""
            ),
            "skill_keywords": list(
                structured_obj.get("skill_keywords")
                or structured_obj.get("skills")
                or []
            ),
        }
        return {
            "job_title": job_title,
            "department": department,
            "jd_content": str(input_snapshot.get("jd_content") or ""),
            "structured_jd": json.dumps(dify_structured, ensure_ascii=False),
        }

    if task_type == TASK_TYPE_RESUME_PARSE:
        return {
            "resume_text": str(input_snapshot.get("resume_text") or ""),
            "candidate_id": str(input_snapshot.get("candidate_id") or ""),
        }

    if task_type == TASK_TYPE_RESUME_SCORE:
        dims = input_snapshot.get("dimensions_json") or []
        return {
            "jd_content": str(input_snapshot.get("jd_content") or ""),
            "dimensions_json": json.dumps(dims, ensure_ascii=False)
            if not isinstance(dims, str)
            else dims,
            "resume_text": str(input_snapshot.get("resume_text") or ""),
            "candidate_id": str(input_snapshot.get("candidate_id") or ""),
            "job_title": job_title,
        }

    if task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        return {
            "job_title": job_title,
            "jd_text": str(input_snapshot.get("jd_text") or ""),
            "resume_text": str(input_snapshot.get("resume_text") or ""),
            "dimensions_json": json.dumps(
                input_snapshot.get("dimensions") or [], ensure_ascii=False
            ),
        }

    if task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        segments = []
        for item in input_snapshot.get("segments") or []:
            if not isinstance(item, dict):
                continue
            segments.append(
                {
                    "segment_id": str(item.get("id") or item.get("segment_id") or ""),
                    "segment_no": item.get("segment_no"),
                    "text": str(item.get("text") or ""),
                }
            )
        return {
            "dimensions_json": json.dumps(
                input_snapshot.get("dimensions") or [], ensure_ascii=False
            ),
            "segments_json": json.dumps(segments, ensure_ascii=False),
        }

    return dict(input_snapshot)


def _structured_from_jd_result_blob(result_blob: Any) -> tuple[dict[str, Any], str]:
    """Parse JD结构化.yml end `result` JSON → (structured_jd dict, jd_content)."""
    if not isinstance(result_blob, dict):
        return {}, ""
    jd_content = str(result_blob.get("jd_content") or "")
    structured = result_blob.get("structured_jd")
    if isinstance(structured, str):
        parsed = _parse_jsonish(structured)
        structured = parsed if isinstance(parsed, dict) else {}
    elif not isinstance(structured, dict):
        structured = {}
    return structured, jd_content


def _anchors_from_dimension_item(item: dict[str, Any]) -> list[str]:
    """Map Dify score_anchors {1..5} (or legacy anchors[]) → canonical 5-slot list."""
    score_anchors = item.get("score_anchors")
    if isinstance(score_anchors, dict):
        return [
            str(
                score_anchors.get(str(i))
                or score_anchors.get(i)
                or ""
            ).strip()
            for i in range(1, 6)
        ]
    if isinstance(score_anchors, list):
        out = [str(a or "").strip() for a in score_anchors[:5]]
        while len(out) < 5:
            out.append("")
        return out

    anchors = item.get("anchors")
    if isinstance(anchors, list) and anchors:
        out = [str(a or "").strip() for a in anchors[:5]]
        while len(out) < 5:
            out.append("")
        return out
    return ["", "", "", "", ""]


def normalize_dify_outputs(
    task_type: str, outputs: dict[str, Any]
) -> dict[str, Any]:
    """Map Dify end-node outputs → canonical AI result schema."""
    if task_type == TASK_TYPE_JD_PARSE:
        structured: Any = None
        # Primary: JD结构化.yml end → result (JSON string/object)
        result_blob = _parse_jsonish(outputs.get("result"))
        structured, _jd_content = _structured_from_jd_result_blob(result_blob)

        if not isinstance(structured, dict) or not structured:
            structured = _parse_jsonish(outputs.get("structured_jd"))
        if not isinstance(structured, dict) or not structured:
            full = _parse_jsonish(outputs.get("full_result"))
            if isinstance(full, dict):
                structured, _ = _structured_from_jd_result_blob(full)
                if not structured:
                    inner = full.get("structured_jd")
                    if isinstance(inner, str):
                        structured = _parse_jsonish(inner)
                    elif isinstance(inner, dict):
                        structured = inner
        if not isinstance(structured, dict) or not structured:
            jd_keys = ("responsibilities", "skill_keywords", "skills")
            if any(k in outputs for k in jd_keys):
                structured = outputs
            else:
                structured = {}

        nice = list(structured.get("nice_to_have") or [])
        plus = structured.get("plus_items_for_diff")
        if isinstance(plus, str) and plus.strip() and plus.strip() not in nice:
            nice = [*nice, plus.strip()]

        return {
            "responsibilities": list(structured.get("responsibilities") or []),
            "requirements": list(structured.get("requirements") or []),
            "must_have": list(structured.get("must_have") or []),
            "nice_to_have": nice,
            "skills": list(
                structured.get("skill_keywords") or structured.get("skills") or []
            ),
        }

    if task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
        dims_payload = _parse_jsonish(outputs.get("final_dimensions"))
        if isinstance(dims_payload, dict) and dims_payload.get("error"):
            err = dims_payload.get("error")
            raise ValueError(f"能力维度工作流返回错误: {err}")
        if not isinstance(dims_payload, dict):
            result_payload = _parse_jsonish(outputs.get("result"))
            if isinstance(result_payload, dict):
                if result_payload.get("error") and not result_payload.get(
                    "dimensions"
                ):
                    raise ValueError(
                        f"能力维度工作流返回错误: {result_payload.get('error')}"
                    )
                if isinstance(result_payload.get("dimensions"), list):
                    dims_payload = {"dimensions": result_payload["dimensions"]}
                elif isinstance(
                    result_payload.get("final_dimensions"), (str, dict)
                ):
                    dims_payload = _parse_jsonish(
                        result_payload.get("final_dimensions")
                    )
        if not isinstance(dims_payload, dict):
            if isinstance(outputs.get("dimensions"), list):
                dims_payload = {"dimensions": outputs["dimensions"]}
            else:
                dims_payload = {"dimensions": []}

        dimensions = []
        for item in dims_payload.get("dimensions") or []:
            if not isinstance(item, dict):
                continue
            dimensions.append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "weight": float(item.get("weight") or 0),
                    "description": str(item.get("description") or ""),
                    "anchors": _anchors_from_dimension_item(item),
                }
            )
        return {"dimensions": dimensions}

    if task_type == TASK_TYPE_RESUME_PARSE:
        blob = _parse_jsonish(outputs.get("result")) or outputs
        if not isinstance(blob, dict):
            blob = {}
        text = str(
            blob.get("standardized_text")
            or blob.get("resume_text")
            or outputs.get("standardized_text")
            or ""
        ).strip()
        return {
            "name": str(blob.get("name") or ""),
            "phone": str(blob.get("phone") or ""),
            "email": str(blob.get("email") or ""),
            "years_of_experience": blob.get("years_of_experience"),
            "education": list(blob.get("education") or []),
            "work_experience": list(
                blob.get("work_experience") or blob.get("experiences") or []
            ),
            "projects": list(blob.get("projects") or []),
            "skills": list(blob.get("skills") or []),
            "standardized_text": text,
        }

    if task_type == TASK_TYPE_RESUME_SCORE:
        blob = _parse_jsonish(outputs.get("result")) or outputs
        if not isinstance(blob, dict):
            blob = {}
        dimensions = []
        for item in blob.get("dimensions") or outputs.get("dimensions") or []:
            if not isinstance(item, dict):
                continue
            dimensions.append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "description": str(item.get("description") or ""),
                    "weight": float(item.get("weight") or 0),
                    "score": float(item.get("score") or 0),
                    "evidence": str(item.get("evidence") or item.get("basis") or ""),
                    "gap": str(item.get("gap") or ""),
                    "risk": str(item.get("risk") or ""),
                }
            )
        return {
            "dimensions": dimensions,
            "total_score": blob.get("total_score"),
            "recommendation": str(blob.get("recommendation") or ""),
            "score_band": str(blob.get("score_band") or ""),
            "must_have_check": list(blob.get("must_have_check") or []),
            "risks": list(blob.get("risks") or []),
            "summary": str(blob.get("summary") or ""),
            "information_insufficient": bool(
                blob.get("information_insufficient") or False
            ),
        }

    if task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        blob: Any = outputs
        nested = _parse_jsonish(outputs.get("result")) if isinstance(outputs, dict) else None
        if isinstance(nested, dict) and (
            "questions" in nested or nested.get("error")
        ):
            blob = nested
        if not isinstance(blob, dict):
            raise ValueError("interview question result must be an object")
        questions = blob.get("questions")
        has_questions = isinstance(questions, list) and bool(questions)
        if blob.get("error") and not has_questions:
            raise ValueError(
                str(
                    blob.get("error_message")
                    or blob.get("error_code")
                    or "interview question workflow error"
                )
            )
        if not isinstance(questions, list):
            raise ValueError("interview question result missing questions")
        orders = []
        for item in questions:
            if not isinstance(item, dict):
                raise ValueError("interview question item must be an object")
            orders.append(item.get("display_order"))
        expected = list(range(1, len(questions) + 1))
        if orders != expected:
            raise ValueError("display_order must run consecutively from 1 to N")
        return {"questions": questions}

    return outputs


def jd_result_is_empty(jd: dict[str, Any]) -> bool:
    keys = (
        "responsibilities",
        "requirements",
        "must_have",
        "nice_to_have",
        "skills",
    )
    return not any(jd.get(k) for k in keys)


def looks_like_score_outputs(outputs: dict[str, Any]) -> bool:
    """True when response resembles 能力维度生成 rather than JD结构化."""
    if "result" in outputs:
        blob = _parse_jsonish(outputs.get("result"))
        if isinstance(blob, dict) and isinstance(
            blob.get("structured_jd"), (dict, str)
        ):
            return False
        if isinstance(blob, dict) and isinstance(blob.get("dimensions"), list):
            return True
    fd = _parse_jsonish(outputs.get("final_dimensions"))
    return isinstance(fd, dict) and (
        "dimensions" in fd or "error" in fd
    ) and "structured_jd" not in (fd or {})


def extract_jd_content_from_outputs(outputs: dict[str, Any]) -> str:
    """Best-effort JD body text from JD结构化 outputs (for score workflow)."""
    result_blob = _parse_jsonish(outputs.get("result"))
    if isinstance(result_blob, dict):
        text = str(result_blob.get("jd_content") or "").strip()
        if text:
            return text
    for key in ("jd_content", "hr_manual_override"):
        text = str(outputs.get(key) or "").strip()
        if text:
            return text
    return ""


def extract_dify_run_ids(
    raw_response: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Map Dify JSON → (provider_run_id, request_id).

    provider_run_id = workflow_run_id
    request_id = task_id (Dify queue/task id, not ai_tasks.id)
    Multi-step envelopes use the last step. No HTTP header guessing.
    """
    if not isinstance(raw_response, dict):
        return None, None
    target: dict[str, Any] | None = raw_response
    steps = raw_response.get("steps")
    if isinstance(steps, list) and steps:
        last = steps[-1]
        target = last if isinstance(last, dict) else None
    if not isinstance(target, dict):
        return None, None
    run_id = target.get("workflow_run_id")
    if run_id is None and isinstance(target.get("data"), dict):
        run_id = target["data"].get("id")
    req_id = target.get("task_id")
    run_s = str(run_id).strip() if run_id is not None else ""
    req_s = str(req_id).strip() if req_id is not None else ""
    return (run_s or None, req_s or None)


def _with_run_ids(outcome: ProviderOutcome) -> ProviderOutcome:
    if outcome.provider_run_id is None or outcome.request_id is None:
        run_id, req_id = extract_dify_run_ids(
            outcome.raw_response if isinstance(outcome.raw_response, dict) else None
        )
        if outcome.provider_run_id is None:
            outcome.provider_run_id = run_id
        if outcome.request_id is None:
            outcome.request_id = req_id
    return outcome


def _extract_outputs(payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort extract structured outputs from Dify workflow run response."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return {}
    outputs = data.get("outputs")
    if isinstance(outputs, dict):
        if "result" in outputs and isinstance(outputs["result"], dict):
            return outputs["result"]
        if "text" in outputs and isinstance(outputs["text"], str):
            parsed = _parse_jsonish(outputs["text"])
            if isinstance(parsed, dict):
                return parsed
        return outputs
    return data if isinstance(data, dict) else {}


async def _post_workflow(
    *,
    task_type: str,
    input_snapshot: dict[str, Any],
) -> ProviderOutcome:
    """Single Dify workflow HTTP call; on success result is raw outputs dict."""
    settings = get_settings()
    base_url = settings.DIFY_API_BASE_URL.rstrip("/")
    api_key = settings.dify_api_key_for(task_type)
    workflow_id = _workflow_id_for(task_type)

    if not base_url or not api_key:
        return ProviderOutcome(
            ok=False,
            raw_request={"provider": "dify", "task_type": task_type},
            error_code="provider_misconfigured",
            error_message=(
                "Dify provider is not fully configured "
                f"(missing API key for {task_type})"
            ),
            error_category=ERROR_CATEGORY_NON_RETRYABLE,
        )

    dify_inputs = build_dify_inputs(task_type, input_snapshot)
    url = f"{base_url}/v1/workflows/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Dify 以 API Key 绑定应用；workflow_id 仅作标记，不能跨应用切换
    body: dict[str, Any] = {
        "inputs": dify_inputs,
        "response_mode": "blocking",
        "user": f"ai-task-{task_type}",
    }
    if workflow_id:
        body["workflow_id"] = workflow_id
    raw_request = {
        "provider": "dify",
        "url": url,
        "workflow_id": workflow_id or None,
        "task_type": task_type,
        "inputs": dify_inputs,
        "api_key_suffix": api_key[-6:] if len(api_key) >= 6 else "***",
    }

    timeout = httpx.Timeout(settings.AI_TASK_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        return ProviderOutcome(
            ok=False,
            raw_request=raw_request,
            error_code="timeout",
            error_message=str(exc) or "Dify request timed out",
            error_category=ERROR_CATEGORY_RETRYABLE,
        )
    except httpx.HTTPError as exc:
        return ProviderOutcome(
            ok=False,
            raw_request=raw_request,
            error_code="network_error",
            error_message=str(exc) or "Dify network error",
            error_category=ERROR_CATEGORY_RETRYABLE,
        )

    raw_response: dict[str, Any]
    try:
        raw_response = response.json()
        if not isinstance(raw_response, dict):
            raw_response = {"body": raw_response}
    except ValueError:
        raw_response = {"body": response.text}

    if response.status_code >= 400:
        error_code, error_category = classify_http_error(response.status_code)
        message = None
        if isinstance(raw_response, dict):
            message = (
                raw_response.get("message")
                or raw_response.get("error")
                or raw_response.get("code")
            )
        return _with_run_ids(
            ProviderOutcome(
                ok=False,
                raw_request=raw_request,
                raw_response=raw_response,
                error_code=error_code,
                error_message=str(message or f"HTTP {response.status_code}"),
                error_category=error_category,
                http_status=response.status_code,
            )
        )

    outputs = _extract_outputs(raw_response)
    return _with_run_ids(
        ProviderOutcome(
            ok=True,
            result=outputs,
            raw_request=raw_request,
            raw_response=raw_response,
            http_status=response.status_code,
        )
    )


def _structured_jd_for_score(
    step1_outputs: dict[str, Any],
    jd_only: dict[str, Any],
) -> dict[str, Any]:
    """Prefer JD结构化原始 structured_jd，缺失字段时用规范化结果补齐。"""
    result_blob = _parse_jsonish(step1_outputs.get("result"))
    original: Any = None
    if isinstance(result_blob, dict):
        original = result_blob.get("structured_jd")
        if isinstance(original, str):
            original = _parse_jsonish(original)
    if not isinstance(original, dict):
        original = {}

    return {
        "responsibilities": list(
            original.get("responsibilities") or jd_only.get("responsibilities") or []
        ),
        "requirements": list(
            original.get("requirements") or jd_only.get("requirements") or []
        ),
        "must_have": list(original.get("must_have") or jd_only.get("must_have") or []),
        "nice_to_have": list(
            original.get("nice_to_have") or jd_only.get("nice_to_have") or []
        ),
        "plus_items_for_diff": str(original.get("plus_items_for_diff") or ""),
        "skill_keywords": list(
            original.get("skill_keywords")
            or original.get("skills")
            or jd_only.get("skills")
            or []
        ),
        "must_have_and_plus": str(original.get("must_have_and_plus") or ""),
    }


def _is_flaky_score_error(message: str) -> bool:
    text = (message or "").lower()
    return (
        "dimension_count_invalid" in text
        or "维度数量" in (message or "")
        or "dimensions must not be empty" in text
    )


def _score_error_message(outputs: dict[str, Any], fallback: str) -> str:
    result_payload = _parse_jsonish(outputs.get("result"))
    if isinstance(result_payload, dict) and result_payload.get("error"):
        return str(result_payload["error"])
    dims_payload = _parse_jsonish(outputs.get("final_dimensions"))
    if isinstance(dims_payload, dict) and dims_payload.get("error"):
        return str(dims_payload["error"])
    return fallback


async def _run_dify_jd_parse_chain(
    input_snapshot: dict[str, Any],
) -> ProviderOutcome:
    """JD_PARSE = JD结构化 → 能力维度生成 (two sequential workflows)."""
    step1_attempts: list[ProviderOutcome] = []
    jd_only: dict[str, Any] | None = None
    last_jd_error = ""

    # JD结构化偶发空数组；有正文时重试第一步
    for _ in range(2):
        step1 = await _post_workflow(
            task_type=TASK_TYPE_JD_PARSE,
            input_snapshot=input_snapshot,
        )
        step1_attempts.append(step1)
        if not step1.ok or not isinstance(step1.result, dict):
            last_jd_error = step1.error_message or "JD结构化失败"
            if step1.error_category == ERROR_CATEGORY_RETRYABLE:
                continue
            return step1

        try:
            jd_normalized = normalize_dify_outputs(TASK_TYPE_JD_PARSE, step1.result)
            if jd_result_is_empty(jd_normalized):
                hint = ""
                if looks_like_score_outputs(step1.result):
                    hint = (
                        "；当前返回的是能力维度结果而非 JD 结构化，"
                        "请配置 DIFY_JD_PARSE_API_KEY 为「JD结构化」应用的 API Key"
                    )
                last_jd_error = f"JD结构化未返回有效内容{hint}"
                has_body = bool(
                    extract_jd_content_from_outputs(step1.result)
                    or str(input_snapshot.get("raw_jd_text") or "").strip()
                )
                if has_body and not looks_like_score_outputs(step1.result):
                    continue
                return _with_run_ids(
                    ProviderOutcome(
                        ok=False,
                        raw_request={"steps": [a.raw_request for a in step1_attempts]},
                        raw_response={"steps": [a.raw_response for a in step1_attempts]},
                        error_code="output_validation_failed",
                        error_message=last_jd_error,
                        error_category=ERROR_CATEGORY_NON_RETRYABLE,
                        http_status=step1.http_status,
                    )
                )
            jd_only = validate_ai_result(TASK_TYPE_JD_PARSE, jd_normalized)
            break
        except (ValidationError, ValueError) as exc:
            last_jd_error = f"JD结构化输出校验失败: {exc}"
            continue

    if jd_only is None:
        step1 = step1_attempts[-1]
        return _with_run_ids(
            ProviderOutcome(
                ok=False,
                raw_request={"steps": [a.raw_request for a in step1_attempts]},
                raw_response={"steps": [a.raw_response for a in step1_attempts]},
                error_code="output_validation_failed",
                error_message=f"{last_jd_error}；可点击重试",
                error_category=ERROR_CATEGORY_RETRYABLE,
                http_status=step1.http_status,
            )
        )

    step1 = step1_attempts[-1]
    result_meta = _parse_jsonish(step1.result.get("result")) or {}
    jd_content = extract_jd_content_from_outputs(step1.result) or str(
        input_snapshot.get("raw_jd_text") or ""
    )
    score_snapshot = {
        "job_title": input_snapshot.get("job_title")
        or result_meta.get("job_title"),
        "department": input_snapshot.get("department")
        or result_meta.get("department"),
        "jd_content": jd_content,
        "structured_jd": _structured_jd_for_score(step1.result, jd_only),
    }

    step2_attempts: list[ProviderOutcome] = []
    last_score_error = ""
    for _ in range(2):
        step2 = await _post_workflow(
            task_type=TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
            input_snapshot=score_snapshot,
        )
        step2_attempts.append(step2)
        if not step2.ok or not isinstance(step2.result, dict):
            last_score_error = step2.error_message or "能力维度生成失败"
            if step2.error_category == ERROR_CATEGORY_RETRYABLE:
                continue
            break

        try:
            dims_normalized = normalize_dify_outputs(
                TASK_TYPE_SCORE_DIMENSION_RECOMMEND, step2.result
            )
            dims_only = validate_ai_result(
                TASK_TYPE_SCORE_DIMENSION_RECOMMEND, dims_normalized
            )
            combined = {**jd_only, "dimensions": dims_only["dimensions"]}
            result = validate_ai_result(TASK_TYPE_JD_PARSE, combined)
            return _with_run_ids(
                ProviderOutcome(
                    ok=True,
                    result=result,
                    raw_request={
                        "steps": [
                            *[a.raw_request for a in step1_attempts],
                            *[a.raw_request for a in step2_attempts],
                        ]
                    },
                    raw_response={
                        "steps": [
                            *[a.raw_response for a in step1_attempts],
                            *[a.raw_response for a in step2_attempts],
                        ]
                    },
                    http_status=step2.http_status or step1.http_status,
                )
            )
        except (ValidationError, ValueError) as exc:
            detail = _score_error_message(step2.result, str(exc))
            last_score_error = detail
            if _is_flaky_score_error(detail) or _is_flaky_score_error(str(exc)):
                continue
            break

    step2 = step2_attempts[-1]
    flaky = _is_flaky_score_error(last_score_error)
    return _with_run_ids(
        ProviderOutcome(
            ok=False,
            raw_request={
                "steps": [
                    *[a.raw_request for a in step1_attempts],
                    *[a.raw_request for a in step2_attempts],
                ]
            },
            raw_response={
                "steps": [
                    *[a.raw_response for a in step1_attempts],
                    *[a.raw_response for a in step2_attempts],
                ]
            },
            error_code="output_validation_failed",
            error_message=(
                f"能力维度生成失败（JD结构化已成功）: {last_score_error}"
                + ("；可点击重试" if flaky else "")
            ),
            error_category=(
                ERROR_CATEGORY_RETRYABLE if flaky else ERROR_CATEGORY_NON_RETRYABLE
            ),
            http_status=step2.http_status or step1.http_status,
        )
    )


def _sha256_json(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _redact_question_live_raw_request(
    raw_request: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(raw_request, dict):
        return raw_request
    inputs = raw_request.get("inputs")
    redacted = {
        key: value
        for key, value in raw_request.items()
        if key not in {"inputs", "api_key_suffix", "url"}
    }
    if isinstance(inputs, dict):
        redacted["input_field_names"] = sorted(inputs.keys())
        for key, value in inputs.items():
            redacted[f"{key}_sha256"] = _sha256_json(value)
    return redacted


def _redact_question_live_raw_response(
    raw_response: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(raw_response, dict):
        return raw_response
    run_id, req_id = extract_dify_run_ids(raw_response)
    payload: dict[str, Any] = {}
    data = raw_response.get("data") if isinstance(raw_response.get("data"), dict) else {}
    if run_id:
        payload["provider_run_id"] = run_id
    elif isinstance(data, dict) and data.get("id"):
        payload["provider_run_id"] = data.get("id")
    if req_id:
        payload["request_id"] = req_id
    if isinstance(data, dict) and data.get("status"):
        payload["status"] = data.get("status")
    return payload


def _redact_question_live_outcome(outcome: ProviderOutcome) -> ProviderOutcome:
    return ProviderOutcome(
        ok=outcome.ok,
        result=outcome.result,
        raw_request=_redact_question_live_raw_request(outcome.raw_request),
        raw_response=_redact_question_live_raw_response(outcome.raw_response),
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        error_category=outcome.error_category,
        http_status=outcome.http_status,
        provider_run_id=outcome.provider_run_id,
        request_id=outcome.request_id,
        extra=outcome.extra,
    )


async def run_dify(
    *,
    task_type: str,
    input_snapshot: dict[str, Any],
) -> ProviderOutcome:
    if task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        from app.services.ai_providers.mock import run_mock

        return await run_mock(task_type=task_type, input_snapshot=input_snapshot)

    if task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        settings = get_settings()
        inputs = build_dify_inputs(task_type, input_snapshot)
        decision = interview_question_live_http_allowed(settings, inputs)
        if decision.fallback_mock:
            from app.services.ai_providers.mock import run_mock

            return await run_mock(task_type=task_type, input_snapshot=input_snapshot)
        if not decision.allow_http:
            return ProviderOutcome(
                ok=False,
                error_code=decision.error_code,
                error_message=decision.reason or decision.error_code,
                error_category=ERROR_CATEGORY_NON_RETRYABLE,
            )

    if task_type in {TASK_TYPE_RESUME_PARSE, TASK_TYPE_RESUME_SCORE}:
        if not _resume_dify_configured(task_type):
            # 未配置简历专用 Dify 凭据时回退 mock，便于阶段 5 联调
            from app.services.ai_providers.mock import run_mock

            return await run_mock(task_type=task_type, input_snapshot=input_snapshot)
        if task_type == TASK_TYPE_RESUME_SCORE and not (
            get_settings().dify_resume_score_api_key_secret.get_secret_value().strip()
            and get_settings().DIFY_RESUME_SCORE_WORKFLOW_ID.strip()
        ):
            return ProviderOutcome(
                ok=False,
                error_code="dify_not_configured",
                error_message=(
                    "RESUME_SCORE requires DIFY_RESUME_SCORE_API_KEY and "
                    "DIFY_RESUME_SCORE_WORKFLOW_ID"
                ),
                error_category=ERROR_CATEGORY_NON_RETRYABLE,
            )

    if task_type == TASK_TYPE_JD_PARSE:
        return await _run_dify_jd_parse_chain(input_snapshot)

    step = await _post_workflow(task_type=task_type, input_snapshot=input_snapshot)
    if task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        step = _redact_question_live_outcome(step)
    if not step.ok or not isinstance(step.result, dict):
        return step

    try:
        normalized = normalize_dify_outputs(task_type, step.result)
        result = validate_ai_result(task_type, normalized)
    except (ValidationError, ValueError) as exc:
        message = str(exc)
        if isinstance(step.result, dict):
            message = _score_error_message(step.result, message)
        flaky = (
            task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND
            and _is_flaky_score_error(message)
        )
        return ProviderOutcome(
            ok=False,
            raw_request=step.raw_request,
            raw_response=step.raw_response,
            error_code="output_validation_failed",
            error_message=message,
            error_category=(
                ERROR_CATEGORY_RETRYABLE if flaky else ERROR_CATEGORY_NON_RETRYABLE
            ),
            http_status=step.http_status,
        )

    return ProviderOutcome(
        ok=True,
        result=result,
        raw_request=step.raw_request,
        raw_response=step.raw_response,
        http_status=step.http_status,
    )
