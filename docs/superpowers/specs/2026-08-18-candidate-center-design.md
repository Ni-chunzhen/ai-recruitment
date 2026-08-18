# 候选人中心设计规格

基线：`main` @ `0ef4d3f`（面试安排落库修复）。本规格只定义只读聚合模块，不写业务实现、不新增表、不复制数据、不迁移。

## 1. 目标与边界

### 1.1 目标

新增模块 **候选人中心**：以 `Candidate` 为跨岗位浏览主体，列表每一行的资源是 `JobApplication`，避免同一人多岗位投递被合并成一行。

首批访问权限仅 `recruitment.manage`。不把本模块开放给 `interview.execute`。

### 1.2 非目标（首批明确不做）

- 不新增业务表、不复制任何现有行、不做 Alembic 迁移、不改 `.gitignore`。
- 不改变 `JobApplication.status` / `pipeline_status` / `InterviewRound.status` 或任何招聘状态。
- 不做自动录用、淘汰、筛选决策、Offer、多轮综合分析。
- 不接真实 Dify，不发送系统邮件或通知。
- 不扩展 `interview.execute` 的对象可见范围。
- 阶段 7 主路径代码已打通，但部分人工闭环可能尚未实际走完。候选人中心只展示库中真实状态；缺失就是缺失，禁止补写或伪造 `COMPLETED` / `READY` / `CONFIRMED` / `RECORDED_SENT`。

### 1.3 资源模型

| 层级 | 资源 | 作用 |
|---|---|---|
| 浏览主体 | `Candidate` | 跨岗位身份；详情页按人聚合「其他应聘摘要」 |
| 列表行 / 详情主资源 | `JobApplication` | 一行一条应聘记录；面试、评分、简历绑定都挂在这一行 |
| 跳转 | 既有岗位 / 时间轴 / 简历校对 / 评分报告 / 邀约·题纲·分析抽屉 | 不在本模块复制写路径 |

同一 `candidate_id` 可对应多条 `JobApplication`（转岗会新建一行；`(candidate_id, job_id)` **无**唯一约束）。列表禁止按人折叠。

## 2. 源码审计（字段与关系以 ORM/迁移为准）

### 2.1 Candidate / JobApplication / Job / JobVersion

`Candidate`（`candidates`）：`id` PK；`name` NOT NULL（`ix_candidates_name`）；`phone` / `email` 可空；无状态列。

`JobApplication`（`job_applications`）：

| 列 | 事实 |
|---|---|
| `id` | PK |
| `candidate_id` | FK `candidates.id` CASCADE，**无**独立索引 |
| `job_id` | FK `jobs.id` CASCADE；`ix_job_applications_job_id` |
| `job_version_id` | FK `job_versions.id` RESTRICT；绑定当时岗位版本 |
| `status` | `in_progress` / `rejected` / `transferred` / `terminated` / `hired`；默认 `in_progress`；`IN_FLIGHT_STATUSES = {in_progress}` |
| `pipeline_status` | `pending_parse` / `pending_hr_screen` / `interviewing` / `rejected` / `talent_pool`；`ix_job_applications_pipeline_status` |
| `resume_version_id` | FK `resume_versions.id` SET NULL；本应聘绑定的简历版本 |
| `interview_started` | bool |
| `interview_task_state` | `none/active/pending_cancel/cancelled/pending_rebuild/rebuilt`；关闭/迁移时的占位字段，**不是**面试官分配真源 |
| `lock_version` | 乐观锁 |
| `close_action` / `close_reason` / `transferred_to_job_id` | 关闭处置 |
| `previous_version_id` / `migration_reason` / `migrated_at` / `migrated_by` / `timeline_events` | 岗位版本迁移 |

现有索引：`ix_job_applications_job_id`、`ix_job_applications_job_version_id`、`ix_job_applications_status`、`ix_job_applications_job_status(job_id, status)`、`ix_job_applications_pipeline_status`。无 `candidate_id`、无 `created_at`/`updated_at` 索引。

`Job.status`：`draft/open/paused/closed`。`JobVersion.status`：`draft/published/superseded`。`Job.current_version_id` / `draft_version_id` 为应用层指针，库表无循环 FK。

### 2.2 Resume / ResumeVersion / 阶段 6 评分

