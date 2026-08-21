# 综合面试分析（Comprehensive Interview Analysis）设计规格

基线：当前工作区 `main` @ `52ca05b`（单轮分析已入 `ai_sensitive` 且强制 mock；面后人工 `HiringDecision` + `pending_offer` 已合入；**无**综合分析实体 / **无** `INTERVIEW_COMPREHENSIVE_*` task_type / **无**综合 API/前端）。

本规格只定义：**应聘级综合面试分析（阶段 9）**——版本模型与迁移、AI task + `ai_sensitive`（本批强制 mock）、动态 STALE、覆盖报告、API/权限、审计脱敏、幂等并发、前端边界、测试与 mock UAT。
本文件是规格，**不**含逐步实现计划；**不**改业务代码、**不**执行迁移、**不**启动 worker、**不**调用 Dify、**不**创建/处理 AI task、**不**读写/清理 Redis 队列。

关联（本规格不重复改写其已锁定语义，仅声明继承或差分）：

| 文档 | 关系 |
|---|---|
| 只读架构审计结论（阶段 9 综合分析审计会话） | **采纳**：1 轮可生成须带覆盖报告；`interviewing`+`in_progress` 可写；`pending_offer` 只读；输入仅结构化分数/引用；`ai_sensitive`+强制 mock；与 `HiringDecision` 解耦；execute 本批不读 |
| `docs/superpowers/specs/2026-08-16-stage-8-batch-1-interview-ai-design.md` | 单轮版本、证据加密、STALE 动态判定、敏感正文加密 **继承**；综合 **不得** 复制转写/长文进 snapshot |
| `docs/superpowers/specs/2026-08-19-interview-round-analysis-sensitive-queue-design.md` | 敏感队列 / `process_sensitive_ai_task` / 转投 / 重试骨架 **继承并扩展** 至新 task_type |
| `docs/superpowers/specs/2026-08-20-post-interview-hiring-decision-design.md` | HiringDecision 门禁与 `analysis_version_id`（单轮）**不变**；本规格 **不** 修改该列语义，**不** 自动创建决策 |
| `docs/superpowers/specs/2026-08-18-candidate-center-design.md` | 候选人中心仅 `recruitment.manage` **继承**；综合入口若挂中心则仅 manage |

---

## 1. 范围

### 1.1 目标

1. 新增应聘级综合分析实体（集 + 版本），由 AI task **`INTERVIEW_COMPREHENSIVE_ANALYZE`** 异步生成；结果为 **人工决策辅助**，**不**改 `pipeline_status` / `application.status`，**不** INSERT `HiringDecision`，**不**触发 Offer / 通知。
2. **允许仅 1 条**「current 且非 STALE」的单轮分析作为合格输入即可生成；结果 **必须** 输出 **覆盖报告**（覆盖不足 / 缺失轮次 / 不合格轮原因）。
3. **仅** `pipeline_status == interviewing` 且 `status == in_progress` 的应聘可 **生成**；`pending_offer`（及其他非 interviewing）**仅可读**已有综合结果，**禁止**再生成。
4. Provider / 持久化输入 **只**使用合格单轮版本的 **结构化分数与引用元数据**；**禁止**携带转写正文、JD 正文、简历正文、quote、overall_summary、维度长文等敏感长文本。
5. 新 task 进入 **`SENSITIVE_AI_TASK_TYPES`** 与 **`ai_sensitive`** 路径；本批 **强制 mock**（`run_dify` 短路 `run_mock`），**禁止** Dify live / 新 live 开关 / 专用 Key / workflow YAML。
6. 权限：**仅** `recruitment.manage` 可写、可读综合分析；**`interview.execute` 本批不得读取**综合分析（含摘要嵌入），避免决策语义扩散。
7. 与既有 **`HiringDecision.analysis_version_id`（单轮 FK）保持解耦**：决策表 **不** 增加综合版本列；综合 API **不** 写决策。

### 1.2 第一期交付物（本规格后实现须覆盖）

