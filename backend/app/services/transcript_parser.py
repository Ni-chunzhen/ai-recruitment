"""Pure deterministic transcript decoding and speaker/timestamp parsing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
MAX_SEGMENTS = 10_000
_BINARY_RATIO_THRESHOLD = 0.30
_ALLOWED_EXTENSIONS = frozenset({".txt", ".md"})

_TIME_TOKEN = r"(?:(?:\d{1,2}:)?\d{1,2}:\d{2})"
_RE_TIMESTAMP_RANGE = re.compile(
    rf"^\s*(?P<start>{_TIME_TOKEN})\s*[-–—]\s*(?P<end>{_TIME_TOKEN})\s+"
    rf"(?P<body>.+?)\s*$"
)
_RE_LONE_TIMESTAMP = re.compile(
    rf"^\s*\[(?P<ts>{_TIME_TOKEN})\]\s+(?P<body>.+?)\s*$"
)
_RE_INTERVIEWER = re.compile(r"^\s*面试官\s*[：:]\s*(?P<text>.*)\s*$")
_RE_CANDIDATE = re.compile(r"^\s*候选人\s*[：:]\s*(?P<text>.*)\s*$")
_RE_SPEAKER_N = re.compile(
    r"^\s*(?P<name>Speaker\s+(?P<num>\d+))\s*:\s*(?P<text>.*)\s*$",
    re.IGNORECASE,
)


class TranscriptParseError(Exception):
    """Raised when transcript bytes/text cannot be safely decoded or parsed."""


@dataclass(frozen=True)
class DecodedTranscript:
    text: str
    encoding: str  # utf-8 | utf-8-sig | gb18030
    sha256: str
    char_count: int
    filename: str | None
    size: int
    mime: str | None
    source_method: str  # PASTE | TXT | MD


@dataclass(frozen=True)
class ParsedSegment:
    segment_no: int
    speaker_key: str
    speaker_name: str
    speaker_role: str  # CANDIDATE | INTERVIEWER | OTHER | UNKNOWN
    start_time_ms: int | None
    end_time_ms: int | None
    text: str
    matched_rule: str


@dataclass(frozen=True)
class ParsedTranscript:
    segments: tuple[ParsedSegment, ...]
    matched_rules: tuple[str, ...]
    segment_count: int


def decode_transcript(
    data: bytes,
    filename: str | None = None,
    *,
    source_method: str | None = None,
) -> DecodedTranscript:
    if not isinstance(data, (bytes, bytearray)):
        raise TranscriptParseError("payload must be bytes")
    payload = bytes(data)
    size = len(payload)
    if size > MAX_FILE_SIZE:
        raise TranscriptParseError("file exceeds 2 MiB limit")
    if size == 0:
        raise TranscriptParseError("empty content")

    safe_name = _sanitize_filename(filename) if filename is not None else None
    method, mime = _resolve_source(safe_name, source_method)

    _reject_binary(payload)

    text, encoding = _decode_bytes(payload)
    text = _normalize_newlines(text)
    if not text.strip():
        raise TranscriptParseError("empty content")
    if len(text) > MAX_TEXT_CHARS:
        raise TranscriptParseError("text exceeds 500000 character limit")

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return DecodedTranscript(
        text=text,
        encoding=encoding,
        sha256=digest,
        char_count=len(text),
        filename=safe_name,
        size=size,
        mime=mime,
        source_method=method,
    )


def parse_transcript(text: str) -> ParsedTranscript:
    if text is None:
        raise TranscriptParseError("empty content")
    normalized = _normalize_newlines(str(text))
    if not normalized.strip():
        raise TranscriptParseError("empty content")
    if len(normalized) > MAX_TEXT_CHARS:
        raise TranscriptParseError("text exceeds 500000 character limit")

    lines = normalized.split("\n")
    segments: list[ParsedSegment] = []
    rules_order: list[str] = []
    seen_rules: set[str] = set()
    # After a blank line, unlabeled text starts a new UNKNOWN paragraph.
    paragraph_break = True

    for raw_line in lines:
        if raw_line.strip() == "":
            paragraph_break = True
            continue

        matched = _match_labeled_line(raw_line)
        if matched is not None:
            speaker_key, speaker_name, speaker_role, start_ms, end_ms, body, rule = (
                matched
            )
            _append_segment(
                segments,
                rules_order,
                seen_rules,
                speaker_key=speaker_key,
                speaker_name=speaker_name,
                speaker_role=speaker_role,
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                text=body,
                matched_rule=rule,
            )
            paragraph_break = False
            continue

        unlabeled = raw_line.strip() if not segments else raw_line.rstrip("\r")
        # Preserve interior indentation relative to original line content for
        # continuation; strip only for brand-new UNKNOWN paragraphs.
        if not segments or paragraph_break:
            _append_segment(
                segments,
                rules_order,
                seen_rules,
                speaker_key="unknown",
                speaker_name="UNKNOWN",
                speaker_role="UNKNOWN",
                start_time_ms=None,
                end_time_ms=None,
                text=unlabeled.strip(),
                matched_rule="unknown_fallback",
            )
            paragraph_break = False
        else:
            prev = segments[-1]
            segments[-1] = ParsedSegment(
                segment_no=prev.segment_no,
                speaker_key=prev.speaker_key,
                speaker_name=prev.speaker_name,
                speaker_role=prev.speaker_role,
                start_time_ms=prev.start_time_ms,
                end_time_ms=prev.end_time_ms,
                text=f"{prev.text}\n{raw_line.rstrip()}",
                matched_rule=prev.matched_rule,
            )

    if not segments:
        raise TranscriptParseError("empty content")
    if len(segments) > MAX_SEGMENTS:
        raise TranscriptParseError("segment count exceeds 10000 limit")

    return ParsedTranscript(
        segments=tuple(segments),
        matched_rules=tuple(rules_order),
        segment_count=len(segments),
    )


def _append_segment(
    segments: list[ParsedSegment],
    rules_order: list[str],
    seen_rules: set[str],
    *,
    speaker_key: str,
    speaker_name: str,
    speaker_role: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    text: str,
    matched_rule: str,
) -> None:
    if len(segments) >= MAX_SEGMENTS:
        raise TranscriptParseError("segment count exceeds 10000 limit")
    segments.append(
        ParsedSegment(
            segment_no=len(segments) + 1,
            speaker_key=speaker_key,
            speaker_name=speaker_name,
            speaker_role=speaker_role,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            text=text,
            matched_rule=matched_rule,
        )
    )
    if matched_rule not in seen_rules:
        seen_rules.add(matched_rule)
        rules_order.append(matched_rule)


def _match_labeled_line(
    line: str,
) -> tuple[str, str, str, int | None, int | None, str, str] | None:
    """Return speaker fields + body + rule, or None if unlabeled."""
    range_match = _RE_TIMESTAMP_RANGE.match(line)
    if range_match:
        body = range_match.group("body")
        start_ms = _time_to_ms(range_match.group("start"))
        end_ms = _time_to_ms(range_match.group("end"))
        labeled = _match_speaker_body(body)
        if labeled is None:
            return None
        key, name, role, text, base_rule = labeled
        rule = f"timestamp_range_{_role_rule_suffix(role, base_rule)}"
        return key, name, role, start_ms, end_ms, text, rule

    lone_match = _RE_LONE_TIMESTAMP.match(line)
    if lone_match:
        body = lone_match.group("body")
        labeled = _match_speaker_body(body)
        if labeled is None:
            return None
        key, name, role, text, base_rule = labeled
        # Lone timestamps are display-only; persist both fields as null.
        rule = f"timestamp_{_role_rule_suffix(role, base_rule)}"
        return key, name, role, None, None, text, rule

    return _match_speaker_as_segment(line)


def _match_speaker_as_segment(
    line: str,
) -> tuple[str, str, str, int | None, int | None, str, str] | None:
    labeled = _match_speaker_body(line)
    if labeled is None:
        return None
    key, name, role, text, rule = labeled
    return key, name, role, None, None, text, rule


def _match_speaker_body(
    body: str,
) -> tuple[str, str, str, str, str] | None:
    m = _RE_INTERVIEWER.match(body)
    if m:
        return (
            "interviewer",
            "面试官",
            "INTERVIEWER",
            m.group("text").strip(),
            "interviewer_label",
        )
    m = _RE_CANDIDATE.match(body)
    if m:
        return (
            "candidate",
            "候选人",
            "CANDIDATE",
            m.group("text").strip(),
            "candidate_label",
        )
    m = _RE_SPEAKER_N.match(body)
    if m:
        num = m.group("num")
        name = f"Speaker {num}"
        return (
            f"speaker_{num}",
            name,
            "UNKNOWN",
            m.group("text").strip(),
            "speaker_n",
        )
    return None


def _role_rule_suffix(role: str, base_rule: str) -> str:
    if role == "INTERVIEWER":
        return "interviewer"
    if role == "CANDIDATE":
        return "candidate"
    if base_rule == "speaker_n":
        return "speaker_n"
    return "unknown"


def _time_to_ms(token: str) -> int:
    parts = [int(p) for p in token.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    else:
        raise TranscriptParseError(f"invalid timestamp: {token}")
    if minutes > 59 or seconds > 59:
        # Allow MM:SS where MM can exceed 59 when used as total minutes? Spec
        # says HH:MM:SS or MM:SS — keep permissive on minutes for MM:SS.
        if len(parts) == 3 and (minutes > 59 or seconds > 59):
            raise TranscriptParseError(f"invalid timestamp: {token}")
        if len(parts) == 2 and seconds > 59:
            raise TranscriptParseError(f"invalid timestamp: {token}")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _sanitize_filename(filename: str) -> str:
    name = filename.strip()
    if not name:
        raise TranscriptParseError("invalid filename")
    # Reject path traversal / directory components rather than silently stripping.
    if any(sep in name for sep in ("/", "\\")) or name in {".", ".."}:
        raise TranscriptParseError("path traversal filename rejected")
    if ".." in name:
        raise TranscriptParseError("path traversal filename rejected")
    # Drive / absolute Windows-style names without separators already rejected above;
    # also reject names that look like "C:foo.txt".
    if len(name) >= 2 and name[1] == ":":
        raise TranscriptParseError("path traversal filename rejected")

    lower = name.lower()
    if "." not in lower:
        raise TranscriptParseError("unsupported file extension")
    ext = "." + lower.rsplit(".", 1)[-1]
    if ext not in _ALLOWED_EXTENSIONS:
        raise TranscriptParseError(f"unsupported file extension: {ext}")
    return name


def _resolve_source(
    filename: str | None, source_method: str | None
) -> tuple[str, str]:
    if filename is None:
        method = source_method or "PASTE"
        if method != "PASTE":
            # Paste payloads have no filename; keep PASTE semantics.
            method = "PASTE"
        return method, "text/plain"

    ext = "." + filename.lower().rsplit(".", 1)[-1]
    if ext == ".txt":
        return "TXT", "text/plain"
    if ext == ".md":
        return "MD", "text/markdown"
    raise TranscriptParseError(f"unsupported file extension: {ext}")


def _reject_binary(payload: bytes) -> None:
    if b"\x00" in payload:
        raise TranscriptParseError("binary content rejected")
    if not payload:
        return
    # Count bytes outside printable ASCII + common whitespace + high UTF-8.
    # Treat non-text control bytes as binary indicators.
    binaryish = 0
    for b in payload:
        if b in (9, 10, 13):  # tab, LF, CR
            continue
        if 32 <= b <= 126:
            continue
        if b >= 0x80:
            # Likely multibyte text (UTF-8 / GB18030); not counted as binary.
            continue
        binaryish += 1
    if binaryish / len(payload) > _BINARY_RATIO_THRESHOLD:
        raise TranscriptParseError("binary content rejected")


def _decode_bytes(payload: bytes) -> tuple[str, str]:
    """Scheme A decoding order:

    1. If payload starts with UTF-8 BOM (EF BB BF) → decode with utf-8-sig
       (strips only the leading BOM; interior U+FEFF is preserved).
    2. Else decode with strict utf-8.
    3. Else decode with strict gb18030.

    Always uses the default strict codec mode (no silent substitution).
    """
    if payload.startswith(b"\xef\xbb\xbf"):
        try:
            return payload.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            try:
                return payload[3:].decode("gb18030"), "gb18030"
            except UnicodeDecodeError as exc:
                raise TranscriptParseError("unable to decode transcript") from exc

    try:
        return payload.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    try:
        return payload.decode("gb18030"), "gb18030"
    except UnicodeDecodeError as exc:
        raise TranscriptParseError("unable to decode transcript") from exc


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
