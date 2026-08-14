#!/usr/bin/env python3
"""Generate Dify workflow YAMLs aligned with backend RESUME_PARSE / RESUME_SCORE."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent

RESUME_PARSE_CODE = r'''
import json
import re


def _parse(raw):
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    text = str(raw).strip()
    if not text:
        return {}
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except Exception:
                return {}
        return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _as_str(value):
    return str(value or "").strip()


def main(structured_out, llm_text: str, resume_text: str, candidate_id: str) -> dict:
    try:
        payload = structured_out if isinstance(structured_out, dict) and structured_out else _parse(llm_text)
        if not isinstance(payload, dict):
            payload = {}

        name = _as_str(payload.get("name"))
        standardized = _as_str(payload.get("standardized_text")) or _as_str(resume_text)
        if not standardized:
            err = {"error": "standardized_text_empty"}
            return {"result": json.dumps(err, ensure_ascii=False)}

        result = {
            "name": name,
            "phone": _as_str(payload.get("phone")),
            "email": _as_str(payload.get("email")),
            "years_of_experience": payload.get("years_of_experience"),
            "education": _as_list(payload.get("education")),
            "work_experience": _as_list(payload.get("work_experience") or payload.get("experiences")),
            "projects": _as_list(payload.get("projects")),
            "skills": [str(x).strip() for x in _as_list(payload.get("skills")) if str(x).strip()],
            "standardized_text": standardized,
            "candidate_id": _as_str(candidate_id),
        }
        return {"result": json.dumps(result, ensure_ascii=False)}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}, ensure_ascii=False)}
'''.lstrip()

RESUME_SCORE_CODE = r'''
import json
import re


def _parse(raw):
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    text = str(raw).strip()
    if not text:
        return {}
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except Exception:
                return {}
        return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _as_str(value):
    return str(value or "").strip()


def _dim_signal(payload):
    """Prefer the candidate payload that actually carries scored dimensions."""
    if not isinstance(payload, dict):
        return -1
    dims = _as_list(payload.get("dimensions"))
    signal = 0
    for item in dims:
        if not isinstance(item, dict):
            continue
        signal += 1
        try:
            if float(item.get("score") or 0) > 0:
                signal += 10
        except Exception:
            pass
        if _as_str(item.get("evidence") or item.get("basis")):
            signal += 2
        if _as_str(payload.get("summary")) or _as_str(payload.get("recommendation")):
            signal += 1
    return signal


def _pick_payload(llm_text: str):
    candidates = []
    text_payload = _parse(llm_text)
    if isinstance(text_payload, dict):
        candidates.append(text_payload)
        for key in ("data", "result", "output"):
            nested = text_payload.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
            elif isinstance(nested, str):
                candidates.append(_parse(nested))
    best = {}
    best_signal = -1
    for c in candidates:
        if not isinstance(c, dict):
            continue
        sig = _dim_signal(c)
        if sig > best_signal:
            best = c
            best_signal = sig
    return best if isinstance(best, dict) else {}


def main(llm_text: str, dimensions_json: str, job_title: str, candidate_id: str) -> dict:
    try:
        dims_src = _parse(dimensions_json)
        if isinstance(dims_src, dict):
            expected = dims_src.get("dimensions") if isinstance(dims_src.get("dimensions"), list) else None
            if expected is None and all(k in dims_src for k in ("name", "weight")):
                expected = [dims_src]
            if expected is None:
                expected = []
        elif isinstance(dims_src, list):
            expected = dims_src
        else:
            expected = []

        if not expected:
            parsed = _parse(dimensions_json)
            if isinstance(parsed, list):
                expected = parsed
            elif isinstance(parsed, dict) and isinstance(parsed.get("dimensions"), list):
                expected = parsed["dimensions"]

        expected_names = [
            _as_str(d.get("name"))
            for d in expected
            if isinstance(d, dict) and _as_str(d.get("name"))
        ]
        weight_map = {
            _as_str(d.get("name")): float(d.get("weight") or 0)
            for d in expected
            if isinstance(d, dict) and _as_str(d.get("name"))
        }
        desc_map = {
            _as_str(d.get("name")): _as_str(d.get("description"))
            for d in expected
            if isinstance(d, dict) and _as_str(d.get("name"))
        }

        payload = _pick_payload(llm_text)

        raw_items = [x for x in _as_list(payload.get("dimensions")) if isinstance(x, dict)]
        scored = []
        for item in raw_items:
            name = _as_str(item.get("name"))
            if not name:
                continue
            if expected_names and name not in weight_map:
                continue
            try:
                score = float(item.get("score") or 0)
            except Exception:
                score = 0.0
            score = max(0.0, min(100.0, score))
            scored.append(
                {
                    "name": name,
                    "description": _as_str(item.get("description")) or desc_map.get(name, ""),
                    "weight": float(weight_map.get(name, item.get("weight") or 0)),
                    "score": score,
                    "evidence": _as_str(item.get("evidence") or item.get("basis")),
                    "gap": _as_str(item.get("gap")),
                    "risk": _as_str(item.get("risk")),
                }
            )

        # Name mismatch fallback: align by order when counts match
        if expected_names and len(scored) == 0 and len(raw_items) == len(expected_names):
            for idx, item in enumerate(raw_items):
                name = expected_names[idx]
                try:
                    score = float(item.get("score") or 0)
                except Exception:
                    score = 0.0
                score = max(0.0, min(100.0, score))
                scored.append(
                    {
                        "name": name,
                        "description": _as_str(item.get("description")) or desc_map.get(name, ""),
                        "weight": float(weight_map.get(name, 0)),
                        "score": score,
                        "evidence": _as_str(item.get("evidence") or item.get("basis")),
                        "gap": _as_str(item.get("gap")),
                        "risk": _as_str(item.get("risk")),
                    }
                )

        have = {d["name"] for d in scored}
        for name in expected_names:
            if name not in have:
                scored.append(
                    {
                        "name": name,
                        "description": desc_map.get(name, ""),
                        "weight": float(weight_map.get(name, 0)),
                        "score": 0.0,
                        "evidence": "简历中未找到足够依据",
                        "gap": "信息不足，无法评估",
                        "risk": "",
                    }
                )

        if expected_names and len(scored) == 0:
            err = {"error": "no_dimension_scores", "job_title": job_title or ""}
            return {"result": json.dumps(err, ensure_ascii=False)}

        result = {
            "dimensions": scored,
            "recommendation": _as_str(payload.get("recommendation")),
            "score_band": _as_str(payload.get("score_band")),
            "must_have_check": [str(x) for x in _as_list(payload.get("must_have_check"))],
            "risks": [str(x) for x in _as_list(payload.get("risks"))],
            "summary": _as_str(payload.get("summary")),
            "information_insufficient": bool(payload.get("information_insufficient") or False),
            "candidate_id": _as_str(candidate_id),
            "job_title": _as_str(job_title),
        }
        # Surface empty-LLM cases so Dify UI shows why scores fell back to 0
        if scored and all(float(d.get("score") or 0) == 0 for d in scored) and not result["summary"]:
            preview = str(llm_text or "").strip().replace("\n", " ")[:240]
            result["summary"] = (
                f"[debug] llm_chars={len(str(llm_text or ''))} "
                f"parsed_dims={len(raw_items)} "
                f"preview={preview or '<empty>'}"
            )
            result["information_insufficient"] = True
        return {"result": json.dumps(result, ensure_ascii=False)}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}, ensure_ascii=False)}
'''.lstrip()


def dump_yaml(data: dict) -> str:
    # Minimal YAML emitter tailored for Dify DSL (keep unicode, no aliases)
    import yaml

    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )


def base_features(file_upload: bool = False) -> dict:
    return {
        "file_upload": {
            "allowed_file_extensions": [".TXT", ".PDF", ".DOCX", ".DOC"] if file_upload else [],
            "allowed_file_types": ["document"] if file_upload else [],
            "allowed_file_upload_methods": ["local_file", "remote_url"],
            "enabled": file_upload,
            "fileUploadConfig": {
                "attachment_image_file_size_limit": 2,
                "audio_file_size_limit": 50,
                "batch_count_limit": 5,
                "file_size_limit": 15,
                "file_upload_limit": 50,
                "image_file_batch_limit": 10,
                "image_file_size_limit": 10,
                "knowledge_file_size_limit": 15,
                "single_chunk_attachment_limit": 10,
                "video_file_size_limit": 100,
                "workflow_file_upload_limit": 10,
            },
            "image": {
                "enabled": False,
                "number_limits": 3,
                "transfer_methods": ["local_file", "remote_url"],
            },
            "number_limits": 1 if file_upload else 3,
        },
        "opening_statement": "",
        "retriever_resource": {"enabled": True},
        "sensitive_word_avoidance": {"enabled": False},
        "speech_to_text": {"enabled": False},
        "suggested_questions": [],
        "suggested_questions_after_answer": {"enabled": False},
        "text_to_speech": {"enabled": False, "language": "", "voice": ""},
    }


DEPS = [
    {
        "current_identifier": None,
        "type": "marketplace",
        "value": {
            "marketplace_plugin_unique_identifier": (
                "langgenius/deepseek:0.0.19@"
                "1aa3c64e2f50179afe8410ad79412dab92bf5919a27da8e2b13071b5e5f8ce81"
            ),
            "version": None,
        },
    }
]


def make_parse() -> dict:
    start_id, llm_id, code_id, end_id = (
        "1786500001001",
        "1786500001002",
        "1786500001003",
        "1786500001004",
    )
    return {
        "app": {
            "description": (
                "阶段5·简历解析。接收系统提取的 resume_text，输出结构化简历 JSON，"
                "供 HR 校对确认后进入多维评分。不得虚构联系方式与经历。"
            ),
            "icon": "📄",
            "icon_background": "#E8F3FF",
            "icon_type": "emoji",
            "mode": "workflow",
            "name": "简历解析",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": DEPS,
        "kind": "app",
        "version": "0.7.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": base_features(False),
            "graph": {
                "edges": [
                    {
                        "data": {
                            "isInIteration": False,
                            "isInLoop": False,
                            "sourceType": "start",
                            "targetType": "llm",
                        },
                        "id": "e1",
                        "source": start_id,
                        "sourceHandle": "source",
                        "target": llm_id,
                        "targetHandle": "target",
                        "type": "custom",
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "isInIteration": False,
                            "isInLoop": False,
                            "sourceType": "llm",
                            "targetType": "code",
                        },
                        "id": "e2",
                        "source": llm_id,
                        "sourceHandle": "source",
                        "target": code_id,
                        "targetHandle": "target",
                        "type": "custom",
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "isInIteration": False,
                            "isInLoop": False,
                            "sourceType": "code",
                            "targetType": "end",
                        },
                        "id": "e3",
                        "source": code_id,
                        "sourceHandle": "source",
                        "target": end_id,
                        "targetHandle": "target",
                        "type": "custom",
                        "zIndex": 0,
                    },
                ],
                "nodes": [
                    {
                        "data": {
                            "selected": False,
                            "title": "用户输入",
                            "type": "start",
                            "variables": [
                                {
                                    "default": "",
                                    "hint": "系统提取的简历纯文本（必填）",
                                    "label": "简历文本",
                                    "options": [],
                                    "placeholder": "",
                                    "required": True,
                                    "type": "paragraph",
                                    "variable": "resume_text",
                                },
                                {
                                    "default": "",
                                    "hint": "候选人业务编号，可为空",
                                    "label": "候选人编号",
                                    "options": [],
                                    "placeholder": "",
                                    "required": False,
                                    "type": "text-input",
                                    "variable": "candidate_id",
                                },
                            ],
                        },
                        "height": 140,
                        "id": start_id,
                        "position": {"x": 40, "y": 280},
                        "positionAbsolute": {"x": 40, "y": 280},
                        "selected": False,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "context": {
                                "enabled": True,
                                "variable_selector": [start_id, "resume_text"],
                            },
                            "model": {
                                "completion_params": {"temperature": 0.1},
                                "mode": "chat",
                                "name": "deepseek-v4-flash",
                                "provider": "langgenius/deepseek/deepseek",
                            },
                            "prompt_template": [
                                {
                                    "id": "p1",
                                    "role": "system",
                                    "text": (
                                        "你是招聘简历解析助手。只根据给定简历原文提取事实，禁止臆造。\n\n"
                                        "# 规则\n"
                                        "1. 姓名无法确认时输出空字符串（系统会标记待补充），不得编造\n"
                                        "2. 手机号/邮箱仅在原文明确出现时填写\n"
                                        "3. 工作经历、项目经历缺失时返回空数组，不要编造\n"
                                        "4. skills 仅提取原文明确技能关键词\n"
                                        "5. standardized_text 必须输出清洗后的完整简历正文，不能为空\n"
                                        "6. 只输出合法 JSON，不要 markdown\n\n"
                                        "输出 schema：\n"
                                        "{\n"
                                        '  "name": "",\n'
                                        '  "phone": "",\n'
                                        '  "email": "",\n'
                                        '  "years_of_experience": null,\n'
                                        '  "education": [{"school":"","degree":"","major":"","start":"","end":"","missing":false}],\n'
                                        '  "work_experience": [{"company":"","title":"","start":"","end":"","description":"","missing":false,"source":"ai"}],\n'
                                        '  "projects": [{"name":"","role":"","start":"","end":"","description":"","missing":false,"source":"ai"}],\n'
                                        '  "skills": ["Vue"],\n'
                                        '  "standardized_text": ""\n'
                                        "}\n\n"
                                        f"# 候选人编号\n{{{{#{start_id}.candidate_id#}}}}\n\n"
                                        f"# 简历原文\n{{{{#{start_id}.resume_text#}}}}"
                                    ),
                                }
                            ],
                            "selected": True,
                            "structured_output": {
                                "schema": {
                                    "$schema": "http://json-schema.org/draft-07/schema#",
                                    "additionalProperties": False,
                                    "type": "object",
                                    "required": ["standardized_text"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "phone": {"type": "string"},
                                        "email": {"type": "string"},
                                        "years_of_experience": {
                                            "type": ["number", "null"]
                                        },
                                        "education": {"type": "array", "items": {"type": "object"}},
                                        "work_experience": {
                                            "type": "array",
                                            "items": {"type": "object"},
                                        },
                                        "projects": {"type": "array", "items": {"type": "object"}},
                                        "skills": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "standardized_text": {"type": "string"},
                                    },
                                }
                            },
                            "structured_output_enabled": True,
                            "title": "LLM·简历结构化",
                            "type": "llm",
                            "vision": {"enabled": False},
                        },
                        "height": 88,
                        "id": llm_id,
                        "position": {"x": 360, "y": 280},
                        "positionAbsolute": {"x": 360, "y": 280},
                        "selected": True,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "code": RESUME_PARSE_CODE,
                            "code_language": "python3",
                            "outputs": {
                                "result": {"children": None, "type": "string"},
                            },
                            "selected": False,
                            "title": "代码·规范化",
                            "type": "code",
                            "variables": [
                                {
                                    "value_selector": [llm_id, "structured_output"],
                                    "value_type": "object",
                                    "variable": "structured_out",
                                },
                                {
                                    "value_selector": [llm_id, "text"],
                                    "value_type": "string",
                                    "variable": "llm_text",
                                },
                                {
                                    "value_selector": [start_id, "resume_text"],
                                    "value_type": "string",
                                    "variable": "resume_text",
                                },
                                {
                                    "value_selector": [start_id, "candidate_id"],
                                    "value_type": "string",
                                    "variable": "candidate_id",
                                },
                            ],
                        },
                        "height": 52,
                        "id": code_id,
                        "position": {"x": 680, "y": 280},
                        "positionAbsolute": {"x": 680, "y": 280},
                        "selected": False,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "outputs": [
                                {
                                    "value_selector": [code_id, "result"],
                                    "value_type": "string",
                                    "variable": "result",
                                }
                            ],
                            "selected": False,
                            "title": "输出",
                            "type": "end",
                        },
                        "height": 90,
                        "id": end_id,
                        "position": {"x": 1000, "y": 280},
                        "positionAbsolute": {"x": 1000, "y": 280},
                        "selected": False,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                ],
                "viewport": {"x": 80, "y": 80, "zoom": 0.7},
            },
            "rag_pipeline_variables": [],
        },
    }


def make_score() -> dict:
    start_id, llm_id, code_id, end_id = (
        "1786600001001",
        "1786600001002",
        "1786600001003",
        "1786600001004",
    )
    return {
        "app": {
            "description": (
                "阶段5·简历多维评分。输入岗位JD、能力维度(name/description/weight)、"
                "已确认简历文本，输出各维度0~100分与辅助建议。"
                "不使用面试评分锚点；加权总分由后端按岗位权重复算。"
            ),
            "icon": "📊",
            "icon_background": "#FFF4E8",
            "icon_type": "emoji",
            "mode": "workflow",
            "name": "简历多维评分",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],  # 避免 marketplace 插件 hash 与云端不一致导致首次发布静默失败；导入后在 LLM 节点重选模型
        "kind": "app",
        "version": "0.7.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": base_features(False),
            "graph": {
                "edges": [
                    {
                        "data": {
                            "isInIteration": False,
                            "isInLoop": False,
                            "sourceType": "start",
                            "targetType": "llm",
                        },
                        "id": "e1",
                        "source": start_id,
                        "sourceHandle": "source",
                        "target": llm_id,
                        "targetHandle": "target",
                        "type": "custom",
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "isInIteration": False,
                            "isInLoop": False,
                            "sourceType": "llm",
                            "targetType": "code",
                        },
                        "id": "e2",
                        "source": llm_id,
                        "sourceHandle": "source",
                        "target": code_id,
                        "targetHandle": "target",
                        "type": "custom",
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "isInIteration": False,
                            "isInLoop": False,
                            "sourceType": "code",
                            "targetType": "end",
                        },
                        "id": "e3",
                        "source": code_id,
                        "sourceHandle": "source",
                        "target": end_id,
                        "targetHandle": "target",
                        "type": "custom",
                        "zIndex": 0,
                    },
                ],
                "nodes": [
                    {
                        "data": {
                            "selected": False,
                            "title": "开始",
                            "type": "start",
                            "variables": [
                                {
                                    "default": "",
                                    "hint": "应聘绑定的已发布岗位版本 JD 正文",
                                    "label": "岗位JD",
                                    "options": [],
                                    "placeholder": "",
                                    "required": True,
                                    "type": "paragraph",
                                    "variable": "jd_content",
                                },
                                {
                                    "default": "",
                                    "hint": (
                                        "JSON数组，每项仅含 name/description/weight。"
                                        "与岗位能力维度生成结果一致，但不含 score_anchors"
                                    ),
                                    "label": "能力维度JSON",
                                    "options": [],
                                    "placeholder": "",
                                    "required": True,
                                    "type": "paragraph",
                                    "variable": "dimensions_json",
                                },
                                {
                                    "default": "",
                                    "hint": "人工确认后的简历标准化文本",
                                    "label": "简历文本",
                                    "options": [],
                                    "placeholder": "",
                                    "required": True,
                                    "type": "paragraph",
                                    "variable": "resume_text",
                                },
                                {
                                    "default": "",
                                    "label": "候选人编号",
                                    "options": [],
                                    "placeholder": "",
                                    "required": False,
                                    "type": "text-input",
                                    "variable": "candidate_id",
                                },
                                {
                                    "default": "",
                                    "label": "岗位名称",
                                    "options": [],
                                    "placeholder": "",
                                    "required": True,
                                    "type": "text-input",
                                    "variable": "job_title",
                                },
                            ],
                        },
                        "height": 220,
                        "id": start_id,
                        "position": {"x": 40, "y": 240},
                        "positionAbsolute": {"x": 40, "y": 240},
                        "selected": False,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "context": {
                                "enabled": False,
                                "variable_selector": [],
                            },
                            "model": {
                                "completion_params": {"temperature": 0.2},
                                "mode": "chat",
                                "name": "deepseek-v4-flash",
                                "provider": "langgenius/deepseek/deepseek",
                            },
                            "prompt_template": [
                                {
                                    "id": "p1",
                                    "role": "system",
                                    "text": (
                                        "你是资深招聘评估专家。请基于岗位JD与给定能力维度，对候选人简历打分。\n\n"
                                        "# 硬性规则\n"
                                        "1. dimensions 中的 name 必须与输入 dimensions_json 完全一致，禁止新增或改名\n"
                                        "2. 每个维度 score 为 0~100 的数值\n"
                                        "3. 只依据简历可验证信息；信息不足时降低分数，并在 gap 说明「信息不足」，"
                                        "同时将 information_insufficient=true；不得把信息缺失直接等同于能力不足\n"
                                        "4. 不要使用、不要输出、不要参考任何 1~5 分面试评分锚点\n"
                                        "5. weight 原样回传输入维度权重即可（最终加权总分由后端复算）\n"
                                        "6. recommendation / score_band 仅作辅助建议，不代表最终录用决定\n"
                                        "7. 只输出合法 JSON，不要 markdown 代码块，不要解释文字\n\n"
                                        "输出 schema：\n"
                                        "{\n"
                                        '  "dimensions": [{\n'
                                        '    "name": "与输入一致的维度名",\n'
                                        '    "description": "可回传输入描述",\n'
                                        '    "weight": 20,\n'
                                        '    "score": 75,\n'
                                        '    "evidence": "简历依据",\n'
                                        '    "gap": "差距或信息不足说明",\n'
                                        '    "risk": "风险（可空）"\n'
                                        "  }],\n"
                                        '  "recommendation": "建议面试|待定|谨慎|不建议",\n'
                                        '  "score_band": "A|B|C|D",\n'
                                        '  "must_have_check": ["必备项核验说明"],\n'
                                        '  "risks": ["风险点"],\n'
                                        '  "summary": "一句话总结",\n'
                                        '  "information_insufficient": false\n'
                                        "}"
                                    ),
                                },
                                {
                                    "id": "p2",
                                    "role": "user",
                                    "text": (
                                        f"岗位名称：{{{{#{start_id}.job_title#}}}}\n"
                                        f"候选人编号：{{{{#{start_id}.candidate_id#}}}}\n\n"
                                        f"能力维度(JSON)：\n{{{{#{start_id}.dimensions_json#}}}}\n\n"
                                        f"岗位JD：\n{{{{#{start_id}.jd_content#}}}}\n\n"
                                        f"已确认简历：\n{{{{#{start_id}.resume_text#}}}}\n\n"
                                        "请按 system 中的 schema 输出 JSON。"
                                    ),
                                },
                            ],
                            "selected": True,
                            "structured_output_enabled": False,
                            "title": "LLM·多维评分",
                            "type": "llm",
                            "vision": {"enabled": False},
                        },
                        "height": 88,
                        "id": llm_id,
                        "position": {"x": 360, "y": 240},
                        "positionAbsolute": {"x": 360, "y": 240},
                        "selected": True,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "code": RESUME_SCORE_CODE,
                            "code_language": "python3",
                            "outputs": {
                                "result": {"children": None, "type": "string"},
                            },
                            "selected": False,
                            "title": "代码·维度对齐",
                            "type": "code",
                            "variables": [
                                {
                                    "value_selector": [llm_id, "text"],
                                    "value_type": "string",
                                    "variable": "llm_text",
                                },
                                {
                                    "value_selector": [start_id, "dimensions_json"],
                                    "value_type": "string",
                                    "variable": "dimensions_json",
                                },
                                {
                                    "value_selector": [start_id, "job_title"],
                                    "value_type": "string",
                                    "variable": "job_title",
                                },
                                {
                                    "value_selector": [start_id, "candidate_id"],
                                    "value_type": "string",
                                    "variable": "candidate_id",
                                },
                            ],
                        },
                        "height": 52,
                        "id": code_id,
                        "position": {"x": 680, "y": 240},
                        "positionAbsolute": {"x": 680, "y": 240},
                        "selected": False,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                    {
                        "data": {
                            "outputs": [
                                {
                                    "value_selector": [code_id, "result"],
                                    "value_type": "string",
                                    "variable": "result",
                                }
                            ],
                            "selected": False,
                            "title": "结束",
                            "type": "end",
                        },
                        "height": 90,
                        "id": end_id,
                        "position": {"x": 1000, "y": 240},
                        "positionAbsolute": {"x": 1000, "y": 240},
                        "selected": False,
                        "type": "custom",
                        "width": 242,
                        "zIndex": 0,
                    },
                ],
                "viewport": {"x": 60, "y": 60, "zoom": 0.7},
            },
            "rag_pipeline_variables": [],
        },
    }


def main() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys

        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml", "-q"])
        import yaml  # noqa: F401

    parse_path = OUT / "简历解析.yml"
    score_path = OUT / "简历多维评分.yml"
    parse_path.write_text(dump_yaml(make_parse()), encoding="utf-8")
    score_path.write_text(dump_yaml(make_score()), encoding="utf-8")
    print(f"wrote {parse_path}")
    print(f"wrote {score_path}")
    # also mirror to Downloads for quick import
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        (downloads / "简历解析.yml").write_text(parse_path.read_text(encoding="utf-8"), encoding="utf-8")
        (downloads / "简历多维评分.yml").write_text(
            score_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        print(f"copied to {downloads}")


if __name__ == "__main__":
    main()