| 层 | 交付 |
|---|---|
| Alembic | 下一空序号迁移（预期 **015**）：综合分析表；扩展 `ck_ai_tasks_task_type` 纳入新类型 |
| 模型 / 常量 | 综合分析集/版本（及可选维度行，见 §3）；`TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE`；`TASK_TYPES` / `SENSITIVE_AI_TASK_TYPES` / Schema `TaskType` Literal |
| Service | 请求生成、dispatch、persist、list/get、动态 STALE、覆盖报告构建 |
| Worker / Provider | 敏感入口白名单扩展；mock 结果契约；`run_dify` 综合短路 mock |
| API | 应用级 generate（manage）+ list/detail（manage）；错误映射 |
| 前端 | manage 可见入口（候选人中心或时间轴 manage 区）；execute **不可见且不可调** |
| 测试 / UAT | 见 §9；规格只定义，本文件不执行 |

### 1.3 非目标（硬性）

- **不**开通 Dify live、敏感专用 Key、workflow YAML、综合 live 开关。
- **不**自动改 pipeline / application.status；**不**创建 / 更新 / 删除 `HiringDecision`；**不**扩展 `HiringDecision.analysis_version_id` 指向综合版本。
- **不**建 Offer 表/API、`offer.*` 权限、SMTP/站内通知、候选人触达。
- **不**让 `interview.execute` 读取综合分析（本批零例外）。
- **不**将转写/JD/简历/quote/维度长文写入 `ai_tasks.input_snapshot`、综合版本明文 JSONB、审计 `changes`、或公共 attempt 载体。
- **不**要求「至少 2 轮合格分析」才可生成（1 轮合法，但必须带覆盖报告）。
- **不**在默认 `celery` 队列执行综合任务；**禁止** `-Q celery,ai_sensitive` 全队列消费作为本功能交付依赖。
- **不**在本规格实施或 UAT 中处置、retry、cancel、mark-stale、SQL/Redis 干预下列受保护 running：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- **不**消费、清理、窥探默认 celery 队列既有消息。

---

## 2. 源码事实（实现必须对齐）

| 符号 / 路径 | 现状 | 本规格 |
|---|---|---|
| 综合实体 / API / 前端 | **不存在** | **新增** |
| `TASK_TYPES` / `ck_ai_tasks_task_type`（`models/ai_task.py` + 013/模型 Check） | 6 类，含 `INTERVIEW_ROUND_ANALYZE` | **扩展** + `INTERVIEW_COMPREHENSIVE_ANALYZE` |
| `SENSITIVE_AI_TASK_TYPES` | 题纲 ∪ 单轮分析 | **并入**综合 |
| `process_sensitive_ai_task` / `enqueue_sensitive_interview_ai_task` | 题纲+单轮 | **并入**综合；继续统一敏感 Celery 入口 |
| `run_dify` + `INTERVIEW_ROUND_ANALYZE` | 无条件 `run_mock` | **保持**；综合 **同等** 无条件 mock |
| `is_analysis_version_stale`（`services/interview_analyses.py`） | 转写确认指针不等或缺失 → STALE | 综合输入门禁与综合版本动态 STALE **必须复用/等价调用** |
| `derive_analysis_status`（`candidate_center.py`） | `none` / `stale` / `ready` | 覆盖报告构建 **对齐** 这些语义 |
| `create_hiring_decision` | 仅 interviewing+in_progress；绑单轮版本 | **不改**；综合 **不** 调用 |
| `HiringDecision.analysis_version_id` | FK → 单轮版本 | **保持**；本规格不解耦以外的关联 |
| `BUSINESS_TYPE_APPLICATION` | 已存在（简历评分等） | 综合 AI task **使用** `business_type=application`，`business_id=application_id` |
| `ROLE_PERMISSION_MATRIX` | manage / execute 两码已存在 | **不**新增 permission；综合 **仅** manage |
| 单轮 `request_analysis_generation` | **不**检查 `pipeline_status` | 综合生成 **必须** 检查 interviewing+in_progress（差分于单轮） |

单轮 STALE 真源（继承）：

