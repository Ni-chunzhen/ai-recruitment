# 面试题纲 live Dify 受控接入 — TDD 实施计划

> **For agentic workers:** 按任务顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。

**规格：** `docs/superpowers/specs/2026-08-18-interview-question-live-dify-design.md`
**基线源码：** `run_dify(*, task_type, input_snapshot)`；题纲当前无条件 mock；`_workflow_id_for` 对题纲 `return ""`；`dify_api_key_for` 题纲回退 `self.dify_api_key`。
**方法：** TDD。符号名锁定为规格 §3，禁止临时改名。

## 全局约束

- 不改 Alembic、数据库、前端、JD/简历 Dify 回退规则、`INTERVIEW_ROUND_ANALYZE` 强制 mock。
- 不把真实 API Key / Workflow ID 写入测试、计划、`.env.example`、YAML、提交。
- 测试夹具只用假值：`test-interview-question-key`、`test-interview-question-workflow-id`。
- 自动化测试必须 monkeypatch `_post_workflow` 或 `httpx.AsyncClient.post`，**零真实 Dify HTTP**。
- 不启动全量 Celery worker；不读生产/未打前缀的真实简历。
- 签名锁定：`run_dify(*, task_type: str, input_snapshot: dict[str, Any]) -> ProviderOutcome`。provider 入参键为 `job_title` / `jd_text` / `resume_text` / `dimensions`（list），**没有** `dimensions_json` 入参键。
- 本计划各任务 **默认不提交**。若用户另行授权 commit，仅包含该任务「允许提交」文件清单。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| `TASK_TYPE_INTERVIEW_QUESTION_GENERATE` | `"INTERVIEW_QUESTION_GENERATE"` |
| `dify_interview_question_generate_api_key_secret` | Settings Python；env `DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY` |
| `dify_interview_question_generate_workflow_id` | Settings Python；env `DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID` |
| `dify_interview_question_live_enabled` | Settings Python，默认 `False`；env `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED` |
| `INTERVIEW_QUESTION_LIVE_UAT_PREFIX` | `"UAT-CC-20260818"` |
| `INTERVIEW_QUESTION_LIVE_FICTIONAL_PREFIX` | `"FICTIONAL-LIVE-20260818"` |
| `interview_question_live_http_allowed` | `(settings, inputs) -> LiveGateDecision` |
| `LiveGateDecision` | `allow_http` / `fallback_mock` / `error_code` / `reason` |
| 错误码 | `interview_question_live_not_configured`、`interview_question_live_unauthorized` |
| 既有凭据函数 | `dify_api_key_for`、`_workflow_id_for`、`_post_workflow`、`build_dify_inputs` |

既有四个 `DIFY_*_WORKFLOW_ID` **全大写 Python 属性不改名**。

## 公共夹具（各测试文件内联，禁止真实数据）

```python
FICTIONAL_INPUT = {
    "job_title": "FICTIONAL-LIVE-20260818 示例岗位-虚构仓储接口工程师",
    "jd_text": "FICTIONAL-LIVE-20260818 本岗位为完全虚构的演示说明。",
    "resume_text": "FICTIONAL-LIVE-20260818 候选人档案为完全虚构样本。",
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
```

每次 `get_settings.cache_clear()` 成对清理。测试结束不得留下真实 env。

---

## Task 1 — 配置与凭据隔离

**Consumes：** 规格 §3、§5。
**Produces：** `backend/app/core/config.py`、`backend/app/services/ai_providers/dify.py`（仅 `_workflow_id_for` + `dify_api_key_for` 题纲分支）、`backend/.env.example`、测试。

**允许改的文件：**

- `backend/app/core/config.py`
- `backend/app/services/ai_providers/dify.py`（凭据选择；**还不改** `run_dify` 无条件 mock）
- `backend/.env.example`
- `backend/tests/core/test_config.py`（追加）
- `backend/tests/services/test_interview_question_live_dify.py`（新建，本任务只放凭据测试）

**禁止：** 改 `run_dify` 门禁、worker、`.env` 真实值、填入 Key/ID。

### RED

在 `test_config.py` / `test_interview_question_live_dify.py` 新增：