`Resume`：`candidate_id` FK CASCADE（`ix_resumes_candidate_id`）；`current_file_version_id` / `current_confirmed_version_id` 应用层指针；`is_void`。

`ResumeVersion.status`：`pending_parse/parsing/pending_review/confirmed/parse_failed/void`。`kind`：`file` / `confirmed`。敏感列：`extracted_text`、`standardized_text`、`ai_structured`、`draft_content`、`confirmed_content`、`storage_key`。

候选人中心绑定源是 **`JobApplication.resume_version_id`**，不是「该人当前确认简历指针」。禁止用 `Resume.current_confirmed_version_id` 顶替本应聘绑定版本。

`AiResult`：`result_type`（简历评分为 `RESUME_SCORE`）；`application_id` / `candidate_id` / `job_version_id` / `resume_version_id` 均可空；当前行由部分唯一索引 `uq_ai_results_current(application_id, result_type) WHERE is_current AND application_id IS NOT NULL` 约束。评分报告由 `get_score_report` 读 **当前** `AiResult.normalized_result` 映射为 `ScoreReportOut`：总分、`recommendation`、`score_band`、`summary`、维度分、`risks`、`must_have_check`、`is_stale`。**不**返回 `raw_output`。维度项含 `evidence/gap/risk` 文本，属于报告正文，只允许走既有评分报告页，不进候选人中心列表。

### 2.3 面试轮次 / 安排 / 面试官

`InterviewRound` FK `application_id` → `job_applications.id` CASCADE（`ix_interview_rounds_application_id`）；`job_version_id` RESTRICT；`uq_interview_rounds_application_sequence(application_id, sequence_no)`；`sequence_no > 0`。

轮次状态（大写字符串，非 PG ENUM）：

`DRAFT` → `SCHEDULED` → `CONFIRMED` → `IN_PROGRESS` → `PENDING_TRANSCRIPT` → `COMPLETED`

可取消：`DRAFT/SCHEDULED/CONFIRMED/IN_PROGRESS --cancel--> CANCELLED`。异常结束：仅 `IN_PROGRESS --end_abnormally--> ENDED_ABNORMALLY`。终态集合 `TERMINAL_ROUND_STATUSES = {COMPLETED, CANCELLED, ENDED_ABNORMALLY}`。

`cancel_interview_round`：把 ACTIVE 安排改为 `CANCELLED`，轮次改为 `CANCELLED`，**不删除** `InterviewRoundInterviewer` 行。`InterviewRoundCreate.interviewers` 要求 `min_length=1`。时间轴 `list_rounds_for_application` 返回该应聘全部轮次（含取消/异常结束），`total_round_count` 计入它们。冲突检测只看 `InterviewSchedule.status = ACTIVE`，与「是否已分配面试官」无关。

`InterviewRoundInterviewer`：`(interview_round_id, interviewer_id)` 唯一；`ix_interview_round_interviewers_round_id`。这是「实际已分配面试官」的真源。

`InterviewSchedule.status`：`ACTIVE` / `SUPERSEDED` / `CANCELLED`。摘要 `InterviewScheduleSummaryOut` 只给 `has_meeting_password`，不返回 `meeting_password_encrypted`；联系电话为 `contact_phone_masked`。

`JobApplication.interview_task_state` 不由 `cancel_interview_round` 更新，禁止用作本模块筛选条件。

### 2.4 邀约 / 转写 / 题纲 / 单轮分析

| 对象 | 关联 | 真实状态 | 列表可暴露 | 详情正文入口 |
|---|---|---|---|---|
| `InterviewInvitationMessage` | `interview_round_id` CASCADE；`schedule_id` RESTRICT | `DRAFT/READY/RECORDED_SENT/VOIDED` | `InvitationMessageSummaryOut`（含脱敏邮箱，无正文） | `GET /interview-invitations/{id}`，`Cache-Control: no-store` |
| 人工确认 | `InterviewRound.invitation_confirmed_at/by/schedule_version/summary` | 有确认时间即已确认 | 确认时间/是否确认 | 时间轴已有 |
| `InterviewTranscript` | 每轮唯一 | 指针：`original/current_draft/current_confirmed_version_id` | `TranscriptListOut` 无正文 | `GET /transcript-versions/{id}` no-store |
| 无转写完成 | `transcript_completion_mode = WITHOUT_TRANSCRIPT \| CONFIRMED_TRANSCRIPT` | 真实落库才算 | 模式与原因码 | 不伪造确认转写 |
| `InterviewQuestionSet` | 每轮唯一 | `DRAFT/READY/ARCHIVED`；READY 必须有 current+确认人时 | `InterviewQuestionSetOut` 无题干 | 版本详情 no-store |
| `InterviewRoundAnalysis` | 每轮唯一 | 无持久 STALE 列；`current_version_id` 可空 | `InterviewAnalysisSetOut`：`overall_score`、计数、`is_stale` | 版本详情含正文/证据 quote，no-store |