```441:447:backend/app/services/interview_analyses.py
def is_analysis_version_stale(
    version: InterviewRoundAnalysisVersion,
    transcript: InterviewTranscript | None,
) -> bool:
    if transcript is None or transcript.current_confirmed_version_id is None:
        return True
    return version.transcript_version_id != transcript.current_confirmed_version_id
```

---

## 3. 模型与迁移

### 3.1 Task / 业务常量（锁定）

```python
TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE = "INTERVIEW_COMPREHENSIVE_ANALYZE"
# TASK_TYPES、SENSITIVE_AI_TASK_TYPES、Schema TaskType、DB ck_ai_tasks_task_type 必须包含上值
# business_type = BUSINESS_TYPE_APPLICATION
# business_id   = application.id
```

工作流元数据（mock 契约用，**非** live）：

| 键 | 值 |
|---|---|
| `schema_version` | `"1.0"` |
| `workflow_key` | `interview_comprehensive_analyze` |
| `workflow_version` | `"1.0"` |

### 3.2 表设计（镜像单轮「集 + 版本」，应聘级）

#### 3.2.1 `application_comprehensive_analyses`（每应聘至多一行）

| 列 | 类型 | 约束 / 说明 |
|---|---|---|
| `id` | UUID PK | |
| `application_id` | UUID FK → `job_applications.id` ON DELETE CASCADE | **UNIQUE** |
| `current_version_id` | UUID NULL FK → versions.id ON DELETE SET NULL（可 defer / use_alter） | 当前指针 |
| `created_at` / `updated_at` | timestamptz | |

#### 3.2.2 `application_comprehensive_analysis_versions`

| 列 | 类型 | 约束 / 说明 |
|---|---|---|
| `id` | UUID PK | |
| `analysis_id` | UUID FK → 上表 ON DELETE CASCADE | |
| `version_no` | Integer | >0；与 `analysis_id` UNIQUE |
| `version_label` | String(32) | 如 `C{n}`；与 `analysis_id` UNIQUE |
| `ai_task_id` | UUID FK → `ai_tasks.id` ON DELETE RESTRICT | **UNIQUE**（一 task 一版本） |
| `input_snapshot_hash` | String(64) | 规范化输入哈希 |
| `round_refs` | JSONB NOT NULL | **仅**结构化引用（§4.2）；禁止长文本键 |
| `coverage_report` | JSONB NOT NULL | 生成瞬间快照的覆盖报告（§5）；读路径仍须动态重算 STALE（§6） |
| `overall_score` | Numeric NULL | 可选综合分（1–5 或与单轮一致区间）；无则 NULL |
| `overall_summary_encrypted` | Text NOT NULL | 综合短摘要密文；**禁止**明文列 |
| `created_by` | UUID FK → users NULL | |
| `created_at` | timestamptz | |

**可选（一期允许省略独立维度表）**：若需结构化维度行，可加 `application_comprehensive_analysis_dimensions`（key/name/weight/score + 加密短评），规则对齐单轮互斥约束；**证据不得**复制 quote 密文到综合表——最多存 `segment_no` / `transcript_segment_id` 引用。

**禁止列**：`jd_text`、`resume_text`、`transcript_text`、明文 `overall_summary`、`quote*`、任何 HiringDecision FK、`pipeline_*` 副作用列。

### 3.3 迁移边界

- 预期 Alembic **015**（以实施时下一空序号为准；**不得**改写 014 `hiring_decisions` 语义）。
- 必须扩展 `ck_ai_tasks_task_type`（或等价）纳入新类型；失败策略对齐 013 的严格校验风格。
- **禁止**修改 `hiring_decisions.analysis_version_id` 的 FK 目标或空约束。

---

## 4. 输入契约与 Provider

### 4.1 合格单轮输入定义（锁定）

对应该聘下某一 `InterviewRound` 的单轮分析版本 **V** 合格，当且仅当：

1. 轮次 `application_id` 匹配；
2. 存在 `InterviewRoundAnalysis`，且 `current_version_id == V.id`；
3. `is_analysis_version_stale(V, transcript) is False`；
4. （推荐）轮次 `status == COMPLETED` 且非取消态——若历史数据存在取消轮仍挂分析，**默认不纳入合格输入**，计入覆盖缺口 `cancelled` / `excluded`。