| 测试函数 | 断言 |
|---|---|
| `test_interview_question_settings_defaults` | `dify_interview_question_live_enabled is False`；Key `get_secret_value().strip()==""`；`dify_interview_question_generate_workflow_id.strip()==""` |
| `test_interview_question_settings_read_env_aliases` | `monkeypatch.setenv` 三个全大写变量（假值）后 `cache_clear`，Python 小写属性读到对应值 |
| `test_dify_api_key_for_interview_does_not_fallback` | 专用 Key 空、`dify_api_key_secret` 有值 → `dify_api_key_for(TASK_TYPE_INTERVIEW_QUESTION_GENERATE)==""`；简历类型仍可回退（不改既有断言语义） |
| `test_workflow_id_for_interview_returns_dedicated_attr` | 设置 `dify_interview_question_generate_workflow_id="test-interview-question-workflow-id"` → `_workflow_id_for(TASK_TYPE_INTERVIEW_QUESTION_GENERATE)` 等于该值；`JD_PARSE` 仍读 `DIFY_JD_PARSE_WORKFLOW_ID` |
| `test_env_example_interview_question_vars_are_empty` | 读 `.env.example` 文本：必须出现这三行（值必须为空，开关行为 `false`）：`DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=false`、`DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY=`、`DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID=`；邻近注释含「禁止复用 DIFY_API_KEY」。**只断言这三项赋值右侧为空**，不要扫描整个文件是否含 `sk-`（避免既有无关示例导致误伤） |

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/core/test_config.py tests/services/test_interview_question_live_dify.py -q --tb=short
```

期望：RED（缺属性 / `_workflow_id_for` 仍 `""` / 题纲 Key 仍回退）。

### GREEN

1. `Settings` 新增三个小写属性 + `validation_alias`（Key 用 `SecretStr` + `Field`，对齐 `dify_resume_score_api_key_secret`）。
2. `dify_api_key_for`：`INTERVIEW_QUESTION_GENERATE` 只返回 `self.dify_interview_question_generate_api_key_secret.get_secret_value().strip()`，**不得** `or self.dify_api_key`。分析任务仍走 `else`（本任务不接通分析 live）。
3. `_workflow_id_for`：题纲 `return settings.dify_interview_question_generate_workflow_id`（可 `.strip()`）。既有四个全大写 ID 分支不动。
4. `.env.example` 按规格 §5.3 追加空值三行 + 安全注释。
5. **不**改 `run_dify` 题纲 mock。既有 `test_dify_unconfigured_interview_falls_back_to_mock` 必须仍 GREEN。

同上 pytest 全绿。另跑：

```text
.venv\Scripts\python.exe -m pytest tests/workers/test_interview_ai_worker.py::test_dify_unconfigured_interview_falls_back_to_mock -q
```

**提交边界（仅当用户明确要求）：** 上列允许文件。禁止 `.env`。

---

## Task 2 — live 门禁与请求协议

**Consumes：** Task 1；规格 §4、§6。
**Produces：** `interview_question_live_http_allowed`、`LiveGateDecision`、常量前缀、`run_dify` 题纲受控分支。

**允许改的文件：**

- `backend/app/services/ai_providers/dify.py`
- `backend/tests/services/test_interview_question_live_dify.py`

**禁止：** 新 HTTP 客户端；改 `_post_workflow` URL/path；改分析任务 mock；改 JD/简历配置门禁；启动 worker；真实网络。

### RED

在 `test_interview_question_live_dify.py` 追加（全部 `monkeypatch.setattr(dify, "_post_workflow", ...)` 或拦截 `httpx.AsyncClient.post`，计数必须为 0 除非「允许 HTTP」用例）：

| 测试函数 | 断言 |
|---|---|
| `test_live_default_switch_off_mocks_and_posts_zero` | development + 开关 False + 成对假凭据 + `FICTIONAL_INPUT` → `ok` mock 题；`_post_workflow` 0 次 |
| `test_live_production_ignores_switch_posts_zero` | `ENVIRONMENT="production"` + 开关 True + 成对假凭据 + 授权输入 → 0 HTTP；走 mock |
| `test_live_switch_on_missing_pair_is_not_configured` | 开关 True，缺 Key 或缺 ID 或只配其一 → `error_code=="interview_question_live_not_configured"`，`error_category=="non_retryable"`，`result is None`，0 HTTP，**不是** mock 成功 |
| `test_live_unauthorized_prefix_posts_zero` | 开关 True、成对假凭据、三文本无前缀 → `interview_question_live_unauthorized`，0 HTTP |
| `test_live_mixed_prefixes_unauthorized` | job 用虚构前缀、简历用 `UAT-CC-20260818` → unauthorized，0 HTTP |
| `test_build_dify_inputs_question_keys` | 入参用 `dimensions` list；返回键恰好 `{job_title, jd_text, resume_text, dimensions_json}`；无 `candidate_id`/`segments_json`/`password` |
| `test_live_allowed_reuses_post_workflow_with_workflow_id` | 开关 True、development、成对假凭据、`DIFY_API_BASE_URL` 非空、`FICTIONAL_INPUT`；拦截 `httpx.AsyncClient.post`（或捕获 `_post_workflow` 内 `json` body）→ 调用 1 次；URL 以 `/v1/workflows/run` 结尾；`json["workflow_id"]=="test-interview-question-workflow-id"`；`json["inputs"]` 仅四键；`json["response_mode"]=="blocking"` |
| `test_analyze_stays_mocked_when_question_live_enabled` | 开关 True + 成对假凭据 + 分析 `input_snapshot` → mock 成功，`_post_workflow` 0 次 |

```text
.venv\Scripts\python.exe -m pytest tests/services/test_interview_question_live_dify.py tests/workers/test_interview_ai_worker.py::test_dify_unconfigured_interview_falls_back_to_mock -q --tb=short
```

期望：门禁相关 RED（`run_dify` 仍无条件 mock）。

### GREEN

1. 在 `dify.py` 增加 `INTERVIEW_QUESTION_LIVE_UAT_PREFIX`、`INTERVIEW_QUESTION_LIVE_FICTIONAL_PREFIX`、`LiveGateDecision`、`interview_question_live_http_allowed(settings, inputs)`。`inputs` 为 `build_dify_inputs` **之后**的四键 dict。
2. `run_dify`：`INTERVIEW_ROUND_ANALYZE` 仍无条件 `run_mock`。`INTERVIEW_QUESTION_GENERATE` 按规格 §6.2：`fallback_mock` → `run_mock`；`allow_http` → **只**调用现有 `_post_workflow(task_type=..., input_snapshot=...)` 再规范化/校验；否则返回 `ProviderOutcome(ok=False, error_code=decision.error_code, error_category=non_retryable)`，不发 HTTP。
3. 缺 `settings.DIFY_API_BASE_URL.strip()` 视为未配置（`interview_question_live_not_configured`）。
4. 禁止第三套 client；禁止把 `QUESTION_WORKFLOW_KEY` 写入 `body.workflow_id`。

「允许 HTTP」用例必须 mock `httpx`/`_post_workflow` 返回合法空壳，**不得**打到真实 Dify。

**提交边界：** 仅 `dify.py` + 该测试文件。不 commit `.env`。

---

## Task 3 — 输出与审计脱敏

**Consumes：** Task 2；规格 §7、§8。
**Produces：** `normalize_dify_outputs` 题纲分支；live 返回前脱敏；`SENSITIVE_AUDIT_KEYS` 增加 `jd_text`；非法输出 `output_invalid` 且不 persist 题纲；**仅在 §源码证据成立时**最小修改 worker 写入。

**允许改的文件：**

- `backend/app/services/ai_providers/dify.py`
- `backend/app/models/__init__.py`（仅 `SENSITIVE_AUDIT_KEYS` 加 `jd_text`）
- `backend/tests/services/test_interview_question_live_dify.py`
- `backend/tests/models/test_interview_ai_models.py`（`SENSITIVE_AUDIT_FIELD_NAMES` 增加 `"jd_text"` + 一条 `sanitize_audit_changes({"jd_text": "..."})` 拒绝测试）
- `backend/tests/workers/test_interview_ai_worker.py`（新增 live 写入路径测试；**不改**既有 mock 用例对加密列含正文的断言）
- `backend/app/workers/ai_tasks.py`（**条件允许**，见 GREEN：仅当 `_write_stage8_raw` / `sensitive_*_encrypted` 仍会写入未脱敏的题纲 `provider_input`、Dify inputs 或输出正文时，才做 `INTERVIEW_QUESTION_GENERATE` live 最小分支）

**禁止：** 改 JD/简历 `normalize_dify_outputs` 分支；改分析 persist；把正文写入公开 JSONB；改变 mock / 分析 / JD / 简历解析/评分的加密审计行为。

### 源码证据（决定 worker 是否必须改）

`_write_stage8_raw` **现在会直接加密写入**未脱敏对象（与 `ProviderOutcome.raw_request` 是否已剥正文无关）：

```174:203:backend/app/workers/ai_tasks.py
def _write_stage8_raw(..., provider_input, outcome, extra=None):
    if provider_input is not None:
        attempt.sensitive_request_encrypted = _encrypt_json_blob(
            {"provider_input": provider_input, "raw_request": outcome.raw_request ...}
        )
    if outcome is not None:
        attempt.sensitive_response_encrypted = _encrypt_json_blob(
            {"result": outcome.result, "raw_response": outcome.raw_response, ...}
        )
    public = _stage8_public_payload(...)  # 仅 provider/http_status/hash 等
    task.raw_request = task.raw_response = attempt.raw_response = task.result_payload = public