分析 STALE 动态规则（源码 `_is_stale`）：无转写或无 `current_confirmed_version_id` → stale；否则 `analysis_version.transcript_version_id != transcript.current_confirmed_version_id`。

敏感密文列（候选人中心任何接口都不得返回）：邀约 `subject/body_*_encrypted`；转写 `raw_text_encrypted` / `text_encrypted`；题纲 `question/purpose/resume_evidence/follow_up_prompts/risk_flags_encrypted`；分析 `overall_summary/analysis/strengths/risks/insufficient_information/suggested_follow_ups/quote_encrypted`；安排 `meeting_password_encrypted`；attempt `sensitive_*_encrypted`；`AiResult.raw_output`；`AITask.raw_request/raw_response/result_payload`。

### 2.5 岗位候选人列表与现有惯例

| 惯例 | 事实 |
|---|---|
| API | `GET /jobs/{job_id}/candidates`；`require_permission("recruitment.manage")` |
| 筛选 | 仅 `in_flight_only`（`status IN in_progress`）；无分页、无关键词 |
| 排序 | `JobApplication.created_at DESC` |
| 404 | 岗位不存在、或 `job_id+application_id` 不匹配 → `detail="not found"`，不泄露是否存在 |
| 列表字段 | `JobApplicationOut`：姓名、电话、邮箱、`status`、`pipeline_status`、绑定版本、面试是否开始；无简历正文 |
| 前端 | `JobDetailView`「候选人」页签整表加载；操作跳转简历校对、匹配报告；JD 抽屉不是候选人详情 |
| 分页样板 | 岗位列表 `page` 默认 1、`page_size` 默认 20、`ge=1, le=100`，响应 `{items,total,page,page_size}` |
| 简历列表 | `offset/limit`，关键词打 `Candidate.name/phone/email` 与文件名 |
| 对象级对 execute | 未分配轮次一律 404 `not found`（本模块不采用 execute 路径） |
| 菜单 | `AdminLayout`：`recruitment.manage` 可见「岗位管理」「简历库」 |

既有应聘详情 `GET /applications/{application_id}` 返回 `ApplicationOut`（流程状态，无面试聚合），不能替代候选人中心详情。

## 3. 查询与列表

### 3.1 默认筛选：已分配面试轮次 = 是

默认只返回满足以下 **EXISTS** 的 `JobApplication`（禁止因多名面试官 JOIN 产生重复行）：

该应聘至少存在一条 `InterviewRound`，且该轮次至少存在一条 `InterviewRoundInterviewer`。

SQL 语义：

```sql
EXISTS (
  SELECT 1
  FROM interview_rounds r
  WHERE r.application_id = job_applications.id
    AND EXISTS (
      SELECT 1
      FROM interview_round_interviewers iri
      WHERE iri.interview_round_id = r.id
    )
)
```

空面试官集合的轮次（若历史脏数据存在）不计入。创建路径虽要求至少一名面试官，筛选仍以分配表行为准，不以 `interview_started` 或 `interview_task_state` 为准。

可切换为全部应聘记录：查询参数 `assigned`，类型 bool，**默认 `true`**；`assigned=false` 去掉 EXISTS，返回所有 `JobApplication`。

### 3.2 取消或失效轮次是否计入（锁定，非可选项）

**计入。** `CANCELLED` 与 `ENDED_ABNORMALLY` 只要仍有 `InterviewRoundInterviewer` 行，就满足「已分配面试轮次」。

锁定依据：

