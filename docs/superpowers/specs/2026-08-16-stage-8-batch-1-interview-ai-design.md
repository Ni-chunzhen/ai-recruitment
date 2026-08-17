# AI 招聘系统阶段 8 第一批：面试问题生成 + 单轮 AI 分析设计规格（修正版）

## 1. 目标与非目标

### 1.1 目标

1. **面试问题生成**：手动触发；输入冻结为绑定岗位版本、确认简历版本与轮次元数据；人工可编辑新版本；AI 原始版本不可变。
2. **单轮 AI 分析**：仅 `COMPLETED` + `CONFIRMED_TRANSCRIPT` + 存在 `current_confirmed_version_id`；基于当前 Cn 可分析片段；维度分 1–5；总分后端按冻结权重计算。

复用 `ai_tasks` / `ai_task_attempts`、面试幂等、Fernet、RBAC、审计。迁移：`013_stage8_interview_ai_foundation`（revises `012_transcript_workflow`，revision 长度 34 ≤ 64）。

### 1.2 非目标

多轮综合分析；自动出题；音视频/听记接口；录用/淘汰/Offer；通知发送；Map-Reduce；真实 Dify 联调验收；改变候选人/申请/轮次业务状态；在 `InterviewRound` 堆 AI 大字段。

## 2. 可复用基础

| 能力 | 事实 |
|---|---|
| task_type 惯例 | 大写：`JD_PARSE`、`RESUME_SCORE` 等（`app/models/ai_task.py`） |
| 本批新增类型 | `INTERVIEW_QUESTION_GENERATE`、`INTERVIEW_ROUND_ANALYZE` |
| business_type 常量 | 现有 `job` / `resume_version` / `application`；本批新增常量 `interview_round`（**DB 无 Check**，见 §4） |
| 岗位维度 | `JobVersion.score_dimensions`：仅 `name/weight/description/anchors/custom?`，**无既有 dimension_key** |
| 转写片段 | `is_included_in_analysis`、`text_encrypted` |
| raw 落库现状 | `AITask.raw_request` / `raw_response` / `result_payload`（JSONB）；`AITaskAttempt.raw_response`（JSONB）+ `response_purged_at` |
| task_type/business_type DB Check | **004–012 均不存在**；合法集合仅由 ORM/服务层约束 |

## 3. dimension_key 最终规则

`JobVersion.score_dimensions` 无稳定业务 key。统一规则：

1. 从**冻结** `score_dimensions` 原始数组顺序生成 key，从 1 开始：`D001`、`D002`、`D003`…（三位零填充）。
2. 启动任务时构造 dimensions snapshot，每项必须含：
   - `dimension_key`
   - `display_order`（与数组下标一致，从 1 起）
   - `name`、`weight`、`description`、`anchors`
3. Dify **只能**返回快照中已有的 `dimension_key`。
4. 缺失、重复或未知 `dimension_key` → 任务 `output_invalid`。
5. 同名维度允许共存；**禁止**用 `name` 作 DB 唯一键；分析维度表唯一键为 `(analysis_version_id, dimension_key)`。
6. 同任务重试必须复用原 `input_snapshot` 内 snapshot 的 key，**禁止**重读岗位版本后重新编号。
7. 题纲 `interview_question_items.dimension_key` 引用同一套 key 规则（可多题同 key）。

辅助函数约定名（后续实现不得改名）：

- `build_dimension_snapshot(score_dimensions: list) -> list[dict]`
- `allocate_dimension_key(index_1_based: int) -> str`  # → `D{index:03d}`

## 4. ai_tasks / attempts 真实结构与 013 扩展

### 4.1 审计结论

| 对象 | 事实 |
|---|---|
| `AITask.task_type` | `String(64)`，**无** `CheckConstraint` |
| `AITask.business_type` | `String(64)`，**无** `CheckConstraint` |
| 敏感明文风险列 | `AITask.input_snapshot` / `raw_request` / `raw_response` / `result_payload`；`AITaskAttempt.raw_response` |
| purge | `AITask.raw_purged_at` 清空 task raw；attempt `raw_response` + `response_purged_at` |

### 4.2 013 对 task_type 的决定

因 012 无 Check，本批 **新建**（非“加入既有 Check”）：

- 约束名：`ck_ai_tasks_task_type`（≤63）
- 合法值：`JD_PARSE`、`SCORE_DIMENSION_RECOMMEND`、`RESUME_PARSE`、`RESUME_SCORE`、`INTERVIEW_QUESTION_GENERATE`、`INTERVIEW_ROUND_ANALYZE`

**不**新建 `business_type` Check（避免对历史任意字符串业务类型造成不一致收紧）。ORM 增加 `BUSINESS_TYPE_INTERVIEW_ROUND = "interview_round"`，服务层校验。

### 4.3 downgrade 与阶段8任务数据

回退 013 **预期删除**阶段8任务历史。顺序：