**最低生成条件**：合格输入集合大小 **≥ 1**。
**不**要求 ≥ 2。

### 4.2 `round_refs` / task `input_snapshot` 允许字段（白名单）

每个合格轮仅允许类似结构（实现可用等价键名，测试断言无违禁键）：

```json
{
  "round_id": "<uuid>",
  "sequence_no": 1,
  "analysis_version_id": "<uuid>",
  "analysis_version_no": 2,
  "overall_score": 3.5,
  "dimensions": [
    {
      "dimension_key": "...",
      "dimension_name": "...",
      "weight": 20.0,
      "score": 4,
      "insufficient_information": false
    }
  ],
  "evidence_refs": [
    { "dimension_key": "...", "segment_no": 3, "transcript_segment_id": "<uuid>" }
  ]
}
```

`insufficient_information` 仅为布尔；**不得**写入不足说明长文。

**硬禁键（示例，审计与测试须拦截）**：`text`、`quote`、`overall_summary`、`analysis`、`strengths`、`risks`、`suggested_follow_ups`、`jd_text`、`jd_content`、`resume_text`、`segment_text`、`transcript_text`、以及任何 `*_encrypted` 密文拷贝进 snapshot。

任务级 snapshot 另含：`schema_version`、`task_type`、`application_id`、`workflow_key`、`workflow_version`、`input_snapshot_hash`、`requested_by`、`requested_at`、`idempotency_key`、`coverage_report`（生成请求时预计算快照）、`round_refs`。

### 4.3 Mock / Dify（锁定）

| 路径 | 行为 |
|---|---|
| `run_dify(..., INTERVIEW_COMPREHENSIVE_ANALYZE)` | **无条件** `run_mock`；禁止 HTTP |
| `run_mock` | 新增 `mock_interview_comprehensive_analyze`；输出经 `validate_ai_result` |
| live 开关 / Key / workflow id | **不存在**；配置层 **不得** 为本类型增加 live 入口 |

Mock 输出最低字段（锁定）：

- `overall_summary`（短文本，persist 时加密）
- `overall_score`（可选）
- `coverage_ack` 或实现选择 **忽略模型覆盖、以服务端 coverage_report 为准**（**推荐锁定：覆盖报告以服务端权威快照为准**，模型不得覆盖缺口分类）
- 可选 `dimension_notes`（短句列表）；不得回传转写原文

### 4.4 敏感队列（锁定）

| 项 | 要求 |
|---|---|
| 入队 | `enqueue_sensitive_interview_ai_task` → `process_sensitive_ai_task` |
| `task_routes` | 继续指向 `settings.celery_sensitive_queue_name`（默认 `ai_sensitive`） |
| 默认队列误投 | `_maybe_reroute_sensitive_from_default` 白名单 **包含** 综合类型 |
| 自动重试 / admin retry | 综合与题纲/单轮分析同走敏感入队 |
| 敏感入口拒绝 | 非 `SENSITIVE_AI_TASK_TYPES` 仍 reject，不 claim |

---

## 5. 覆盖报告（Coverage Report）

### 5.1 必须输出

每次生成的版本 **必须** 持久化 `coverage_report`，且读 API **必须** 返回（可附动态 `is_stale`）。报告至少包含：

| 字段 | 说明 |
|---|---|
| `eligible_round_count` | 合格输入轮数（≥1 才能生成） |
| `total_round_count` | 该应聘轮次总数 |
| `included_rounds` | `{ round_id, sequence_no, analysis_version_id, overall_score? }[]` |
| `gaps` | 缺口列表（见下） |
| `coverage_insufficient` | bool：当 `eligible_round_count < total_round_count` **或** 存在任何 gap **或** `eligible_round_count == 1` 且 `total_round_count >= 2` 时为 true；**即使仅 1 轮且应聘只有 1 轮**，仍须返回报告，且 UI 须展示「仅单轮覆盖」提示（可将 `coverage_insufficient=false` 但 `single_round_only=true`，**二者至少其一显式表达覆盖边界**） |