```

调用点：`_handle_process` 在 stage8 成功/失败/校验失败时传入 **`provider_input`**，该值来自 `_prepare_stage8_provider_input` → `_question_memory_input`，含 **`jd_text` / `resume_text` 明文**。`outcome.result` 含 **题干**。既有 mock 测试 `test_output_invalid_from_persist_writes_encrypted_and_skips_version` **要求**解密后仍能看到 `SECRET_RESUME` / `SECRET_QUESTION`——该行为对 mock 必须保持。

公开 JSONB（`_stage8_public_payload`）当前已不含正文键。live 缺口在 **加密列仍打包原始 `provider_input` 与 `result`**。

### RED

| 测试函数 | 断言 |
|---|---|
| `test_normalize_question_result_object` | `normalize_dify_outputs(TASK_TYPE_INTERVIEW_QUESTION_GENERATE, {"questions":[...]})` 返回仅含 `questions` 的对象 |
| `test_normalize_question_error_object_raises` | `{"error": True, "error_code": "output_validation_failed"}` 且无合法 questions → `ValueError`，不得补造 questions |
| `test_run_dify_invalid_enum_is_output_validation_failed` | 允许 HTTP 条件下 mock `_post_workflow` 返回 200 + `evidence_source="OTHER"` → `ok is False`，`error_code=="output_validation_failed"`，`result is None` |
| `test_run_dify_skipped_display_order_is_output_validation_failed` | `display_order` 为 1,3 → 同上 |
| `test_live_outcome_raw_request_has_no_body_or_key_suffix` | 允许 HTTP 且 mock 成功时，`outcome.raw_request` **不含** `jd_text`/`resume_text` 正文、不含 `api_key_suffix`、不含 Key；含 `workflow_id`、`input_field_names` 或等价 hash 字段 |
| `test_question_live_worker_audit_carriers_have_no_plaintext` | **完整 worker 写入路径**（`_handle_process`，伪造 Dify 成功，授权虚构前缀 live）：断言下列载体均不含 `jd_text`/`resume_text` 正文、题干明文、完整 `inputs` 字典、Key、`api_key_suffix`：`task.raw_request`、`task.raw_response`、`task.result_payload`、`attempt.raw_response`、解密后的 `attempt.sensitive_request_encrypted`、`attempt.sensitive_response_encrypted`。只允许字段名、哈希、`workflow_id`、`provider_run_id`、HTTP 状态、`question_count` 等元数据。既有 mock 用例（加密列含 `SECRET_RESUME`/`SECRET_QUESTION`）不得被改断言 |
| `test_sensitive_audit_keys_include_jd_text` | `"jd_text" in SENSITIVE_AUDIT_KEYS`（可并入既有 `test_sensitive_audit_keys_and_markers`） |
| `test_sanitize_audit_changes_rejects_jd_text` | `sanitize_audit_changes({"jd_text": "JD正文"})` 抛 `ValueError` |
| `test_question_output_invalid_does_not_touch_resume_tasks` | 夹具中预置 `RESUME_PARSE` 任务状态不变（可用现有 worker 测试风格；禁止真 Dify） |

`test_question_live_worker_audit_carriers_have_no_plaintext` 必须走 `_handle_process` + `_write_stage8_raw` 真实写入，禁止只断言 `ProviderOutcome.raw_request`。拦截 `_post_workflow` / `run_dify` 的 HTTP，不得访问真实 Dify。

保留既有 `test_output_invalid_from_persist_writes_encrypted_and_skips_version`：非法 persist 不写题纲成功版本；其「mock 加密列仍含正文」语义保持。

```text
.venv\Scripts\python.exe -m pytest tests/services/test_interview_question_live_dify.py tests/models/test_interview_ai_models.py tests/workers/test_interview_ai_worker.py -q --tb=short
```

### GREEN

1. `normalize_dify_outputs`：**显式** `INTERVIEW_QUESTION_GENERATE` 分支。End `result` 为 dict 则用之；若 `error` 且无合法 `questions` → `ValueError`。禁止把错误对象改写成题目。然后 `validate_ai_result`。
2. 题纲 live 在 `run_dify` 返回前剥离 `_post_workflow` 的 `inputs` 正文与 `api_key_suffix`。JD/简历 `raw_request` 形状本任务不改。
3. `SENSITIVE_AUDIT_KEYS` 增加 `jd_text`。
4. **Worker 写入（按测试证据二选一，禁止误伤 mock/分析/JD/简历）：**
   - **若** `test_question_live_worker_audit_carriers_have_no_plaintext` 在仅脱敏 provider `raw_request`/`result` 后已经 GREEN（即 `_write_stage8_raw` 不再吃到未脱敏 `provider_input`/题干），则 **保留 `ai_tasks.py` 不改**，并在实现说明里引用该测试为证据。
   - **若** 该测试仍失败（当前源码会把 `_question_memory_input` 的 `jd_text`/`resume_text` 和 `outcome.result` 题干打进 `sensitive_*_encrypted`），则对 `INTERVIEW_QUESTION_GENERATE` **且 live HTTP 已发生** 增加最小脱敏分支：写入 `_write_stage8_raw` 前替换 `provider_input` / `outcome.result` / `raw_response` 为字段名+hash+`question_count`/`workflow_id`/`provider_run_id`/`http_status`；**不得**改变 mock 题纲、`INTERVIEW_ROUND_ANALYZE`、JD、简历解析/评分的加密审计。
5. 不得为了让 live 测试通过而改掉既有 mock 测试中「加密列含 SECRET_*」的断言。

简历/mock 回归：

```text
.venv\Scripts\python.exe -m pytest tests/services/test_ai_tasks.py tests/workers/test_interview_ai_worker.py::test_dify_unconfigured_interview_falls_back_to_mock tests/workers/test_interview_ai_worker.py::test_output_invalid_from_persist_writes_encrypted_and_skips_version -q --tb=short
```

**提交边界（仅当用户明确要求）：** `dify.py`、`models/__init__.py`、相关测试；`backend/app/workers/ai_tasks.py` **仅当上述条件成立且确实做了 live 最小分支时才纳入**。禁止 `.env`。

---

## Task 4 — 虚构数据 smoke 脚本

**Consumes：** 规格 §9.3；Task 1–3 符号。
**Produces：** `backend/scripts/smoke_interview_question_dify.py` + **静态**测试（不发 HTTP）。

**允许改的文件：**

- `backend/scripts/smoke_interview_question_dify.py`
- `backend/tests/scripts/test_smoke_interview_question_dify.py`（或放在 `test_interview_question_live_dify.py` 末尾）

**禁止：** 脚本内 `create_database_engine`、Celery `apply_async`/`enqueue`、打印 `input_snapshot`/`inputs`/`Authorization`；写入真实 Key；本任务运行脚本去打 Dify。

### RED

静态读取脚本源码（`ast` 或子串）：

| 测试函数 | 断言 |
|---|---|
| `test_smoke_script_calls_run_dify_keyword_signature` | 源码含 `run_dify(`，且调用使用关键字 `task_type=` 与 `input_snapshot=`；`task_type` 为 `TASK_TYPE_INTERVIEW_QUESTION_GENERATE` |
| `test_smoke_script_fictional_prefixes` | `job_title`/`jd_text`/`resume_text` 字面量均以 `FICTIONAL-LIVE-20260818` 开头 |
| `test_smoke_script_uses_dimensions_list_not_dimensions_json_input` | `input_snapshot` 使用 `dimensions`；不把 `dimensions_json` 当作 provider 入参键 |
| `test_smoke_script_has_no_db_or_celery` | 源码不含 `create_database_engine`、`celery`、`apply_async`、`enqueue` |
| `test_smoke_script_print_allowlist` | `print(` 实参不得包含 `jd_text`、`resume_text`、`api_key`、`input_snapshot`；允许 `ok`/`http_status`/`error_code`/`question_count` |

```text
.venv\Scripts\python.exe -m pytest tests/scripts/test_smoke_interview_question_dify.py -q --tb=short
```

（若测试写在 live 文件中，改为对该文件过滤）

期望：RED（脚本尚不存在）。

### GREEN

按规格 §9.3 实现脚本：

- `get_settings.cache_clear()` 后读开关；开关 False 时调用 `run_dify` 仍走 mock，**脚本不得自行打开开关**。
- 仅 `await run_dify(task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE, input_snapshot={...虚构 dict...})`。
- 打印：`ok`、`http_status`、`error_code`、成功时 `question_count`。
- 注释写明：仅当人工在本地 `.env` 配置专用 Key、ID，且 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=true`、`ENVIRONMENT=development` 时才可能发 HTTP。

本任务 **不执行** `python scripts/smoke_interview_question_dify.py` 打网。

**提交边界：** 脚本 + 静态测试。禁止 `.env`。

---

## Task 5 — 验证与受控 live

**Consumes：** Task 1–4 GREEN。
**Produces：** 本任务 **无代码变更**（除非 Task 1–4 回归失败需回 Task 修复）。人工步骤不得由 agent 填写 Key。

**禁止：** 启动全量 worker；使用生产数据；未打 `UAT-CC-20260818` 前缀的历史简历；把 Key 写入仓库。

### 5.1 自动化必须先全绿

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/services/test_interview_question_live_dify.py tests/core/test_config.py tests/models/test_interview_ai_models.py tests/workers/test_interview_ai_worker.py tests/scripts/test_smoke_interview_question_dify.py -q --tb=short
```

若静态测试合在 live 文件中，去掉不存在的 `tests/scripts/...` 路径。
**全绿之前禁止**改本地 `.env` 专用凭据、禁止跑 live smoke。

开关关闭回归（显式）：

```text
.venv\Scripts\python.exe -m pytest tests/services/test_interview_question_live_dify.py::test_live_default_switch_off_mocks_and_posts_zero tests/workers/test_interview_ai_worker.py::test_dify_unconfigured_interview_falls_back_to_mock -q
```

### 5.2 人工写入开发凭据（agent 不做）

1. 手工导入 `docs/dify/interview-question-outline-workflow.yml`（本计划不执行）。
2. 仅写入 **本地 gitignore 的** `backend/.env`：
   - `DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY`
   - `DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID`
   - 保持 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=false` 直到下一步
3. `ENVIRONMENT=development`。

### 5.3 首次仅虚构 smoke

1. 将 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=true`。
2. **不**启动 Celery。
3. `cd backend && .venv\Scripts\python.exe scripts/smoke_interview_question_dify.py`
4. 只根据 `ok` / `http_status` / `question_count` / `error_code` 判断；终端不得出现简历/JD 正文。

失败：关开关，不要用生产数据重试。

### 5.4 成功后才允许 UAT 前缀数据

三文本均以 `UAT-CC-20260818` 开头。不得对未打前缀的候选人发 live。本计划不要求跑全量 worker。

### 5.5 回滚回归

1. `.env` 设 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=false`（可保留 Key/ID）。
2. 再跑 §5.1 开关关闭两条测试，必须 mock、零 HTTP。
3. 不删 YAML、不改数据库。

**提交边界：** 本任务无仓库文件。`.env` **永不提交**。

---

## 规格映射自检

| 规格 | 计划任务 |
|---|---|
| §3 / §5 三属性 + alias、禁回退、`_workflow_id_for`、`.env.example` | Task 1 |
| §4 / §6 门禁、mock/错误码、复用 `_post_workflow`、body `workflow_id`、分析仍 mock | Task 2 |
| §7 / §8 规范化、`output_invalid`、不 persist、`jd_text` 审计键、脱敏（含 worker 公开+加密载体）、隔离简历 | Task 3 |
| §9.3 smoke 真实签名、虚构前缀、无 DB/Celery、不打印正文 | Task 4 |
| §9.1 先测后凭据、§9.2 人工 `.env`、§9.3 先虚构、§9.4 UAT、§9.5 关开关、禁全量 worker/生产数据 | Task 5 |
| §1.2 非目标 | 全局约束 |

无 TBD。Python 属性、`run_dify` 签名、`build_dify_inputs` 键名与当前源码/规格一致。