1. 取消/异常结束只改轮次状态，并把 ACTIVE 安排标为 `CANCELLED`，不删除分配行。
2. 时间轴已把这两种终态计入 `total_round_count`。
3. 「已分配」的真源是分配表，不是 `schedule.status=ACTIVE`，也不是非终态轮次。
4. `COMPLETED` 是终态但必须计入；因此不能用 `TERMINAL_ROUND_STATUSES` 做排除。取消与异常结束与完成一样保留历史分配。

无面试官的 `DRAFT` 空壳轮次不计入。仅有已取消安排、但轮次仍带面试官的记录计入。

### 3.3 其余筛选 / 关键词 / 排序 / 分页

只允许现有列与现有索引能支撑的白名单。未知参数 400。

| 参数 | 默认 | 白名单 | 支撑 |
|---|---|---|---|
| `assigned` | `true` | `true/false` | EXISTS + `ix_interview_rounds_application_id` + `ix_interview_round_interviewers_round_id` |
| `status` | 空 | `in_progress/rejected/transferred/terminated/hired` | `ix_job_applications_status` |
| `pipeline_status` | 空 | `pending_parse/pending_hr_screen/interviewing/rejected/talent_pool` | `ix_job_applications_pipeline_status` |
| `job_id` | 空 | UUID | `ix_job_applications_job_id` |
| `keyword` | 空 | 非空则 `ilike` | `Candidate.name`（`ix_candidates_name`）、`Candidate.phone`、`Candidate.email`、`Job.code`、`Job.name`；**禁止**搜简历正文、转写、题干、分析、JD 原文 |
| `page` | 1 | `ge=1` | 岗位列表惯例 |
| `page_size` | 20 | `ge=1, le=100` | 同上 |
| `sort` | `updated_at_desc` | `updated_at_desc` / `created_at_desc` | `JobApplication.updated_at` / `created_at`（无独立索引，见 §5.4） |

不提供轮次状态、邀约状态、评分区间等无现成列表索引的筛选。

### 3.4 列表行摘要

一行 = 一条 `JobApplication`。至少包含：

- 候选人：`candidate_id`、`name`；电话/邮箱沿用岗位候选人列表惯例（非密文）。
- 岗位：`job_id`、`job_name`、`job_code`、`job_version_id`、版本标签。
- 应聘状态：`status` 与 `pipeline_status` 分列，不合成假状态。
- 最新相关轮次：本应聘 `sequence_no` 最大的 `InterviewRound`（含取消/异常结束）；字段：`round_id/name/sequence_no/status`。无轮次则全空。
- 该最新轮次的安排状态：无 `current_schedule_id` → `none`；否则即该安排的 `ACTIVE/SUPERSEDED/CANCELLED`。不返回会议密码、明文电话、`meeting_url` 以外的密文。列表甚至不返回 `meeting_url`（时间轴摘要才有 URL）。
- 邀约状态（只看最新轮次，真实落库，不推断「该发未发」）：
  1. `invitation_confirmed_at IS NOT NULL` → `confirmed`
  2. 否则存在 `RECORDED_SENT` 消息 → `recorded_sent`
  3. 否则存在 `READY` → `ready`
  4. 否则存在 `DRAFT` → `draft`
  5. 否则存在消息且全部 `VOIDED` → `voided`
  6. 否则 → `none`
- 转写状态（只看最新轮次）：
  1. `transcript_completion_mode = WITHOUT_TRANSCRIPT` → `without_transcript`
  2. 否则 `current_confirmed_version_id` 非空 → `confirmed`
  3. 否则 `current_draft_version_id` 非空 → `draft`
  4. 否则 `original_version_id` 非空 → `original`
  5. 否则 → `none`
- 题纲状态：无 `InterviewQuestionSet` → `none`；否则即 set 的 `DRAFT/READY/ARCHIVED`。
- 单轮分析状态：无 analysis 或 `current_version_id` 空 → `none`；有当前版本且 `_is_stale` → `stale`；有当前版本且非 stale → `ready`。列表可带 `overall_score`（数值），禁止 `overall_summary` 与证据。

