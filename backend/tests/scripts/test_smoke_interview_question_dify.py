from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = BACKEND_ROOT / "scripts" / "smoke_interview_question_dify.py"
FICTIONAL_PREFIX = "FICTIONAL-LIVE-20260818"
FORBIDDEN_PRINT_TOKENS = (
    "jd_text",
    "resume_text",
    "api_key",
    "input_snapshot",
    "Authorization",
    "authorization",
)
ALLOWED_PRINT_FIELDS = ("ok", "http_status", "error_code", "question_count")
FORBIDDEN_CALLS = (
    "create_database_engine",
    "apply_async",
    "enqueue",
)


def _script_source() -> str:
    return SMOKE_SCRIPT.read_text(encoding="utf-8")


def _script_tree() -> ast.AST:
    return ast.parse(_script_source())


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _run_dify_calls(tree: ast.AST) -> list[ast.Call]:
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if _call_name(node) == "run_dify":
            found.append(node)
    return found


def _input_snapshot_dicts(tree: ast.AST) -> list[ast.Dict]:
    snapshots: list[ast.Dict] = []
    for call in _run_dify_calls(tree):
        for keyword in call.keywords:
            if keyword.arg == "input_snapshot" and isinstance(keyword.value, ast.Dict):
                snapshots.append(keyword.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "INPUT_SNAPSHOT"
            for target in node.targets
        ) and isinstance(node.value, ast.Dict):
            snapshots.append(node.value)
    return snapshots


def _dict_string_values(node: ast.Dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values[key.value] = value.value
    return values


def _dict_keys(node: ast.Dict) -> set[str]:
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def test_smoke_script_calls_run_dify_keyword_signature() -> None:
    tree = _script_tree()
    calls = _run_dify_calls(tree)
    assert calls, "smoke script must call run_dify"
    for call in calls:
        assert call.args == [], "run_dify is keyword-only"
        names = {kw.arg for kw in call.keywords}
        assert names == {"task_type", "input_snapshot"}
        task_type = next(kw.value for kw in call.keywords if kw.arg == "task_type")
        assert isinstance(task_type, ast.Name)
        assert task_type.id == "TASK_TYPE_INTERVIEW_QUESTION_GENERATE"


def test_smoke_script_fictional_prefixes() -> None:
    snapshots = _input_snapshot_dicts(_script_tree())
    assert snapshots
    for snapshot in snapshots:
        values = _dict_string_values(snapshot)
        for field in ("job_title", "jd_text", "resume_text"):
            assert values[field].startswith(FICTIONAL_PREFIX)


def test_smoke_script_uses_dimensions_list_not_dimensions_json_input() -> None:
    snapshots = _input_snapshot_dicts(_script_tree())
    assert snapshots
    for snapshot in snapshots:
        keys = _dict_keys(snapshot)
        assert "dimensions" in keys
        assert "dimensions_json" not in keys
        for key, value in zip(snapshot.keys, snapshot.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "dimensions":
                assert isinstance(value, ast.List)


def test_smoke_script_has_no_db_or_celery() -> None:
    source = _script_source()
    lowered = source.lower()
    assert "create_database_engine" not in source
    assert "celery" not in lowered
    assert "apply_async" not in source
    assert "enqueue" not in source
    tree = _script_tree()
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    imported |= {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "httpx" not in imported
    for node in ast.walk(tree):
        assert _call_name(node) not in FORBIDDEN_CALLS


def test_smoke_script_print_allowlist() -> None:
    tree = _script_tree()
    printed = []
    for node in ast.walk(tree):
        if _call_name(node) != "print":
            continue
        rendered = ast.unparse(node)
        printed.append(rendered)
        for token in FORBIDDEN_PRINT_TOKENS:
            assert token not in rendered
    joined = "\n".join(printed)
    for field in ALLOWED_PRINT_FIELDS:
        assert field in joined


def test_smoke_script_does_not_enable_live_switch() -> None:
    tree = _script_tree()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            modules.extend(alias.name for alias in node.names)
            assert "os" not in modules
        if isinstance(node, ast.Call):
            name = _call_name(node)
            assert name not in {"setenv", "putenv", "setdefault"}
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            raise AssertionError("smoke script must not touch os.environ")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "dify_interview_question_live_enabled"
                ):
                    raise AssertionError("smoke script must not set the live switch")