**锁定产品语义**：允许 1 条合格单轮；**必须**让调用方看到覆盖不足/缺失轮次信息，禁止静默假装「全覆盖」。

推荐同时返回：

- `single_round_only: bool`（`eligible_round_count == 1`）
- `missing_round_count: int`

### 5.2 Gap 原因码（固定枚举）

| `reason_code` | 判定依据（对齐现有符号） |
|---|---|
| `cancelled` | 轮次 `CANCELLED` |
| `ended_abnormally` | `ENDED_ABNORMALLY` |
| `not_completed` | 非 `COMPLETED`（且非上两类终态展示时可用更细码） |
| `without_transcript` | `transcript_completion_mode == WITHOUT_TRANSCRIPT` |
| `transcript_unconfirmed` | 需要转写但无 `CONFIRMED_TRANSCRIPT` / 无 `current_confirmed_version_id` |
| `analysis_none` | 完成且可分析语境下无 `current_version_id`（`derive_analysis_status` → `none`） |
| `analysis_stale` | 有 current 但 `is_analysis_version_stale` |
| `excluded_other` | 其他明确排除（须少用） |

每个 gap 项：`round_id`、`sequence_no`（若有）、`reason_code`、可选 `status` 快照字符串（非长文本）。

---

## 6. 状态机、动态 STALE、副作用禁令

### 6.1 应聘前置（生成）

| 条件 | 生成 |
|---|---|
| `status == in_progress` 且 `pipeline_status == interviewing` | **允许**（仍须 ≥1 合格单轮） |
| `pipeline_status == pending_offer` | **拒绝生成**；**允许** manage 只读已有版本 |
| `rejected` / 关闭 / 非 in_progress | **拒绝生成**；只读策略：若已有历史版本，manage 可读（审计）；无则 404 |

### 6.2 综合版本动态 STALE（锁定）

读路径函数（建议名 `is_comprehensive_version_stale(version, …)`）在下列 **任一** 成立时返回 true：

1. `round_refs` 中任一 `analysis_version_id` 不再等于该轮 `InterviewRoundAnalysis.current_version_id`；
2. 对应单轮版本 `is_analysis_version_stale(V, transcript)` 为 true；
3. 引用的轮次已被删除或不属于该应聘（防御）。

**不得**仅依赖写入时的布尔列作为唯一真源；可冗余存储 `coverage_report`，但 `is_stale` **必须动态计算**（对齐单轮 `is_stale` 模式）。

新版本成功 persist 后：推进综合集 `current_version_id`；旧版本保留历史，读列表标 `is_current` / `is_stale`。

### 6.3 副作用禁令（锁定）

综合生成成功 / 失败路径均 **禁止**：

- 修改 `job_applications.pipeline_status` / `status` / `lock_version`（除非未来另开规格；**本批禁止**为综合改 lock）
- INSERT/UPDATE `hiring_decisions`
- 调用 `create_hiring_decision`
- 发送通知 / 创建 Offer
- 触发单轮分析 regenerate 或题纲 generate

---

## 7. API / 权限 / 前端

### 7.1 权限

| 操作 | `recruitment.manage` | `interview.execute` |
|---|---|---|
| POST generate | 允许 | **403** |
| GET list / detail | 允许 | **403** |
| 前端入口可见 | 是 | **否** |

**不**新增 permission code。
**差分于单轮分析**：单轮 execute 可读分配轮；综合本批 **零读取**。

### 7.2 HTTP（建议路径，实现可微调但须应用级）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/applications/{application_id}/comprehensive-analysis/generate` | body：`idempotency_key`（必填，对齐单轮 AI generate）；**202** + task 摘要 |
| `GET` | `/applications/{application_id}/comprehensive-analysis` | 集摘要 + versions（含 `is_current`/`is_stale`/coverage） |
| `GET` | `/applications/{application_id}/comprehensive-analysis/versions/{version_id}` | 详情；`Cache-Control: no-store` |

错误映射（锁定要点）：