禁止返回：简历正文、`extracted_text`/`standardized_text`/`confirmed_content`/`ai_structured`、转写正文、题干、分析正文、证据 quote、任何 `*_encrypted`、`raw_output` / `raw_request` / `raw_response` / `result_payload`、会议密码。

列表响应形状对齐岗位列表：`{items, total, page, page_size}`。列表不必 `no-store`（与邀约列表一致）；不得把详情正文塞进列表。

## 4. 详情

### 4.1 定位与 404

路径同时校验 `candidate_id` + `application_id`：

`JobApplication.id = application_id AND JobApplication.candidate_id = candidate_id`

组合不存在或不匹配 → **404** `detail="not found"`（与岗位/面试对象级 404 同一句，不区分「没这个人」和「人与应聘不匹配」）。

仅 `recruitment.manage`；无权限 403（路由依赖，与岗位 API 相同）。

### 4.2 当前应聘展示范围

只加载 **这一条** `JobApplication` 及其 `application_id` 下的数据。

1. **简历摘要**：若 `resume_version_id` 空则标明未绑定。否则只给 `ResumeListItem` 级元数据：`resume_id`、`resume_version_id`、`version_label`、`kind`、`status`、`original_filename`、`confirmed_at`。跳转既有 `/resumes/{versionId}/review`。不返回抽取正文、标准化正文、draft/confirmed JSON、storage_key、preview_url。
2. **AI 评分摘要**：只查 `AiResult.application_id = 当前应聘` 且 `result_type=RESUME_SCORE` 且 `is_current`。无结果就显示无评分。摘要字段：`result_id`、`version_label`、`total_score`、`calculated_total_score`、`score_band`、`recommendation`、`summary`、`information_insufficient`、`is_stale`、`is_current`、各维 `name/weight/score`。不返回 `raw_output`、维度 `evidence/gap/risk`。完整报告跳转既有 `/applications/{id}/score-report`。
3. **应聘状态**：`status`、`pipeline_status`、`close_action`、`interview_started`；不改写。
4. **本应聘全部面试轮次**（按 `sequence_no` 升序，含取消/异常结束）：轮次元数据、当前安排摘要（密码只给 `has_meeting_password`）、上述邀约/转写/题纲/分析**状态**。
5. 正文按需加载，复用既有入口，不在本模块复制写 API：
   - 时间轴：`/applications/{applicationId}/interviews`
   - 邀约抽屉：`InterviewInvitationDrawer` ← `GET /interview-rounds/{id}/invitations` 与详情 no-store
   - 转写页：`/interview-rounds/{roundId}/transcript`
   - 题纲抽屉：`InterviewQuestionSetDrawer`
   - 分析抽屉：`InterviewAnalysisDrawer`

详情响应必须 `Cache-Control: no-store`。

### 4.3 其他岗位应聘历史

同 `candidate_id`、不同 `application_id` 的其他 `JobApplication` 只给摘要：`application_id`、`job_id`、`job_name`、`job_code`、`status`、`pipeline_status`、`created_at`，以及跳转到对应候选人中心详情。

**禁止**把其他 `JobApplication` 的轮次、安排、邀约、转写、题纲、单轮分析、`AiResult`、`resume_version_id` 混进当前详情。转岗后的新行是另一条应聘，旧行面试不跟随。

## 5. 技术切分与验收

### 5.1 拟新增（只读）职责

| 层 | 职责 | 不负责 |
|---|---|---|
| repository 聚合查询 | EXISTS 筛选、白名单过滤、分页计数、按 `application_id` 批量取最新轮次与状态，避免 N+1 和面试官重复行 | 写库、迁移 |
| service | 拼列表/详情 DTO、跨应聘隔离、状态派生、脱敏裁剪 | 改招聘状态、触发 AI、发信 |
| API | `GET /candidate-center/applications`；`GET /candidate-center/candidates/{candidate_id}/applications/{application_id}`；`require_permission("recruitment.manage")`；详情 `no-store`；404/400 | `interview.execute` 分支 |
| 前端菜单 | `AdminLayout` 在「简历库」旁增加「候选人中心」，仅 `recruitment.manage` | 面试官菜单 |
| 前端列表 | 默认 `assigned=true`；可切全部；分页/筛选/关键词对齐岗位列表 | 行内写操作 |
| 前端详情 | 当前应聘只读聚合 + 其他应聘摘要跳转；打开既有抽屉/路由 | 新写路径、伪造完成态 |

