"""RED/GREEN tests for deterministic transcript decoding and parsing."""

from __future__ import annotations

import hashlib
import importlib
import inspect

import pytest

from app.services.transcript_parser import (
    DecodedTranscript,
    ParsedSegment,
    ParsedTranscript,
    TranscriptParseError,
    decode_transcript,
    parse_transcript,
)

MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
MAX_SEGMENTS = 10_000


# ---------------------------------------------------------------------------
# UTF-8 BOM / Scheme A decoding
# ---------------------------------------------------------------------------


def test_utf8_bom_strips_leading_feff_and_parses_interviewer() -> None:
    """BOM file must not leave U+FEFF; first segment must be INTERVIEWER."""
    body = "面试官：请介绍自己"
    payload = b"\xef\xbb\xbf" + body.encode("utf-8")
    decoded = decode_transcript(payload, "notes.txt")
    assert decoded.text[:1] != "\ufeff"
    assert not decoded.text.startswith("\ufeff")
    assert decoded.text == body
    assert decoded.encoding == "utf-8-sig"
    parsed = parse_transcript(decoded.text)
    assert parsed.segments[0].speaker_role == "INTERVIEWER"
    assert parsed.segments[0].text == "请介绍自己"
    assert parsed.segments[0].matched_rule == "interviewer_label"


def test_utf8_without_bom_decodes_as_utf8() -> None:
    body = "面试官：请介绍自己"
    decoded = decode_transcript(body.encode("utf-8"), "notes.txt")
    assert decoded.encoding == "utf-8"
    assert decoded.text == body
    assert decoded.text[:1] != "\ufeff"
    assert parse_transcript(decoded.text).segments[0].speaker_role == "INTERVIEWER"


def test_gb18030_decodes_without_bom() -> None:
    body = "候选人：我有五年经验"
    payload = body.encode("gb18030")
    assert not payload.startswith(b"\xef\xbb\xbf")
    decoded = decode_transcript(payload, "notes.txt")
    assert decoded.encoding == "gb18030"
    assert decoded.text == body
    assert parse_transcript(decoded.text).segments[0].speaker_role == "CANDIDATE"


def test_illegal_bytes_fail_safely_without_replace() -> None:
    # Invalid as UTF-8; also invalid as complete GB18030 sequence.
    payload = b"\xff\xfe\xfa"
    with pytest.raises(TranscriptParseError, match="unable to decode"):
        decode_transcript(payload, "a.txt")
    import ast

    import app.services.transcript_parser as mod

    tree = ast.parse(inspect.getsource(mod._decode_bytes))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "errors":
                    raise AssertionError("decode must not pass errors=")


def test_bom_only_stripped_at_file_start_interior_feff_preserved() -> None:
    # Leading BOM stripped; an intentional U+FEFF inside the body remains.
    interior = "面试官：前\ufeff后"
    payload = b"\xef\xbb\xbf" + interior.encode("utf-8")
    decoded = decode_transcript(payload, "notes.txt")
    assert decoded.text == interior
    assert decoded.text.startswith("面试官")
    assert "\ufeff" in decoded.text
    assert decoded.text.count("\ufeff") == 1


def test_decode_scheme_a_order_documented() -> None:
    """Final order: BOM→utf-8-sig; else utf-8; else gb18030. No utf-8-before-bom."""
    import app.services.transcript_parser as mod

    source = inspect.getsource(mod._decode_bytes)
    bom_idx = source.index("ef\\xbb\\xbf")
    utf8_idx = source.index('decode("utf-8")')
    gb_idx = source.index('decode("gb18030")')
    assert bom_idx < utf8_idx < gb_idx or bom_idx < gb_idx
    # Must not fall through to a second utf-8-sig attempt after plain utf-8.
    assert source.count('decode("utf-8-sig")') == 1


# ---------------------------------------------------------------------------
# decode_transcript
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("encoding", "encode_as"),
    [
        ("utf-8", "utf-8"),
        ("utf-8-sig", "utf-8-sig"),
        ("gb18030", "gb18030"),
    ],
)
def test_decodes_supported_encodings(encoding: str, encode_as: str) -> None:
    payload = "面试官：请介绍自己".encode(encode_as)
    result = decode_transcript(payload, "notes.txt")
    assert isinstance(result, DecodedTranscript)
    assert "面试官" in result.text
    assert result.encoding == encoding
    assert result.source_method == "TXT"
    assert result.mime == "text/plain"
    assert result.filename == "notes.txt"
    assert result.size == len(payload)
    assert result.char_count == len(result.text)
    assert result.sha256 == hashlib.sha256(result.text.encode("utf-8")).hexdigest()