| 条件 | HTTP |
|---|---|
| 非 manage | 403 |
| 应聘不存在 | 404 |
| 非 interviewing+in_progress 却 POST | 409 或 400（项目 State/Validation 映射；文案含 interviewing） |
| `pending_offer` POST | 同上拒绝 |
| 合格单轮数 = 0 | 400/409（文案含 coverage / analysis） |
| inflight 冲突 | 409 |
| 幂等冲突（异哈希） | 409 |

### 7.3 前端边界

- 入口：候选人中心详情 **或** 面试时间轴 **manage** 区域；文案须标明「综合分析（辅助）」，**禁止**「自动录用 / Offer / Dify」。
- **必须**展示覆盖报告（缺口列表 / 单轮覆盖提示）。
- execute 用户：**不得**出现综合入口；回归测试断言无「多轮综合」对 execute 暴露（可更新曾硬禁文案的测试边界：**manage 可见、execute 不可见**）。
- **不得**在综合 UI 内提交 HiringDecision（决策仍走既有面板）；**不得**把综合 `version_id` 当作 HiringDecision 的 `analysis_version_id`。

---

## 8. 审计、幂等、并发

### 8.1 审计

| action | 时机 | changes 允许键（示例） |
|---|---|---|
| `comprehensive_analysis.generate_requested` | 创建 PENDING task | `application_id`、`task_id`、`task_type`、`input_snapshot_hash`、`eligible_round_count`、`gap_count`、`status` |
| `comprehensive_analysis.generated` | persist 成功 | `application_id`、`analysis_id`、`analysis_version_id`、`version_no`、`task_id`、`overall_score`、`eligible_round_count`、`coverage_insufficient` / `single_round_only` |

**禁止**：任何 `SENSITIVE_AUDIT_KEYS` 命中键；转写/JD/简历/quote/summary 正文。

### 8.2 幂等与并发

对齐单轮分析模式：

1. `idempotency_key` + actor + action + `scope_id=application_id`；请求哈希含 `input_snapshot_hash`。
2. 同 application 同 `input_snapshot_hash` 的 inflight（pending/running）→ 复用 task。
3. 不同 hash 已有 inflight → Conflict。
4. `ai_task_id` 唯一约束防双写版本；persist 前按 task 查已有版本则返回（幂等成功）。
5. API：`request_*` flush PENDING → `commit` → `dispatch_persisted_*`；dispatch 失败不回滚已提交 PENDING（`pending_dispatch`），对齐单轮。

### 8.3 与 HiringDecision 解耦（再声明）

- 综合版本 id **不是** HiringDecision 合法 `analysis_version_id`。
- Hiring 门禁继续只用单轮 `is_current ∧ ¬stale`。
- 本批 **不** 要求决策前必须存在综合分析。

---

## 9. 测试与 mock UAT

### 9.1 自动化（实现时必补）

| 类别 | 断言要点 |
|---|---|
| 生成门禁 | interviewing+in_progress+≥1 合格 → 202；pending_offer / rejected / 0 合格 → 拒绝 |
| 覆盖 | 1 轮合格生成成功且响应含覆盖边界字段；多轮有缺口时 `gaps` 非空且 `coverage_insufficient` 符合 §5 |
| 输入隐私 | snapshot / round_refs **无**违禁键；公共 attempt 载体无转写/摘要明文 |
| STALE | 推进单轮 current 或改确认转写指针 → 综合 `is_stale=true` |
| 队列 | 入队敏感 Celery；默认误投转投；retry 走敏感 |
| Mock | `run_dify` 综合不 HTTP；validate 契约 |
| 副作用 | 成功后 pipeline/status/HiringDecision 行数不变 |
| 权限 | manage 可读写；execute POST/GET → 403 |
| 幂等 / 冲突 | 同 key 同 hash 复用；异 hash inflight → 409 |
| 审计 | changes 无敏感键 |
| 回归 | HiringDecision 仍只绑单轮；单轮分析/题纲敏感路径不回退；前端 execute 无综合入口 |
| 迁移 | ck 接受新 task_type；拒绝未知类型 |

### 9.2 Fixture 最小集（隔离、虚构）