1. 删除七张业务表（先循环 FK，再子表→父表）。
2. `DELETE FROM ai_task_attempts WHERE task_id IN (SELECT id FROM ai_tasks WHERE task_type IN (...stage8...))`（或 CASCADE 删 task）。
3. `DELETE FROM ai_tasks WHERE task_type IN ('INTERVIEW_QUESTION_GENERATE','INTERVIEW_ROUND_ANALYZE')`。
4. `DROP CONSTRAINT ck_ai_tasks_task_type`（恢复 012：无 task_type Check）。
5. 删除 attempt 上 013 新增加密列。
6. **不**删除其他 task_type 的历史行；**不**动 012 转写结构。

说明：downgrade 后因无 Check，任意 `task_type` 字符串又可插入——这是恢复 012 真实行为，不是“再次拒绝新类型”。PG 测试锁定该真实语义。

### 4.4 敏感 raw 加密列（最终选定）

| 列 | 表 | 类型 | nullable | 用途 |
|---|---|---|---|---|
| `sensitive_request_encrypted` | `ai_task_attempts` | `TEXT` | YES | 仅两阶段8类型在 OUTPUT_INVALID 等需排错时写入 Fernet 密文 |
| `sensitive_response_encrypted` | `ai_task_attempts` | `TEXT` | YES | 同上 |

选择 attempt 表的原因：现有 per-try 审计与 `response_purged_at` purge 路径在 attempt；task 级 `raw_*` JSONB 对阶段8类型只存非敏感元数据。  
普通 API **永不**返回这两列；purge 同时置空；downgrade 删除这两列。

## 5. 敏感 AI 数据存储方案（阻塞项已关闭）

### 5.1 禁止明文进入普通 JSONB

禁止写入 `input_snapshot` / `raw_request` / `raw_response` / `result_payload` 的明文：

简历正文、转写正文、问题/purpose/resume evidence、分析正文、strengths/risks/follow-ups、evidence quote、Dify 原始含正文响应、候选人姓名/邮箱/手机、面试官展示名、JD 原文。

### 5.2 `input_snapshot` 白名单（不可变引用）

公共：`schema_version`、`task_type`、`round_id`、`job_version_id`、`workflow_key`、`workflow_version`、`requested_by`、`requested_at`、`idempotency_key`、`request_hash`、`input_snapshot_hash`、`dimensions`（含 §3 字段，**anchors 为非敏感评分说明文本，允许**）。

问题任务另加：`resume_version_id`。

分析任务另加：`transcript_id`、`transcript_version_id`、`segments: [{segment_id, segment_no, plaintext_sha256}]`（**无 text**）。

### 5.3 Worker 加载与哈希复核

Worker 仅按 snapshot 中钉死的 FK/ID 加载不可变 `JobVersion` / 确认 `ResumeVersion` / 确认 `TranscriptVersion` 及指定 segment；解密仅在内存。重算 plaintext SHA-256，与 snapshot 不一致 → `output_invalid`/安全失败。重试不读“当前指针”。

### 5.4 非敏感 raw / result_payload

对两阶段8类型，JSONB 仅可含：`provider`、`workflow_version`、`http_status`、token/耗时、内容 hash、`validation_error_code` 等。成功正文写入七表 Fernet 列。

### 5.5 审计

审计防护按职责分离：

- key 级：`SENSITIVE_AUDIT_KEYS` 以精确键名拒绝 `question`、`purpose`、`resume_evidence`、`follow_up_prompts`、`risk_flags`、`overall_summary`、`analysis`、`strengths`、`risks`、`insufficient_information`、`suggested_follow_ups`、`quote`、`raw_request`、`raw_response`、`result_payload`、`sensitive_request` / `sensitive_response`、正文键及所有已知 `*_encrypted` 键；递归检查 dict、list 与 tuple。
- value 级：`SENSITIVE_VALUE_MARKERS` 只清除值中明确的凭据或项目密文标记，如 `password`、`token`、`authorization`、`cookie`、`secret`、`api_key`、`bearer`、`enc:v1:`；不得以 `question`、`quote`、`analysis`、`encrypted` 等业务语义词猜测敏感正文。
- 调用方：只传 ID、版本、计数、状态和错误码，禁止先把正文传入审计再依赖 value scrub 遮盖。

## 6. 七表及基础设施最终迁移范围

### 6.1 `interview_question_sets`（每轮唯一聚合根）

字段：`id`；`interview_round_id` UNIQUE FK CASCADE；`current_version_id` NULL（循环 FK SET NULL）；`status`；`confirmed_by`/`confirmed_at`（成对）；`created_by`/`created_at`/`updated_at`。

**不含** `job_version_id` / `resume_version_id`。

状态：`DRAFT` | `READY` | `ARCHIVED`。  
READY ⇒ `current_version_id`、`confirmed_by`、`confirmed_at` 均非空。  
READY 后再编辑：服务改回 DRAFT、清确认字段、写新 `MANUAL_EDIT` 版本。