稳定符号（后续实现不得改名）：

- `assigned_interview_exists()`：§3.1 EXISTS
- `list_candidate_center_applications`
- `get_candidate_center_application_detail`

### 5.2 RBAC / 404 / 白名单

- 列表与详情：仅 `recruitment.manage`。`interview.execute` 即使已分配面试官也 **403**（本模块不开放）。
- 对象 404：应聘不存在、`candidate_id` 不匹配，同一 `not found`。
- 筛选值不在白名单 → 400。
- 不在候选人中心做 execute 的「未分配伪装 404」。

### 5.3 分页

沿用岗位列表：`page` + `page_size`（1–100），响应带 `total`。不用简历库的 `offset/limit`，也不用岗位候选人页签那种一次拉全量。

### 5.4 性能与索引风险（不做迁移，只记录）

- EXISTS 可走 `ix_interview_rounds_application_id` 与 `ix_interview_round_interviewers_round_id`。
- `status` / `pipeline_status` / `job_id` 有索引。
- **无** `ix_job_applications_candidate_id`：详情「其他应聘」按人查可能顺序扫。
- **无** `updated_at`/`created_at` 索引：默认排序可能 filesort。
- 禁止为消风险而加迁移。实现必须用子查询/窗口函数一次取「每应聘最大 `sequence_no`」，禁止对列表页逐行查轮次。

### 5.5 TDD 分批顺序与验收映射

不要在本文件写逐步实施计划。实现按下列批次，先红后绿。

| 批次 | 锁定内容 | 验收映射 |
|---|---|---|
| 1 查询 | EXISTS 默认筛选；取消/异常结束计入；多面试官不重复行；`assigned=false` 出全部 | repository 单测 |
| 2 列表 API | 白名单、分页、关键词只打允许列、RBAC、列表无正文/密文/raw | API 单测 |
| 3 详情 | `candidate_id+application_id` 404；评分/面试只取当前 `application_id`；其他应聘只有摘要；`no-store` | API + service 单测 |
| 4 前端列表 | 菜单权限、默认已分配、切换全部、分页 | vitest |
| 5 前端详情 | 复用时间轴/邀约/题纲/分析/转写/评分入口；不混岗位数据 | vitest |

回归：`backend pytest -q`、`frontend pnpm vitest run`、`frontend pnpm type-check`。

## 6. 可复用 vs 首批不做

### 6.1 可复用的现有能力

- RBAC：`require_permission("recruitment.manage")`、路由 `meta.permission`。
- 对象 404 句式与 `get_application_by_id(..., job_id=)` 的归属校验模式。
- 岗位列表分页与关键词形态。
- 岗位候选人 `JobApplicationOut` 的联系方式惯例。
- 时间轴 `GET /applications/{id}/interview-rounds`、安排摘要脱敏。
- 邀约列表摘要 vs 详情 no-store。
- 转写 `TranscriptListOut` vs 版本详情。
- 题纲/分析 set 摘要 vs 版本详情抽屉。
- 简历列表项元数据、`GET /applications/{id}/resume-score-report`（不含 `raw_output`）。
- 前端 `InterviewInvitationDrawer`、`InterviewQuestionSetDrawer`、`InterviewAnalysisDrawer`、转写路由、评分报告页、简历校对页。

### 6.2 首批不做

跨岗位写操作、关闭/转岗/迁移版本、上传简历、创建轮次、排期、邀约生成/确认/记发送、转写校对、出题、单轮分析、筛选决策、Offer、多轮综合分析、真实 Dify、系统发信、通知、`interview.execute` 入口、新表与迁移、按人合并列表行、用 `interview_task_state` 当分配筛选、把其他应聘的面试或 AI 结果拼进当前详情、列表返回任何正文/密文/raw、把未发生的人工步骤标成已完成。

## 7. 自检

- 无 TODO/TBD。
- 列表行 = `JobApplication`，不按人去重，不复制数据。
- 默认 EXISTS 筛选、取消/异常结束计入、跨岗位隔离、敏感数据边界均已锁定。
- 状态只读真实库值；阶段 7 未走完的人工闭环保持未完成。
