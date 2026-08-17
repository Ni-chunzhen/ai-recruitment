"""RED/GREEN tests for stage 8 dimension snapshot, scores and evidence validation."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

SECRET = "TOP_SECRET_TRANSCRIPT_98765"
TRANSCRIPT_VERSION_ID = UUID("22222222-2222-2222-2222-222222222222")
SEGMENT_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SEGMENT_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _snapshot_item(
    key: str,
    *,
    name: str = "沟通协作",
    weight: str = "50.00",
    anchors: list[str] | None = None,
    description: str = "跨团队沟通",
    display_order: int | None = None,
) -> dict:
    order = display_order if display_order is not None else int(key[1:])
    return {
        "dimension_key": key,
        "display_order": order,
        "name": name,
        "weight": Decimal(weight),
        "description": description,
        "anchors": anchors
        if anchors is not None
        else ["不足", "一般", "达标", "良好", "优秀"],
    }


def _two_dims() -> list:
    from app.schemas.interview_ai import InterviewDimensionSnapshot

    return [
        InterviewDimensionSnapshot.model_validate(_snapshot_item("D001", weight="40.00")),
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D002", name="专业能力", weight="60.00")
        ),
    ]


def _segment(
    *,
    segment_id: UUID = SEGMENT_A,
    segment_no: int = 1,
    included: bool = True,
    text: str = "我当时先对齐目标再推动方案。",
    transcript_version_id: UUID = TRANSCRIPT_VERSION_ID,
):
    from app.schemas.interview_ai import InterviewEvidenceSegment

    return InterviewEvidenceSegment(
        id=segment_id,
        transcript_version_id=transcript_version_id,
        segment_no=segment_no,
        is_included_in_analysis=included,
        text=text,
    )


def test_allocate_dimension_key_zero_padded() -> None:
    from app.services.interview_ai_validation import allocate_dimension_key

    assert allocate_dimension_key(1) == "D001"
    assert allocate_dimension_key(9) == "D009"
    assert allocate_dimension_key(10) == "D010"
    assert allocate_dimension_key(999) == "D999"


@pytest.mark.parametrize("position", (0, -1, 1000))
def test_allocate_dimension_key_rejects_out_of_range(position: int) -> None:
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        allocate_dimension_key,
    )

    with pytest.raises(AIOutputValidationError) as exc_info:
        allocate_dimension_key(position)
    assert exc_info.value.code
    assert SECRET not in str(exc_info.value)


def test_build_dimension_snapshot_preserves_order_and_allows_duplicate_names() -> None:
    from app.services.interview_ai_validation import build_dimension_snapshot

    raw = [
        {
            "name": " 沟通 ",
            "weight": "40.00",
            "anchors": ["a", "b"],
        },
        {
            "name": "沟通",
            "weight": 60,
            "description": "第二项",
            "anchors": ["1", "2", "3", "4", "5"],
        },
    ]
    original = deepcopy(raw)
    snapshot = build_dimension_snapshot(raw)
    assert raw == original
    assert [item.dimension_key for item in snapshot] == ["D001", "D002"]
    assert [item.display_order for item in snapshot] == [1, 2]
    assert snapshot[0].name == "沟通"
    assert snapshot[0].description == ""
    assert snapshot[0].anchors == ["a", "b"]
    assert snapshot[1].name == "沟通"
    assert snapshot[1].weight == Decimal("60")


def test_build_dimension_snapshot_rejects_empty_numeric_anchors_and_oversize() -> None:
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        build_dimension_snapshot,
    )

    with pytest.raises(AIOutputValidationError):
        build_dimension_snapshot([])
    with pytest.raises(AIOutputValidationError):
        build_dimension_snapshot([{"name": "   ", "weight": 100, "anchors": ["a"]}])
    with pytest.raises(AIOutputValidationError):
        build_dimension_snapshot(
            [{"name": "专业", "weight": 100, "anchors": [1, "二"]}]
        )
    with pytest.raises(AIOutputValidationError):
        build_dimension_snapshot(
            [{"name": f"D{i}", "weight": 2, "anchors": ["a"]} for i in range(51)]
        )


def test_require_complete_analysis_anchors() -> None:
    from app.schemas.interview_ai import InterviewDimensionSnapshot
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        require_complete_analysis_anchors,
    )

    ok = [
        InterviewDimensionSnapshot.model_validate(_snapshot_item("D001")),
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D002", name="专业能力", weight="50.00")
        ),
    ]
    require_complete_analysis_anchors(ok)

    short = [
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D001", anchors=["不足", "一般", "达标", "良好"])
        )
    ]
    with pytest.raises(AIOutputValidationError) as exc_info:
        require_complete_analysis_anchors(short)
    assert "D001" in str(exc_info.value)
    assert "沟通协作" in str(exc_info.value)
    assert SECRET not in str(exc_info.value)

    blank = [
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D001", anchors=["不足", "一般", "达标", "良好", "  "])
        )
    ]
    with pytest.raises(AIOutputValidationError):
        require_complete_analysis_anchors(blank)

    dup = [
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D001", anchors=["不足", "一般", "达标", "良好", "不足"])
        )
    ]
    with pytest.raises(AIOutputValidationError):
        require_complete_analysis_anchors(dup)


def test_validate_dimension_weights_tolerance_and_range() -> None:
    from app.schemas.interview_ai import InterviewDimensionSnapshot
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        validate_dimension_weights,
    )

    validate_dimension_weights(
        [
            InterviewDimensionSnapshot.model_validate(
                _snapshot_item("D001", weight="49.99")
            ),
            InterviewDimensionSnapshot.model_validate(
                _snapshot_item("D002", name="专业", weight="50.00")
            ),
        ]
    )
    validate_dimension_weights(
        [
            InterviewDimensionSnapshot.model_validate(
                _snapshot_item("D001", weight="50.00")
            ),
            InterviewDimensionSnapshot.model_validate(
                _snapshot_item("D002", name="专业", weight="50.01")
            ),
        ]
    )

    def dims(*weights: str) -> list:
        return [
            InterviewDimensionSnapshot.model_validate(
                _snapshot_item(f"D{i:03d}", name=f"N{i}", weight=w)
            )
            for i, w in enumerate(weights, start=1)
        ]

    with pytest.raises(AIOutputValidationError):
        validate_dimension_weights(dims("0", "100.00"))
    with pytest.raises(AIOutputValidationError):
        validate_dimension_weights(dims("-1.00", "101.00"))
    with pytest.raises(AIOutputValidationError):
        validate_dimension_weights(dims("101.00"))
    with pytest.raises(AIOutputValidationError):
        validate_dimension_weights(dims("49.98", "50.00"))
    nan_item = InterviewDimensionSnapshot.model_construct(
        dimension_key="D001",
        display_order=1,
        name="沟通协作",
        weight=Decimal("NaN"),
        description="",
        anchors=["a"],
    )
    with pytest.raises(AIOutputValidationError) as nan_info:
        validate_dimension_weights([nan_item])
    assert SECRET not in str(nan_info.value)
    inf_item = InterviewDimensionSnapshot.model_construct(
        dimension_key="D001",
        display_order=1,
        name="沟通协作",
        weight=Decimal("Infinity"),
        description="",
        anchors=["a"],
    )
    with pytest.raises(AIOutputValidationError):
        validate_dimension_weights([inf_item])


def test_compute_interview_overall_score_formula_and_edges() -> None:
    from app.schemas.interview_ai import InterviewDimensionSnapshot
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        compute_interview_overall_score,
    )

    dims = _two_dims()
    assert compute_interview_overall_score(dims, {"D001": 5, "D002": 5}) == Decimal(
        "5.00"
    )
    assert compute_interview_overall_score(dims, {"D001": 5, "D002": 3}) == Decimal(
        "3.80"
    )
    even = [
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D001", weight="50.00")
        ),
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D002", name="专业能力", weight="50.00")
        ),
    ]
    assert compute_interview_overall_score(even, {"D001": 5, "D002": 2}) == Decimal(
        "3.50"
    )
    assert compute_interview_overall_score(dims, {"D001": None, "D002": 5}) is None

    half_dims = [
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D001", weight="99.50")
        ),
        InterviewDimensionSnapshot.model_validate(
            _snapshot_item("D002", name="专业", weight="0.50")
        ),
    ]
    assert compute_interview_overall_score(half_dims, {"D001": 1, "D002": 2}) == Decimal(
        "1.01"
    )

    with pytest.raises(AIOutputValidationError):
        compute_interview_overall_score(dims, {"D001": 5})
    with pytest.raises(AIOutputValidationError):
        compute_interview_overall_score(dims, {"D001": 5, "D002": 5, "D003": 1})
    with pytest.raises(AIOutputValidationError):
        compute_interview_overall_score(dims, {"D001": True, "D002": 5})
    with pytest.raises(AIOutputValidationError):
        compute_interview_overall_score(dims, {"D001": 0, "D002": 5})
    with pytest.raises(AIOutputValidationError):
        compute_interview_overall_score(dims, {"D001": 6, "D002": 5})


def test_validate_question_result_against_snapshot_rules() -> None:
    from app.schemas.interview_ai import InterviewQuestionGenerateResult
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        validate_question_result_against_snapshot,
    )

    dims = _two_dims()
    ok = InterviewQuestionGenerateResult.model_validate(
        {
            "questions": [
                {
                    "dimension_key": "D001",
                    "question": "请描述一次跨团队冲突处理。",
                    "purpose": "考察协作",
                    "evidence_source": "JOB_REQUIREMENT",
                    "resume_evidence": None,
                    "follow_up_prompts": ["后续呢？"],
                    "risk_flags": ["回避"],
                    "display_order": 1,
                },
                {
                    "dimension_key": "D001",
                    "question": "另一个沟通问题。",
                    "purpose": "补充追问",
                    "evidence_source": "GENERAL",
                    "resume_evidence": None,
                    "follow_up_prompts": [],
                    "risk_flags": [],
                    "display_order": 2,
                },
                {
                    "dimension_key": "D002",
                    "question": "简历中的项目如何落地？",
                    "purpose": "核验经历",
                    "evidence_source": "RESUME_EXPERIENCE",
                    "resume_evidence": "主导过招聘系统上线",
                    "follow_up_prompts": [],
                    "risk_flags": [],
                    "display_order": 3,
                },
            ]
        }
    )
    validate_question_result_against_snapshot(ok, dims)

    unknown = InterviewQuestionGenerateResult.model_validate(
        {
            "questions": [
                {
                    **ok.questions[0].model_dump(),
                    "dimension_key": "D999",
                    "question": "未知维度题",
                    "display_order": 1,
                }
            ]
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_question_result_against_snapshot(unknown, dims)

    skipped = InterviewQuestionGenerateResult.model_validate(
        {
            "questions": [
                {**ok.questions[0].model_dump(), "display_order": 1},
                {
                    **ok.questions[1].model_dump(),
                    "display_order": 3,
                    "question": "跳号题",
                },
            ]
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_question_result_against_snapshot(skipped, dims)

    resume_missing = InterviewQuestionGenerateResult.model_validate(
        {
            "questions": [
                {
                    **ok.questions[2].model_dump(),
                    "resume_evidence": None,
                    "display_order": 1,
                    "question": "缺证据",
                }
            ]
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_question_result_against_snapshot(resume_missing, dims)

    unexpected_resume = InterviewQuestionGenerateResult.model_validate(
        {
            "questions": [
                {
                    **ok.questions[0].model_dump(),
                    "resume_evidence": "不该出现",
                    "display_order": 1,
                    "question": "岗位题却带简历证据",
                }
            ]
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_question_result_against_snapshot(unexpected_resume, dims)

    duplicate = InterviewQuestionGenerateResult.model_validate(
        {
            "questions": [
                {**ok.questions[0].model_dump(), "display_order": 1},
                {
                    **ok.questions[0].model_dump(),
                    "display_order": 2,
                    "question": "  请描述一次   跨团队冲突处理。  ",
                },
            ]
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_question_result_against_snapshot(duplicate, dims)


def test_normalize_evidence_text_rules() -> None:
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        normalize_evidence_text,
    )

    assert (
        normalize_evidence_text("  我当时\r\n先对齐\u00a0目标  ")
        == "我当时 先对齐 目标"
    )
    assert normalize_evidence_text("Hello") == "Hello"
    with pytest.raises(AIOutputValidationError):
        normalize_evidence_text("   \r\n  ")
    assert normalize_evidence_text("你好，世界") == "你好，世界"
    assert normalize_evidence_text("你好，世界") != normalize_evidence_text(
        "你好,世界"
    )


def _analyze_result(*, score: int | None = 4, quote: str = "先对齐目标", extra_dim: bool = False):
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult

    dimensions = [
        {
            "dimension_key": "D002",
            "score": 5,
            "evidence": [
                {
                    "segment_id": str(SEGMENT_B),
                    "segment_no": 2,
                    "quote": "方案已经上线",
                }
            ],
            "analysis": "专业能力有结果。",
            "strengths": ["结果导向"],
            "risks": ["覆盖面有限"],
            "insufficient_information": None,
            "suggested_follow_ups": [],
        },
        {
            "dimension_key": "D001",
            "score": score,
            "evidence": [
                {
                    "segment_id": str(SEGMENT_A),
                    "segment_no": 1,
                    "quote": quote,
                }
            ]
            if score is not None or quote
            else [],
            "analysis": "沟通路径清楚。",
            "strengths": ["对齐目标"],
            "risks": ["细节不足"],
            "insufficient_information": None if score is not None else "信息不足说明",
            "suggested_follow_ups": ["追问结果"],
        },
    ]
    if extra_dim:
        dimensions.append(
            {
                "dimension_key": "D003",
                "score": 3,
                "evidence": [
                    {
                        "segment_id": str(SEGMENT_A),
                        "segment_no": 1,
                        "quote": "先对齐目标",
                    }
                ],
                "analysis": "额外维度",
                "strengths": [],
                "risks": [],
                "insufficient_information": None,
                "suggested_follow_ups": [],
            }
        )
    return InterviewRoundAnalyzeResult.model_validate(
        {
            "dimensions": dimensions,
            "overall_summary": "综合稳定。",
            "model_reported_overall_score": "9.99",
        }
    )


def test_validate_analysis_result_computes_backend_score_and_allows_reorder() -> None:
    from app.services.interview_ai_validation import (
        validate_analysis_result_against_snapshot,
    )

    dims = _two_dims()
    segments = [
        _segment(),
        _segment(
            segment_id=SEGMENT_B,
            segment_no=2,
            text="最终方案已经上线并复盘。",
        ),
        _segment(
            segment_id=uuid4(),
            segment_no=3,
            text="未被引用的合法片段。",
        ),
    ]
    official = validate_analysis_result_against_snapshot(
        _analyze_result(),
        dims,
        transcript_version_id=TRANSCRIPT_VERSION_ID,
        segments=segments,
    )
    assert official == Decimal("4.60")


def test_validate_analysis_result_null_score_and_insufficient() -> None:
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        validate_analysis_result_against_snapshot,
    )

    dims = _two_dims()
    segments = [
        _segment(),
        _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
    ]
    payload = _analyze_result(score=None, quote="先对齐目标")
    payload.dimensions[1].insufficient_information = "转写未覆盖冲突处理细节"
    official = validate_analysis_result_against_snapshot(
        payload,
        dims,
        transcript_version_id=TRANSCRIPT_VERSION_ID,
        segments=segments,
    )
    assert official is None

    scored_with_insufficient = InterviewRoundAnalyzeResult.model_validate(
        {
            "dimensions": [
                {
                    "dimension_key": "D001",
                    "score": 4,
                    "evidence": [
                        {
                            "segment_id": str(SEGMENT_A),
                            "segment_no": 1,
                            "quote": "先对齐目标",
                        }
                    ],
                    "analysis": "有分",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": "不该同时存在",
                    "suggested_follow_ups": [],
                },
                {
                    "dimension_key": "D002",
                    "score": 5,
                    "evidence": [
                        {
                            "segment_id": str(SEGMENT_B),
                            "segment_no": 2,
                            "quote": "方案已经上线",
                        }
                    ],
                    "analysis": "有分",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": None,
                    "suggested_follow_ups": [],
                },
            ],
            "overall_summary": "摘要",
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_analysis_result_against_snapshot(
            scored_with_insufficient,
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=segments,
        )


def test_validate_analysis_rejects_bad_segments_and_quote_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        validate_analysis_result_against_snapshot,
    )

    dims = _two_dims()
    good_segments = [
        _segment(),
        _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
    ]

    with pytest.raises(AIOutputValidationError):
        validate_analysis_result_against_snapshot(
            _analyze_result(extra_dim=True),
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=good_segments,
        )

    mismatch = _analyze_result(quote=SECRET)
    with pytest.raises(AIOutputValidationError) as exc_info:
        validate_analysis_result_against_snapshot(
            mismatch,
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=good_segments,
        )
    assert SECRET not in str(exc_info.value)
    assert SECRET not in caplog.text
    assert "input_value" not in str(exc_info.value)
    assert getattr(exc_info.value, "code", None)

    excluded = [
        _segment(included=False),
        _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
    ]
    with pytest.raises(AIOutputValidationError):
        validate_analysis_result_against_snapshot(
            _analyze_result(),
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=excluded,
        )

    other_version = [
        _segment(transcript_version_id=uuid4()),
        _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
    ]
    with pytest.raises(AIOutputValidationError):
        validate_analysis_result_against_snapshot(
            _analyze_result(),
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=other_version,
        )

    wrong_no = [
        _segment(segment_no=9),
        _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
    ]
    with pytest.raises(AIOutputValidationError):
        validate_analysis_result_against_snapshot(
            _analyze_result(),
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=wrong_no,
        )

    empty_text = [
        _segment(text="   "),
        _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
    ]
    with pytest.raises(Exception):
        validate_analysis_result_against_snapshot(
            _analyze_result(),
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=empty_text,
        )

    dup_same_dim = [
        _segment(),
        _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
    ]
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult

    duplicated = InterviewRoundAnalyzeResult.model_validate(
        {
            "dimensions": [
                {
                    "dimension_key": "D001",
                    "score": 4,
                    "evidence": [
                        {
                            "segment_id": str(SEGMENT_A),
                            "segment_no": 1,
                            "quote": "先对齐目标",
                        },
                        {
                            "segment_id": str(SEGMENT_A),
                            "segment_no": 1,
                            "quote": "推动方案",
                        },
                    ],
                    "analysis": "重复引用",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": None,
                    "suggested_follow_ups": [],
                },
                {
                    "dimension_key": "D002",
                    "score": 5,
                    "evidence": [
                        {
                            "segment_id": str(SEGMENT_B),
                            "segment_no": 2,
                            "quote": "方案已经上线",
                        }
                    ],
                    "analysis": "专业",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": None,
                    "suggested_follow_ups": [],
                },
            ],
            "overall_summary": "摘要",
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_analysis_result_against_snapshot(
            duplicated,
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=dup_same_dim,
        )


def test_different_dimensions_may_reuse_same_segment() -> None:
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult
    from app.services.interview_ai_validation import (
        validate_analysis_result_against_snapshot,
    )

    dims = _two_dims()
    segments = [_segment(text="我当时先对齐目标再推动方案。")]
    payload = InterviewRoundAnalyzeResult.model_validate(
        {
            "dimensions": [
                {
                    "dimension_key": "D001",
                    "score": 4,
                    "evidence": [
                        {
                            "segment_id": str(SEGMENT_A),
                            "segment_no": 1,
                            "quote": "先对齐目标",
                        }
                    ],
                    "analysis": "沟通",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": None,
                    "suggested_follow_ups": [],
                },
                {
                    "dimension_key": "D002",
                    "score": 3,
                    "evidence": [
                        {
                            "segment_id": str(SEGMENT_A),
                            "segment_no": 1,
                            "quote": "推动方案",
                        }
                    ],
                    "analysis": "专业",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": None,
                    "suggested_follow_ups": [],
                },
            ],
            "overall_summary": "共享证据。",
        }
    )
    official = validate_analysis_result_against_snapshot(
        payload,
        dims,
        transcript_version_id=TRANSCRIPT_VERSION_ID,
        segments=segments,
    )
    assert official == Decimal("3.40")


def test_scored_dimension_requires_evidence() -> None:
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult
    from app.services.interview_ai_validation import (
        AIOutputValidationError,
        validate_analysis_result_against_snapshot,
    )

    dims = _two_dims()
    payload = InterviewRoundAnalyzeResult.model_validate(
        {
            "dimensions": [
                {
                    "dimension_key": "D001",
                    "score": 4,
                    "evidence": [],
                    "analysis": "无证据",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": None,
                    "suggested_follow_ups": [],
                },
                {
                    "dimension_key": "D002",
                    "score": 5,
                    "evidence": [
                        {
                            "segment_id": str(SEGMENT_B),
                            "segment_no": 2,
                            "quote": "方案已经上线",
                        }
                    ],
                    "analysis": "专业",
                    "strengths": [],
                    "risks": [],
                    "insufficient_information": None,
                    "suggested_follow_ups": [],
                },
            ],
            "overall_summary": "摘要",
        }
    )
    with pytest.raises(AIOutputValidationError):
        validate_analysis_result_against_snapshot(
            payload,
            dims,
            transcript_version_id=TRANSCRIPT_VERSION_ID,
            segments=[
                _segment(),
                _segment(segment_id=SEGMENT_B, segment_no=2, text="方案已经上线"),
            ],
        )