- 应聘 A：`in_progress` + `interviewing`；1 轮 COMPLETED + 确认转写 + current 非 STALE 分析（单轮覆盖用例）
- 应聘 B：同上 + 额外 CANCELLED / WITHOUT_TRANSCRIPT / stale 分析轮（缺口用例）
- 应聘 C：`pending_offer` + 已有综合版本（只读）与无版本（POST 拒绝）
- 用户：manage / execute-only
- **禁止**夹具使用或触碰受保护 task id

### 9.3 Mock UAT（规格定义，本文件不执行）

1. `AI_PROVIDER=mock`；敏感 worker **仅** `-Q ai_sensitive`（或项目等价隔离）；**不**处理默认队列积压。
2. 仅 manage 账号对隔离应聘 A/B 触发生成；确认覆盖报告与辅助文案；确认流水未变、无新 HiringDecision。
3. pending_offer 应聘：GET 可读（若有版本）；POST 拒绝。
4. execute 账号：综合 API 403；UI 无入口。
5. **禁止** retry/cancel/mark-stale 受保护 running；**禁止** Redis 清理默认队列。
6. **禁止** 打开任何综合 Dify live 配置或真实 HTTP。

---

## 10. 范围外（明确不做）

| 项 | 说明 |
|---|---|
| Dify live / YAML / 专用 Key | 硬禁 |
| 自动决策 / 改 pipeline / 写 HiringDecision | 硬禁 |
| Offer / 通知 / `offer.*` | 硬禁 |
| 修改 `HiringDecision.analysis_version_id` 语义 | 硬禁 |
| execute 读综合 | 本批硬禁 |
| 强制 ≥2 轮才生成 | 明确不做 |
| 默认队列执行综合 / 双队列混布依赖 | 硬禁 |
| 触碰两条历史 running / 清理默认队列 | 硬禁 |
| 本文件编码与 UAT 执行 | 不做 |

---

## 11. 稳定符号表

| 符号 | 值 |
|---|---|
| Task type | `INTERVIEW_COMPREHENSIVE_ANALYZE` |
| 队列 | `ai_sensitive`（`CELERY_SENSITIVE_QUEUE_NAME`） |
| Celery 入口 | `process_sensitive_ai_task` |
| business_type | `application` |
| 表（建议名） | `application_comprehensive_analyses` / `application_comprehensive_analysis_versions` |
| 版本标签 | `C{n}` |
| 生成门禁 | `interviewing` ∧ `in_progress` ∧ `eligible_round_count ≥ 1` |
| `pending_offer` | 只读，禁止生成 |
| STALE | 动态；单轮 current 指针变化 ∨ `is_analysis_version_stale` |
| 权限 | **仅** `recruitment.manage` |
| Provider | **仅 mock** |
| HiringDecision | **解耦**；不改 `analysis_version_id` |
| 迁移 | 预期 Alembic **015** |
| 受保护 running | `dde1470f-…`；`3556206d-…` |

---

## 12. 自检清单（规格完成度）

- [x] 锁定：1 轮可生成 + 必须覆盖报告 / 缺失轮次表达
- [x] 锁定：仅 `interviewing`+`in_progress` 可生成；`pending_offer` 只读
- [x] 锁定：输入仅 current 非 STALE 单轮结构化分数与引用；禁转写/JD/简历/长文本
- [x] 锁定：新 task + `ai_sensitive`；本批强制 mock；禁 Dify live
- [x] 锁定：仅辅助；不改流程、不创建 HiringDecision；与 `analysis_version_id` 解耦
- [x] 锁定：仅 manage 读写；execute 本批不读
- [x] 覆盖：模型/迁移、任务与敏感队列、动态 STALE、覆盖报告、API/权限、审计脱敏、幂等并发、前端边界、测试与 mock UAT
- [x] 明确禁止：Dify live、自动决策、Offer/通知、修改 HiringDecision、操作默认队列、两条历史 running
- [x] 无 TBD 双主方案；无真实密钥或候选人正文
- [x] 本文件仅规格；未改代码、未提交、未执行 UAT
