# 面试题纲 live Dify 受控接入设计规格

基线：当前工作区 `main` @ `1b36f8f`；题纲 YAML 资产 `docs/dify/interview-question-outline-workflow.yml`（Dify DSL `kind=app` / `version=0.7.0`）。
本规格只定义 **`INTERVIEW_QUESTION_GENERATE` 的受控 live 路径**。不写业务实现、不改 `.env` 真实值、不导入 Dify、不启动 worker、不调用 Dify。

## 1. 目标与非目标

### 1.1 目标

1. 题纲任务复用既有 Dify 调用抽象，而不是另写 HTTP 客户端。
2. 专用 API Key 与 Workflow ID **成对**配置；**禁止**回退通用 `DIFY_API_KEY`。
3. 仅在开发环境、显式开关、成对凭据、授权输入四者同时成立时发真实请求。
4. 成功输出必须通过既有 `InterviewQuestionGenerateResult` 与 snapshot 对照；失败不得落库成功题纲版本。
5. live 失败或误配不得影响简历解析 / 多维评分 / 单轮分析任务。
6. 关闭 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED` 即刻回到 mock，无需删 YAML 或改数据库。

### 1.2 非目标

- **不**对 `INTERVIEW_ROUND_ANALYZE` 做 live（继续现有强制 mock）。
- **不**改 JD 解析链、简历解析、简历评分的 URL / body / 回退规则。
- **不**把 `QUESTION_WORKFLOW_KEY` / `QUESTION_WORKFLOW_VERSION` 当作 Dify workflow ID。
- **不**自动导入 YAML、不访问 Dify 控制台、不在规格或仓库写入真实 Key / ID。
- **不**做招聘决策、Offer、通知发送、多轮综合分析。
- **不**在首次 live 验证时启动全量 Celery worker。
- **不**在生产环境开通 live。

## 2. 源码事实（实现必须对齐，不得改名逃避）

### 2.1 调用链

| 层 | 符号 | 现状 |
|---|---|---|
| Worker 入口 | `app.workers.ai_tasks._run_provider` | `AI_PROVIDER==dify` → `run_dify()`，否则 `run_mock()` |
| 题纲内存输入 | `_prepare_stage8_provider_input` → `_question_memory_input` | 组装 `job_title/jd_text/resume_text/dimensions` 等；**task JSONB snapshot 不含简历/JD 明文** |
| Provider | `run_dify(*, task_type: str, input_snapshot: dict[str, Any]) -> ProviderOutcome` | 仅这两个 keyword-only 参数；`input_snapshot` 是 `dict[str, Any]`，不是 dataclass。题纲现状：**无条件 mock**（本规格改成受控门禁，不改签名） |
| Worker 传入值 | `_run_provider(..., input_snapshot=provider_input)` | 题纲的 `provider_input` 来自 `_question_memory_input`：含 `job_title`/`jd_text`/`resume_text`/`dimensions`（**list**）及 round/job/resume id 等；**不是** Dify body |
| 输入映射 | `build_dify_inputs(task_type, input_snapshot)` | 题纲从 dict 读取 `job_title`、`jd_text`、`resume_text`、`dimensions`；写出 Dify 四键 `job_title`、`jd_text`、`resume_text`、`dimensions_json` |
| Key 选择 | `Settings.dify_api_key_for(task_type)` | 题纲走 `else` → 空专用 Key 后回退 `self.dify_api_key`（live **禁止**此回退；应读 `dify_interview_question_generate_api_key_secret`） |
| ID 选择 | `_workflow_id_for(task_type)` | 题纲 `return ""`（live 必须 `return settings.dify_interview_question_generate_workflow_id`） |
| HTTP | `_post_workflow` | `POST {DIFY_API_BASE_URL}/v1/workflows/run`；`response_mode=blocking`；ID 非空则 `body["workflow_id"]=id` |
| 输出抽取 | `_extract_outputs` | `outputs.result` 为 dict 时直接返回该对象 |
| 规范化 | `normalize_dify_outputs` | **无**题纲分支，当前 `return outputs` |
| Schema | `validate_ai_result` → `InterviewQuestionGenerateResult` | `extra=forbid` |
| Snapshot 对照 | `validate_question_result_against_snapshot` | 连续 `display_order`、已知 `dimension_key`、证据枚举规则 |
| 落库 | `persist_question_generation_result` | 校验失败抛错；worker 收成 `output_invalid`，不写成功版本 |
| 公开 raw | `_stage8_public_payload` / `_write_stage8_raw` | JSONB 仅元数据；正文进 `sensitive_*_encrypted` |

### 2.2 既有 WORKFLOW_ID 确实参与请求

```496:501:backend/app/services/ai_providers/dify.py
    if workflow_id:
        body["workflow_id"] = workflow_id
    raw_request = {
        ...
        "workflow_id": workflow_id or None,
```

因此题纲 live **必须**配置并传递专用 Workflow ID。注释「仅作标记、不能跨应用切换」只说明鉴权仍靠 API Key，不表示请求体省略该字段。

业务 snapshot 常量（**不是** Dify ID）：

```57:59:backend/app/models/ai_task.py
QUESTION_SNAPSHOT_SCHEMA_VERSION = "1.0"
QUESTION_WORKFLOW_KEY = "interview_question_generate"
QUESTION_WORKFLOW_VERSION = "1.0"
```

### 2.3 简历路径对照（可复用形状，不可复用回退）

- `RESUME_SCORE`：专用 Key **与** `DIFY_RESUME_SCORE_WORKFLOW_ID` 同时存在才 live；否则 `dify_not_configured`。
- `RESUME_PARSE`：允许「专用 Key」或「通用 Key + workflow ID」。
- 题纲 live **严于**简历解析：禁止通用 Key 回退；开关默认关闭；生产拒绝 live HTTP。

### 2.4 审计敏感键缺口

`SENSITIVE_AUDIT_KEYS` 已含 `resume_text`、`jd_content`、`api_key`、`transcript_text`、`meeting_password` 等，**尚未**包含 `jd_text`。实现时必须把 `jd_text` 加入该集合，防止审计写入 JD 正文。

### 2.5 `run_dify` 真实签名与题纲 provider 输入

源码（不得改名、不得增加位置参数）：

```python
async def run_dify(
    *,
    task_type: str,
    input_snapshot: dict[str, Any],
) -> ProviderOutcome:
```

`build_dify_inputs` 对 `INTERVIEW_QUESTION_GENERATE` 实际读取的键：

| provider `input_snapshot` 键 | 类型 | 映射到 Dify Start |
|---|---|---|
| `job_title` | str（缺省 →「未命名岗位」） | `job_title` |
| `jd_text` | str | `jd_text` |
| `resume_text` | str | `resume_text` |
| `dimensions` | list（再 `json.dumps`） | `dimensions_json` |

**没有**名为 `dimensions_json` 的 provider 入参键。smoke / 单测必须按上表构造 `input_snapshot=`，禁止杜撰其它参数名。

## 3. 稳定接口名（后续实现不得改名）

| 符号 | 锁定值 / 签名 |
|---|---|
| 任务类型 | `TASK_TYPE_INTERVIEW_QUESTION_GENERATE` = `"INTERVIEW_QUESTION_GENERATE"` |
| 开关 | Python：`dify_interview_question_live_enabled`（默认 `False`）；环境变量：`DIFY_INTERVIEW_QUESTION_LIVE_ENABLED` |
| 专用 Key | Python：`dify_interview_question_generate_api_key_secret`；环境变量：`DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY` |
| 专用 ID | Python：`dify_interview_question_generate_workflow_id`；环境变量：`DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID` |
| UAT 前缀 | `INTERVIEW_QUESTION_LIVE_UAT_PREFIX = "UAT-CC-20260818"` |
| 虚构前缀 | `INTERVIEW_QUESTION_LIVE_FICTIONAL_PREFIX = "FICTIONAL-LIVE-20260818"` |
| 门禁函数 | `interview_question_live_http_allowed(settings, inputs) -> LiveGateDecision` |
| 凭据函数 | 继续用 `dify_api_key_for` / `_workflow_id_for`，**禁止**第三套 HTTP 客户端 |
| 输入映射 | 继续用 `build_dify_inputs` |
| 请求 | 继续用 `_post_workflow` |
| 错误码 | `interview_question_live_forbidden`、`interview_question_live_not_configured`、`interview_question_live_unauthorized` |
| YAML 资产 | `docs/dify/interview-question-outline-workflow.yml` |
| 首次 live 脚本 | `backend/scripts/smoke_interview_question_dify.py`（不启动 Celery） |

`LiveGateDecision` 字段：`allow_http: bool`、`fallback_mock: bool`、`error_code: str | None`、`reason: str`（reason 只含环境名/开关布尔/字段名，不含 Key 或正文）。

## 4. 对接一致性

题纲 live 必须走：

```text
Settings
  → dify_api_key_for(...)  # 读 dify_interview_question_generate_api_key_secret
  → _workflow_id_for(...)  # 读 dify_interview_question_generate_workflow_id
  → build_dify_inputs(task_type, input_snapshot)
  → _post_workflow(task_type=..., input_snapshot=...)
       POST {DIFY_API_BASE_URL}/v1/workflows/run
       Authorization: Bearer <专用 Key>
       body.inputs = {job_title, jd_text, resume_text, dimensions_json}
       body.response_mode = "blocking"
       body.user = "ai-task-INTERVIEW_QUESTION_GENERATE"
       若 ID 非空：body.workflow_id = <专用 ID>
  → _extract_outputs
  → normalize_dify_outputs(INTERVIEW_QUESTION_GENERATE, ...)
  → validate_ai_result(...)
```

规则：

1. 不新增第二个 Dify base URL，不改 path。
2. ID 非空时必须写入 `body.workflow_id`，并进入最小审计元数据的 `workflow_id` 字段（这是工作流标识，不是 API Key）。
3. `QUESTION_WORKFLOW_KEY` / `QUESTION_WORKFLOW_VERSION` 只留在 `ai_tasks.input_snapshot` 与公开 raw 的 `workflow_version`（现有行为）；**禁止**当作 `body.workflow_id`。
4. `normalize_dify_outputs` 为本任务类型新增显式分支：从 End `result` 取出对象；若含 `error` 且无合法 `questions`，抛 `ValueError`（最终 `output_validation_failed`）。禁止把错误对象改写成伪造题目。
5. `_post_workflow` 对 JD/简历的 `raw_request.inputs` 形状保持不变。题纲 live 在 `run_dify` 返回前必须脱敏（见 §7），不得把题纲正文/Key 后缀写进公开 JSONB。

## 5. 专用配置

### 5.1 命名分列（必须遵守）

`Settings` 源码里 Dify 相关命名已经分两套，实现不得再把环境变量名直接当 Python 属性用在新字段上：

| 类别 | Python 属性（源码现状） | 环境变量 |
|---|---|---|
| 专用 API Key | 小写 snake_case + `_secret`，如 `dify_resume_score_api_key_secret` | `Field(validation_alias="DIFY_RESUME_SCORE_API_KEY")` |
| 既有 Workflow ID | **属性与 env 同为全大写**，如 `DIFY_RESUME_SCORE_WORKFLOW_ID` | 同名 |
| 环境/开关类 | 多为全大写，如 `ENVIRONMENT`、`AI_PROVIDER`、`DIFY_API_BASE_URL` | 同名 |

题纲 **新增三项** 一律走专用 Key 那套：**Python 小写 snake_case** + **`validation_alias` 全大写环境变量**。不回改既有四个 `DIFY_*_WORKFLOW_ID` 属性。

| Python `Settings` 属性 | 类型 / 默认 | 环境变量（`.env.example`） | 谁读取 Python 属性 |
|---|---|---|---|
| `dify_interview_question_generate_api_key_secret` | `SecretStr`，默认空 | `DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY` | `dify_api_key_for()`：`self.dify_interview_question_generate_api_key_secret.get_secret_value().strip()` |
| `dify_interview_question_generate_workflow_id` | `str = ""` | `DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID` | `_workflow_id_for()`：`settings.dify_interview_question_generate_workflow_id` |
| `dify_interview_question_live_enabled` | `bool = False` | `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED` | 门禁：`settings.dify_interview_question_live_enabled is True` |

Key 字段写法对齐既有：

```python
dify_interview_question_generate_api_key_secret: SecretStr = Field(
    validation_alias="DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY",
    default=SecretStr(""),
)
```

ID / 开关同样使用 `validation_alias`，Python 名保持小写。`_workflow_id_for` 既有分支继续 `settings.DIFY_JD_PARSE_WORKFLOW_ID` 等全大写属性；**仅题纲新分支**读 `settings.dify_interview_question_generate_workflow_id`。

### 5.2 取值规则

`dify_api_key_for`：当 `task_type == INTERVIEW_QUESTION_GENERATE` 时只返回专用 Key 的 strip 值；未配置返回 `""`，**不得** `or self.dify_api_key`。

`_workflow_id_for`：该任务 `return settings.dify_interview_question_generate_workflow_id`（返回前可 `.strip()`，与门禁非空判断一致）。

成对规则：`dify_interview_question_generate_api_key_secret` 与 `dify_interview_question_generate_workflow_id` 必须同时非空才视为「凭据就绪」。只配其中一个 → 与未配置相同，禁止 HTTP。

### 5.3 `.env.example`（只写环境变量名）

只追加全大写变量名与安全注释，示例值必须为空。禁止在 example 或规格里写 Python 属性名当作 env：

```text
# 面试题纲 live（默认关闭）。Key 与 Workflow ID 必须成对；禁止复用 DIFY_API_KEY。
# 仅 ENVIRONMENT=development 且本开关为 true 时才允许 HTTP。
DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=false
DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY=
DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID=
```

禁止把真实 Key/ID 写入规格、YAML、测试夹具、提交内容。测试使用明显假值（例如 `test-interview-question-key` / `test-interview-question-workflow-id`）赋给 **Python 属性**。

## 6. 强制门禁

在 `run_dify()` 替换「题纲无条件 mock」，但 **分析任务仍无条件 mock**。

### 6.1 允许 HTTP 的充要条件

以下 **全部** 成立才调用 `_post_workflow`：

1. `task_type == INTERVIEW_QUESTION_GENERATE`
2. `settings.ENVIRONMENT.strip().lower() == "development"`（其它任何值，含空、`production`、`staging`，均拒绝 live HTTP）
3. `settings.dify_interview_question_live_enabled is True`
4. `settings.dify_interview_question_generate_api_key_secret.get_secret_value().strip()` 非空
5. `settings.dify_interview_question_generate_workflow_id.strip()` 非空
6. `build_dify_inputs` 之后的四个键恰好为 `{job_title, jd_text, resume_text, dimensions_json}`，无额外键
7. `job_title`、`jd_text`、`resume_text` 均以同一授权前缀开头：
   - `UAT-CC-20260818`，或
   - `FICTIONAL-LIVE-20260818`
8. 三者前缀必须同类（不可岗位用 UAT、简历用虚构）

`dimensions_json` 不要求前缀（结构化维度快照，不含联系方式约定）；但仍禁止夹带 `password` / `token` / `api_key` 键。

### 6.2 不满足时的行为（禁止网络）

| 场景 | HTTP | 结果 |
|---|---|---|
| 非 `development` | 禁止 | **忽略开关/Key/ID**；沿用 `run_mock`（题纲 UI 不中断）；公开元数据 `live_refused_reason=environment` |
| `development` 且 `dify_interview_question_live_enabled is False` | 禁止 | 沿用 `run_mock`（与今日行为一致） |
| 开关为 True 但 Key/ID 不成对或缺 `settings.DIFY_API_BASE_URL` | 禁止 | **不 mock**；`ok=false`，`error_code=interview_question_live_not_configured`，`error_category=non_retryable` |
| 开关 `true`、凭据就绪、输入未授权 | 禁止 | **不 mock**；`error_code=interview_question_live_unauthorized`，`non_retryable` |
| 分析任务 `INTERVIEW_ROUND_ANALYZE` | 禁止 | 继续无条件 mock |

实现必须让测试能断言 `httpx.AsyncClient.post` / `_post_workflow` **零次调用**。

生产即使误把开关设为 true、误填 Key/ID，也不得发出题纲 live 请求。

## 7. 数据边界与审计

### 7.1 允许发往 Dify 的输入

仅这四项（名称锁定，与 YAML Start 变量一致）：

| 键 | 来源 |
|---|---|
| `job_title` | `QuestionProviderInput.job_title` ← 岗位 `name` |
| `jd_text` | `_job_plaintext(job_version)` |
| `resume_text` | `_resume_plaintext(resume_version)` |
| `dimensions_json` | `json.dumps(dimensions)` |

**明确会外发** `jd_text` 与 `resume_text`。因此只允许 §6.1 的开发/UAT 授权前缀数据。不得把生产候选人简历或未打前缀的真实 JD 送出。

禁止出现在 `body.inputs` 的项（有则门禁失败，不发 HTTP）：

- 密码、token、Authorization、cookie、API Key
- `sensitive_*_encrypted`、题纲/分析密文列、`raw_request` / `raw_response` / `result_payload`
- 会议链接、`meeting_password`
- 转写正文、`segments` / `segments_json` / `quote`
- `candidate_id`、邮箱、电话（题纲映射本身不含这些键；若内存输入被污染必须拦截）

### 7.2 审计与持久化

公开 JSONB（`AITask.raw_request/raw_response/result_payload`、`AITaskAttempt.raw_response`）只允许：

- `task_type`
- `environment`
- `provider`（`dify` 或 `mock`）
- `input_field_names`（排序后的键名列表）
- `input_snapshot_hash`、各输入字段 sha256（`job_title_sha256` 等，不是正文）
- `http_status`
- `workflow_id`（专用 ID，非 Key）
- `provider_run_id` / `request_id`（现有 `extract_dify_run_ids`）
- `question_count` 或 `validation_error_code`
- `live_refused_reason`（若未发 HTTP）
- 结果状态由 task/attempt `status` 表达

禁止公开或日志打印：正文、Key、`api_key_suffix`（`_post_workflow` 现有后缀字段对题纲 live 必须在返回前删除）。

题纲 live 的 `sensitive_request_encrypted` / `sensitive_response_encrypted`：

- **不得**再写入 `jd_text` / `resume_text` 明文或完整 `inputs` 字典
- 可加密保存与公开字段同构的元数据 + 输出 `questions` 的 **hash**（不是题干明文）
- 这比当前 mock 路径「整包加密 provider_input」更严，且只约束题纲 live；分析/简历路径本批不动

审计 `record_audit` 的 `changes` 必须继续被 `SENSITIVE_AUDIT_KEYS` 拦截；并补 `jd_text`。

## 8. 输出与失败

### 8.1 成功

`InterviewQuestionGenerateResult`：

- `questions`：1–30
- 每项：`dimension_key`、`question`、`purpose`、`evidence_source` ∈ {`JOB_REQUIREMENT`,`RESUME_EXPERIENCE`,`GENERAL`}、`resume_evidence?`、`follow_up_prompts[]`、`risk_flags[]`、`display_order`
- `validate_question_result_against_snapshot`：`display_order` 为 `1..N` 连续；`dimension_key` 必须属于 snapshot；`RESUME_EXPERIENCE` 必须有 `resume_evidence`，其它枚举必须为空；题干去空白后不得重复
- `extra=forbid`：出现 `hire` / `offer` 等 → 非法
- 通过后才调用既有 `persist_question_generation_result`

### 8.2 失败（不得落库成功版本）

| Dify / 解析情况 | 处理 |
|---|---|
| 工作流 End 返回 `{error:true,...}` 且无合法 `questions` | `output_validation_failed` → 任务 `output_invalid` |
| HTTP 200 但 JSON 非法、缺字段、枚举非法、排序不连续 | 同上；**禁止**把 mock 题目写回去 |
| HTTP 4xx/5xx / 超时 / 网络错 | 沿用 `classify_http_error`；不 persist 版本 |
| persist 二次校验失败 | worker 已有 `_STAGE8_OUTPUT_INVALID_EXCEPTIONS` → `output_invalid` |

`AI_TASK_STATUS_OUTPUT_INVALID = "output_invalid"`。既有测试 `test_output_invalid_from_persist_writes_encrypted_and_skips_version` 语义保持。

### 8.3 隔离

- 题纲 live 失败只更新该 `ai_tasks.id` 及其 attempts。
- 禁止重试或补偿时改写 `RESUME_PARSE` / `RESUME_SCORE` / `JD_PARSE` 任务。
- `_after_task_success` 仍按 `task_type` 分支；题纲只进 `persist_question_generation_result`。
- `_after_task_failure` 对题纲保持现状（不调用简历失败回调）。

## 9. 验证与首次 live

### 9.1 TDD（必须先全绿，才允许人工填开发凭据）

测试文件建议：`backend/tests/services/test_interview_question_live_dify.py`（可补 worker 单测）。用 monkeypatch 拦截 `_post_workflow` / `httpx.AsyncClient.post`。

必须覆盖：

1. 默认 `dify_interview_question_live_enabled is False` → mock，零 HTTP。
2. `settings.ENVIRONMENT="production"` + 开关 True + 成对假凭据 → 零 HTTP（忽略开关）。
3. 开关 True 但缺 Key 或缺 ID 或只配其一 → `interview_question_live_not_configured`，零 HTTP，且 **不**回退 `self.dify_api_key`。
4. `dify_api_key_for(TASK_TYPE_INTERVIEW_QUESTION_GENERATE)` 在 `dify_interview_question_generate_api_key_secret` 为空时返回空串，即使 `dify_api_key_secret` / env `DIFY_API_KEY` 有值。
5. `_workflow_id_for` 返回 `settings.dify_interview_question_generate_workflow_id`；允许 HTTP 时 `_post_workflow` 的 body 含该 `workflow_id`。
6. 输入缺少授权前缀 → `interview_question_live_unauthorized`，零 HTTP。
7. 虚构前缀与 UAT 前缀混用 → unauthorized，零 HTTP。
8. `build_dify_inputs(TASK_TYPE_INTERVIEW_QUESTION_GENERATE, input_snapshot)` 的**返回值**键集合恰好为 `job_title`/`jd_text`/`resume_text`/`dimensions_json`；其**入参 dict** 使用 `dimensions`（list），没有 `dimensions_json` 入参键；无 `segments_json` / `candidate_id` / `password`。
9. 伪造 Dify 200 + 合法 `questions` → `validate_ai_result` 通过。
10. 伪造 200 + 非法 JSON / 非法枚举 / `display_order` 跳号 → `output_validation_failed`，无成功 persist。
11. 公开 raw / audit 不含正文、不含 Key、不含 `api_key_suffix`；含字段名与 hash。
12. 分析任务在开关 true 时仍 mock。
13. 题纲 `output_invalid` 不修改任何 `RESUME_*` 任务夹具。

禁止这些测试访问真实 Dify 网络。

### 9.2 人工写入开发凭据（测试全绿之后）

1. 手工导入 `docs/dify/interview-question-outline-workflow.yml`（本规格不执行）。
2. 在 Dify 绑定本环境模型，复制该应用的 API Key 与 workflow ID。
3. **仅**写入本地开发 `.env`（已 gitignore），变量名见 §5。
4. 保持 env `ENVIRONMENT=development`，`DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=false` 直到准备第一次调用。

### 9.3 首次 live（虚构，不启动全量 worker）

脚本 `backend/scripts/smoke_interview_question_dify.py` 只调用现有签名，关键字参数名必须与源码一致：

```python
async def run_dify(
    *,
    task_type: str,
    input_snapshot: dict[str, Any],
) -> ProviderOutcome:
```

构造 **纯内存** `dict[str, Any]`，键必须是 `build_dify_inputs(INTERVIEW_QUESTION_GENERATE)` 实际读取的键（`job_title` / `jd_text` / `resume_text` / `dimensions`），不要传 `dimensions_json`，不要增加签名里不存在的参数：

```python
await run_dify(
    task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    input_snapshot={
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
    },
)
```

约束：

- 不读数据库、不加载真实应聘/简历、不 enqueue Celery、不启动全量 worker
- 三文本字段均以 `FICTIONAL-LIVE-20260818` 开头（满足 §6.1）
- 打印只允许：`ok`、`http_status`、`question_count`、`error_code`；禁止打印 `input_snapshot` 正文、Dify body、Key

### 9.4 受控 UAT

仅在虚构 live 成功后，使用岗位名、JD、简历标准化文本均以 `UAT-CC-20260818` 开头的开发数据走题纲生成。不得对未打前缀的历史候选人简历发 live。

### 9.5 回滚

将环境变量 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED` 设为 `false`（或不设，对应 `settings.dify_interview_question_live_enabled is False`）并重启 API/worker 进程后，`run_dify` 对题纲立即 mock。

- 不需要删除 Dify YAML
- 不需要改数据库、不需要 downgrade
- 不需要删除专用 Key/ID（留着也不会发 HTTP）
- 分析任务始终 mock，不受此开关影响

## 10. 实现落点（本规格不执行）

| 文件 | 变更意图 |
|---|---|
| `backend/app/core/config.py` | 三个 **小写** Python 属性 + `validation_alias`；`dify_api_key_for` 读 `dify_interview_question_generate_api_key_secret` 且禁止回退 |
| `backend/.env.example` | 空值变量名 + 安全注释 |
| `backend/app/services/ai_providers/dify.py` | 门禁读小写 Settings 属性；`_workflow_id_for` 题纲分支读 `dify_interview_question_generate_workflow_id`；`normalize_dify_outputs` 题纲分支；live 脱敏。签名 `run_dify(*, task_type, input_snapshot)` 不变 |
| `backend/app/models/__init__.py` | `SENSITIVE_AUDIT_KEYS` 增加 `jd_text` |
| `backend/tests/services/test_interview_question_live_dify.py` | §9.1 |
| `backend/scripts/smoke_interview_question_dify.py` | §9.3（脚本本身不发请求，除非本地显式打开开关） |
| `backend/app/workers/ai_tasks.py` | 原则上不改分支结构；公开 raw 已走 `_stage8_public_payload`，live 脱敏在 provider 完成 |

**不改**：Alembic、前端、简历/JD provider 行为、`INTERVIEW_ROUND_ANALYZE` mock 门禁、数据库。

## 11. 验收清单

- [ ] 题纲 live 只通过 `_post_workflow` 发 `POST /v1/workflows/run`
- [ ] ID 非空出现在 body 与最小审计元数据
- [ ] 无通用 Key 回退
- [ ] 非 development 零 HTTP
- [ ] 开关 false → mock
- [ ] 未授权输入零 HTTP
- [ ] 非法输出 → `output_invalid`，无成功题纲版本
- [ ] 简历任务不受影响
- [ ] 关闭开关即 mock
- [ ] 仓库与规格无真实 Key/ID/简历正文