删除策略：正常 service 不提供删除单个题纲 version 的动作，ARCHIVED 历史版本也不由普通 API 删除；删 set（随 round CASCADE）删除全部 versions/items。循环 FK 的 `ON DELETE SET NULL` 是数据库层行为：`DRAFT + current_version_id` 可在直接删除该 version 后将指针置空；`READY + current_version_id + confirmed_by/at` 直接删除 current version 时，SET NULL 形成的中间结果违反 `ck_interview_question_sets_ready_requires_confirm`，因此整条 DELETE 被数据库拒绝。013 downgrade 会先删除循环 FK，再按子表到父表删除，不经过这条业务 Check 所阻止的单版删除路径。`ai_task_id` RESTRICT 仍阻止先删被引用 task。

### 6.2 `interview_question_versions`

字段：`id`；`question_set_id` FK CASCADE；`version_no`；`version_label`；`source_type`；`ai_task_id` NULL FK **ai_tasks RESTRICT**；`job_version_id` NOT NULL FK job_versions RESTRICT；`resume_version_id` NOT NULL FK resume_versions RESTRICT；`input_snapshot_hash`；`created_by`/`created_at`。

约束：

- `unique(set, version_no)`、`unique(set, version_label)`、`version_no > 0`
- `source_type ∈ AI_GENERATED, MANUAL_EDIT`
- Check `ck_question_versions_source_ai_task`：  
  `AI_GENERATED ⇒ ai_task_id IS NOT NULL`；`MANUAL_EDIT ⇒ ai_task_id IS NULL`
- `ai_task_id` 非空时全局 UNIQUE（一名任务至多一版；禁止删版后复用同 task 再生第二版）

MANUAL_EDIT：**继承**来源版本的 `job_version_id`、`resume_version_id`、`input_snapshot_hash`。不设 `source_version_id`。

### 6.3 `interview_question_items`

同前：加密题干/purpose/follow_ups/risk_flags；`resume_evidence_encrypted` 可空；`evidence_source`；`display_order > 0` 版本内唯一；`dimension_key` 不唯一。

### 6.4–6.7 分析四表

与检查点 A 一致，补充：

- `interview_round_analyses`：每轮唯一；循环 `current_version_id` SET NULL；无持久化 STALE。
- `analysis_versions`：`transcript_version_id` / `job_version_id` / `ai_task_id` 均 RESTRICT；`ai_task_id` UNIQUE；`overall_score` NULL 或 \[1,5\]；`dimensions_snapshot` JSONB（含 dimension_key，无正文）。
- `analysis_dimensions`：`(version, dimension_key)` 唯一；score/insufficient 互斥 Check；weight `(0,100]`。
- `analysis_evidence`：`(dimension, segment)` 唯一；`segment_no > 0`；segment FK RESTRICT。

STALE：`analysis_version.transcript_version_id != transcript.current_confirmed_version_id`（动态）。

### 6.8 基础设施

- 七表 + 两循环 FK  
- `ck_ai_tasks_task_type`  
- `ai_task_attempts.sensitive_request_encrypted` / `sensitive_response_encrypted`  
- **不**改 `alembic_version` 列宽；**不**建 PG ENUM；**不**向 `interview_rounds` 加 AI 列  

## 7. 输出契约与校验（摘要）

问题：`dimension_key/question/purpose/evidence_source/resume_evidence?/follow_up_prompts/risk_flags/display_order`。  
分析：`dimension_key/score?/evidence[]/analysis/strengths/risks/insufficient_information?/suggested_follow_ups` + `overall_summary`；禁止 hire/reject/offer。  
证据：归属 Cn、`is_included_in_analysis`、解密非空、规范化连续子串。  
正式分析启动：每维恰好 5 个非空 anchors；权重合计 100±0.01。  
overall：全维有分则 `round(Σ score_i * weight_i/100, 2)` ∈\[1,5\]；任维 null 则 overall null。

## 8. RBAC / 幂等 / API / 前端

同修正前矩阵：manage 全开；execute 仅已分配轮次；未分配 404。  
`InterviewIdempotencyKey` 异 hash → 409；不改简历幂等。  
API 草案路径保持；详情 `Cache-Control: no-store`；列表无密文。  
前端时间轴入口；AI 失败不挡开面；无录用按钮。

## 9. 长转写与 Dify 边界

超阈值拒绝启动（不截断）。Mock/契约优先；真实联调不在本批验收。Dify 只见内存组装的正文，库内 JSONB 不见正文。

## 10. 验收映射

013 结构/安全/约束 → `test_migration_013.py` + `test_migration_013_pg.py`；业务门禁/证据/RBAC/STALE/前端 → 后续任务。

## 11. 明确禁止

禁止多轮分析、招聘决策、Offer、自动改状态、明文敏感 JSONB、用 name 当维度唯一键、重试重读当前指针。