def test_paste_source_method_without_filename() -> None:
    payload = "候选人：你好".encode("utf-8")
    result = decode_transcript(payload)
    assert result.source_method == "PASTE"
    assert result.mime == "text/plain"
    assert result.filename is None


def test_md_extension_sets_source_and_mime() -> None:
    payload = "面试官：开始".encode("utf-8")
    result = decode_transcript(payload, "round.md")
    assert result.source_method == "MD"
    assert result.mime == "text/markdown"
    assert result.filename == "round.md"


def test_normalizes_crlf_and_cr_to_lf() -> None:
    payload = "面试官：A\r\n候选人：B\r其他".encode("utf-8")
    result = decode_transcript(payload, "a.txt")
    assert result.text == "面试官：A\n候选人：B\n其他"
    assert "\r" not in result.text


def test_sha256_is_of_normalized_utf8_bytes() -> None:
    payload = "面试官：A\r\n候选人：B".encode("utf-8")
    result = decode_transcript(payload, "a.txt")
    expected = hashlib.sha256("面试官：A\n候选人：B".encode("utf-8")).hexdigest()
    assert result.sha256 == expected


def test_filename_uses_sanitized_basename_only() -> None:
    payload = "面试官：x".encode("utf-8")
    result = decode_transcript(payload, "notes.TXT")
    assert result.filename == "notes.TXT"
    assert result.source_method == "TXT"


def test_rejects_path_traversal_filename() -> None:
    payload = "面试官：x".encode("utf-8")
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, "../secrets.txt")
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, "subdir/notes.txt")
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, r"C:\tmp\notes.txt")


@pytest.mark.parametrize("name", ["notes.docx", "a.pdf", "x.mp3", "noext"])
def test_rejects_illegal_extension(name: str) -> None:
    payload = "面试官：x".encode("utf-8")
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, name)


def test_rejects_undecodable_payload() -> None:
    # Invalid for utf-8 / utf-8-sig / gb18030, but not flagged as binary.
    payload = bytes([0x80, 0x81, 0x82])
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, "a.txt")


def test_rejects_empty_content() -> None:
    with pytest.raises(TranscriptParseError):
        decode_transcript(b"", "a.txt")
    with pytest.raises(TranscriptParseError):
        decode_transcript(b"   \n\t  ", "a.txt")


def test_rejects_null_bytes_as_binary() -> None:
    payload = b"hello\x00world"
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, "a.txt")


def test_rejects_high_binary_ratio() -> None:
    # Control bytes only (no NUL) so the ratio check fires, not the NUL check.
    payload = bytes([1, 2, 3, 4, 5, 6, 7, 8]) * 2000
    assert len(payload) < MAX_FILE_SIZE
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, "a.txt")


def test_rejects_over_2mib_file() -> None:
    payload = b"a" * (MAX_FILE_SIZE + 1)
    with pytest.raises(TranscriptParseError, match="2 MiB"):
        decode_transcript(payload, "a.txt")


def test_accepts_exact_2mib_file_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact 2 MiB is allowed by the size gate (char limit patched aside)."""
    monkeypatch.setattr(
        "app.services.transcript_parser.MAX_TEXT_CHARS", MAX_FILE_SIZE + 10
    )
    payload = b"a" * MAX_FILE_SIZE
    result = decode_transcript(payload, "a.txt")
    assert result.size == MAX_FILE_SIZE


def test_rejects_over_500000_chars() -> None:
    text = "字" * (MAX_TEXT_CHARS + 1)
    payload = text.encode("utf-8")
    assert len(payload) < MAX_FILE_SIZE
    with pytest.raises(TranscriptParseError, match="500000"):
        decode_transcript(payload, "a.txt")


def test_accepts_exact_500000_chars() -> None:
    text = "字" * MAX_TEXT_CHARS
    payload = text.encode("utf-8")
    assert len(payload) <= MAX_FILE_SIZE
    result = decode_transcript(payload, "a.txt")
    assert result.char_count == MAX_TEXT_CHARS


def test_extension_case_normalized_txt_and_md() -> None:
    body = "面试官：x".encode("utf-8")
    for name, method in (
        ("notes.txt", "TXT"),
        ("notes.TXT", "TXT"),
        ("notes.md", "MD"),
        ("notes.MD", "MD"),
    ):
        result = decode_transcript(body, name)
        assert result.source_method == method


@pytest.mark.parametrize(
    "name",
    [
        "notes.docx",
        "a.pdf",
        "x.mp3",
        "noext",
        "archive.tar.md.exe",
        "notes.txt.exe",
        "notes.md.pdf",
        "file.txt.bak",
    ],
)
def test_rejects_double_or_disallowed_extensions(name: str) -> None:
    payload = "面试官：x".encode("utf-8")
    with pytest.raises(TranscriptParseError):
        decode_transcript(payload, name)


def test_rejects_binary_disguised_as_txt() -> None:
    payload = b"MZ\x00\x00" + bytes(range(1, 32)) * 100
    with pytest.raises(TranscriptParseError, match="binary"):
        decode_transcript(payload, "notes.txt")
    with pytest.raises(TranscriptParseError, match="binary"):
        decode_transcript(payload, "notes.md")


# ---------------------------------------------------------------------------
# parse_transcript — speaker / time formats
# ---------------------------------------------------------------------------


def test_parses_interviewer_label() -> None:
    result = parse_transcript("面试官：请介绍自己")
    assert result.segment_count == 1
    seg = result.segments[0]
    assert seg.segment_no == 1
    assert seg.speaker_role == "INTERVIEWER"
    assert seg.speaker_name == "面试官"
    assert seg.speaker_key == "interviewer"
    assert seg.text == "请介绍自己"
    assert seg.start_time_ms is None
    assert seg.end_time_ms is None
    assert seg.matched_rule == "interviewer_label"
    assert "interviewer_label" in result.matched_rules


def test_parses_candidate_label() -> None:
    result = parse_transcript("候选人：我有五年经验")
    seg = result.segments[0]
    assert seg.speaker_role == "CANDIDATE"
    assert seg.speaker_name == "候选人"
    assert seg.speaker_key == "candidate"
    assert seg.text == "我有五年经验"
    assert seg.matched_rule == "candidate_label"


def test_parses_speaker_n_as_unknown_role() -> None:
    result = parse_transcript("Speaker 1: Hello\nSpeaker 2: World")
    assert result.segment_count == 2
    s1, s2 = result.segments
    assert s1.speaker_role == "UNKNOWN"
    assert s1.speaker_name == "Speaker 1"
    assert s1.speaker_key == "speaker_1"
    assert s1.text == "Hello"
    assert s1.matched_rule == "speaker_n"
    assert s2.speaker_name == "Speaker 2"
    assert s2.speaker_key == "speaker_2"
    assert s2.matched_rule == "speaker_n"


def test_parses_lone_timestamp_interviewer_persists_null_times() -> None:
    result = parse_transcript("[00:01:20] 面试官：继续")
    seg = result.segments[0]
    assert seg.speaker_role == "INTERVIEWER"
    assert seg.speaker_name == "面试官"
    assert seg.text == "继续"
    assert seg.start_time_ms is None
    assert seg.end_time_ms is None
    assert seg.matched_rule == "timestamp_interviewer"


def test_parses_timestamp_range_candidate_to_ms() -> None:
    result = parse_transcript("00:01:20 - 00:01:35 候选人：回答")
    seg = result.segments[0]
    assert seg.speaker_role == "CANDIDATE"
    assert seg.speaker_name == "候选人"
    assert seg.text == "回答"
    assert seg.start_time_ms == (1 * 60 + 20) * 1000
    assert seg.end_time_ms == (1 * 60 + 35) * 1000
    assert seg.matched_rule == "timestamp_range_candidate"


def test_parses_mm_ss_timestamp_range() -> None:
    result = parse_transcript("01:20 - 01:35 面试官：提问")
    seg = result.segments[0]
    assert seg.start_time_ms == (1 * 60 + 20) * 1000
    assert seg.end_time_ms == (1 * 60 + 35) * 1000
    assert seg.matched_rule == "timestamp_range_interviewer"


def test_parses_all_five_formats_together() -> None:
    text = "\n".join(
        [
            "面试官：请介绍自己",
            "候选人：我有五年经验",
            "Speaker 1: Hello",
            "[00:01:20] 面试官：继续",
            "00:01:20 - 00:01:35 候选人：回答",
        ]
    )
    result = parse_transcript(text)
    assert result.segment_count == 5
    rules = [s.matched_rule for s in result.segments]
    assert rules == [
        "interviewer_label",
        "candidate_label",
        "speaker_n",
        "timestamp_interviewer",
        "timestamp_range_candidate",
    ]


# ---------------------------------------------------------------------------
# continuation / UNKNOWN / blank lines
# ---------------------------------------------------------------------------


def test_unknown_fallback_for_unlabeled_paragraph() -> None:
    result = parse_transcript("这段话没有说话人标签")
    assert result.segment_count == 1
    seg = result.segments[0]
    assert seg.speaker_role == "UNKNOWN"
    assert seg.speaker_name == "UNKNOWN"
    assert seg.speaker_key == "unknown"
    assert seg.text == "这段话没有说话人标签"
    assert seg.matched_rule == "unknown_fallback"
    assert "unknown_fallback" in result.matched_rules


def test_multiline_continuation_appends_with_newline() -> None:
    text = "面试官：第一行\n这是续行\n还有一行"
    result = parse_transcript(text)
    assert result.segment_count == 1
    assert result.segments[0].text == "第一行\n这是续行\n还有一行"
    assert result.segments[0].matched_rule == "interviewer_label"


def test_blank_lines_are_paragraph_boundaries_not_segments() -> None:
    text = "面试官：A\n\n\n候选人：B"
    result = parse_transcript(text)
    assert result.segment_count == 2
    assert result.segments[0].text == "A"
    assert result.segments[1].text == "B"


def test_blank_line_starts_new_unknown_paragraph() -> None:
    text = "无标签一段\n\n无标签二段"
    result = parse_transcript(text)
    assert result.segment_count == 2
    assert result.segments[0].matched_rule == "unknown_fallback"
    assert result.segments[0].text == "无标签一段"
    assert result.segments[1].text == "无标签二段"


def test_rejects_empty_parse_input() -> None:
    with pytest.raises(TranscriptParseError):
        parse_transcript("")
    with pytest.raises(TranscriptParseError):
        parse_transcript("   \n\n  ")


def test_rejects_over_10000_segments() -> None:
    lines = [f"面试官：段{i}" for i in range(MAX_SEGMENTS + 1)]
    with pytest.raises(TranscriptParseError):
        parse_transcript("\n".join(lines))


def test_accepts_exact_10000_segments() -> None:
    lines = [f"候选人：段{i}" for i in range(MAX_SEGMENTS)]
    result = parse_transcript("\n".join(lines))
    assert result.segment_count == MAX_SEGMENTS
    assert result.segments[-1].segment_no == MAX_SEGMENTS


def test_matched_rules_unique_ordered_by_first_appearance() -> None:
    text = "面试官：a\n面试官：b\n候选人：c"
    result = parse_transcript(text)
    assert result.matched_rules == ("interviewer_label", "candidate_label")


def test_parsed_records_are_frozen() -> None:
    result = parse_transcript("面试官：x")
    with pytest.raises(Exception):
        result.segments[0].text = "mutated"  # type: ignore[misc]
    decoded = decode_transcript("面试官：x".encode("utf-8"), "a.txt")
    with pytest.raises(Exception):
        decoded.text = "mutated"  # type: ignore[misc]


def test_no_ai_imports_in_transcript_parser_module() -> None:
    module = importlib.import_module("app.services.transcript_parser")
    source = inspect.getsource(module).lower()
    forbidden = (
        "openai",
        "anthropic",
        "dify",
        "langchain",
        "ai_providers",
        "ai_tasks",
    )
    for name in forbidden:
        assert name not in source
    # Pure: no DB / network / filesystem side effects in public API bodies.
    for fn in (decode_transcript, parse_transcript):
        body = inspect.getsource(fn).lower()
        assert "sqlalchemy" not in body
        assert "requests" not in body
        assert "httpx" not in body
        assert "open(" not in body


def test_types_exported() -> None:
    assert issubclass(TranscriptParseError, Exception)
    assert ParsedTranscript.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert ParsedSegment.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert DecodedTranscript.__dataclass_params__.frozen  # type: ignore[attr-defined]
