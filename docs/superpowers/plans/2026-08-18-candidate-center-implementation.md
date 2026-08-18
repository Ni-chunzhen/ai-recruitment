# 候选人中心实施计划

**Goal：** 按已批准规格实现只读「候选人中心」：列表行是 `JobApplication`，默认 EXISTS 筛选已分配面试轮次，详情按 `candidate_id + application_id` 归属校验，复用既有评分报告页与面试时间轴页，不改招聘状态。

**Architecture：** 新建只读聚合层（repository 一次查出列表/展示轮次 → service 派生状态并裁剪敏感字段 → API 仅 `recruitment.manage`）。前端新增菜单、列表、详情；正文跳转既有路由，不复制写 API、不内嵌邀约/题纲/分析抽屉。

**Tech Stack：** FastAPI、SQLAlchemy 2.0 async、Pydantic、pytest、Vue 3、Vue Router、Pinia、Vitest、Element Plus。无 Alembic、无新表、无 aiosqlite 新依赖；repository 单测用 PostgreSQL 方言 `compile` 锁定 EXISTS/展示轮次 SQL 形状。

**Spec：** `docs/superpowers/specs/2026-08-18-candidate-center-design.md`

## 全局约束

- 只读聚合；禁止写 `JobApplication` / `InterviewRound` / 评分 / 邀约 / 转写 / 题纲 / 分析。
- 无迁移、无新表、不改 `.gitignore`、不 push。
- 仅 `recruitment.manage`；`interview.execute` 访问本模块 403。
- `assigned` 默认 `true`；`CANCELLED` 与 `ENDED_ABNORMALLY` 只要仍有 `InterviewRoundInterviewer` 行即计入。
- 列表无正文、密文、raw；详情 `Cache-Control: no-store`。
- `candidate_id` 与 `application_id` 不匹配 → 404 `detail="not found"`。
- 禁止把其他 `JobApplication` 的面试或 `AiResult` 混进当前详情。
- 稳定符号不得改名：`assigned_interview_exists`、`list_candidate_center_applications`、`get_candidate_center_application_detail`。

## 稳定接口名

| 符号 | 锁定值 |
|---|---|
| 列表 API | `GET /api/v1/candidate-center/applications` |
| 详情 API | `GET /api/v1/candidate-center/candidates/{candidate_id}/applications/{application_id}` |
| 列表函数 | `list_candidate_center_applications` |
| 详情函数 | `get_candidate_center_application_detail` |
| EXISTS 函数 | `assigned_interview_exists()` |
| 列表 DTO | `CandidateCenterListItem`、`CandidateCenterListResponse` |
| 详情 DTO | `CandidateCenterDetailOut` |
| 查询模型 | `CandidateCenterListQuery`（`extra="forbid"`） |
| 前端 client | `frontend/src/api/candidateCenter.ts`：`listCandidateCenterApplications`、`getCandidateCenterApplicationDetail` |
| 前端页面 | `CandidateCenterListView.vue`、`CandidateCenterDetailView.vue` |
| 前端路由 | `name: 'candidate-center'` → `/candidate-center`；`name: 'candidate-center-detail'` → `/candidate-center/candidates/:candidateId/applications/:applicationId` |
| 评分页（既有） | `/applications/:applicationId/score-report`；数据 `GET /api/v1/applications/{id}/resume-score-report` |
| 时间轴页（既有） | `/applications/:applicationId/interviews`；数据 `GET /api/v1/applications/{id}/interview-rounds` |

---

## 真实文件结构映射

### 将创建

| 文件 | 职责 |
|---|---|
| `backend/app/repositories/candidate_center.py` | EXISTS 筛选、展示轮次子查询、关键词/状态/分页 SQL；`assigned_interview_exists`、`list_candidate_center_application_rows`、`count_candidate_center_applications`、`get_candidate_center_application_row`、`list_other_applications_for_candidate` |
| `backend/app/schemas/candidate_center.py` | `CandidateCenterListQuery`、`CandidateCenterListItem`、`CandidateCenterListResponse`、`CandidateCenterDetailOut` 及简历/评分/轮次/其他应聘摘要子模型；`extra="forbid"` |
| `backend/app/services/candidate_center.py` | 状态派生、评分摘要裁剪、跨应聘隔离、敏感字段拒绝；`list_candidate_center_applications`、`get_candidate_center_application_detail` |
| `backend/app/api/v1/endpoints/candidate_center.py` | 两个 GET；`require_permission("recruitment.manage")`；详情 `Cache-Control: no-store`；404/400 映射 |
| `backend/tests/repositories/test_candidate_center.py` | compile EXISTS/展示轮次/去重/关键词/分页 SQL |
| `backend/tests/services/test_candidate_center.py` | 展示轮次语义、状态派生、评分裁剪、隔离、敏感键 |
| `backend/tests/api/v1/test_candidate_center.py` | RBAC、默认 `assigned=true`、400/404、no-store、列表无正文 |
| `frontend/src/api/candidateCenter.ts` | 类型与两个 GET client |
| `frontend/src/views/CandidateCenterListView.vue` | 默认已分配、切换全部、筛选、分页、状态列 |
| `frontend/src/views/CandidateCenterDetailView.vue` | 当前应聘聚合、其他应聘跳转、既有页面链接 |
| `frontend/tests/CandidateCenterListView.spec.ts` | 菜单、默认 assigned、分页、权限 |
| `frontend/tests/CandidateCenterDetailView.spec.ts` | 隔离、跳转路由名、无写按钮 |

### 将修改

| 文件 | 职责 |
|---|---|
| `backend/app/api/v1/router.py` | `include_router(candidate_center.router)` |
| `frontend/src/router/index.ts` | 注册两条 `recruitment.manage` 路由 |
| `frontend/src/layouts/AdminLayout.vue` | `recruitment.manage` 下在「简历库」后增加「候选人中心」→ `/candidate-center` |

### 只读复用（本检查点实现不得改这些文件）

| 文件 | 复用点 |
|---|---|
| `backend/app/models/candidate.py`、`interview.py`、`invitation.py`、`interview_transcript.py`、`interview_ai.py`、`resume.py`、`job.py` | 既有 ORM/状态常量 |
| `backend/app/repositories/candidates.py` | `CandidateNotFoundError`、`get_application_by_id` 归属模式 |
| `backend/app/repositories/jobs.py` | `page`/`page_size` offset-limit、keyword `ilike` |
| `backend/app/repositories/resumes.py` | `get_current_ai_result(application_id, result_type)` |
| `backend/app/repositories/interviews.py` | `list_rounds_for_application`（详情全部轮次） |
| `backend/app/api/v1/endpoints/jobs.py` | `Query(default=1/20, ge=1, le=100)`、`require_permission("recruitment.manage")` |
| `backend/app/api/v1/endpoints/candidates.py` | 404 `detail="not found"` |
| `backend/app/api/v1/endpoints/interview_ai.py` | `_NO_STORE = "no-store"` |
| `backend/app/api/dependencies/auth.py` | `require_permission` → 403 `forbidden` |
| `backend/app/schemas/job.py` | `JobListResponse` 形状 `{items,total,page,page_size}` |
| `backend/app/schemas/interview_ai_api.py` | `extra="forbid"` 查询白名单写法 |
| `frontend/src/api/client.ts` | `baseURL: '/api/v1'` |
| `frontend/src/api/resumes.ts` | `pipelineStatusLabel`；`getScoreReport` |
| `frontend/src/api/interviews.ts` | `getInterviewTimeline` |
| `frontend/src/views/JobsListView.vue` | 筛选 reactive + `el-pagination` |
| `frontend/src/views/JobDetailView.vue` | 候选人表状态列、跳转 `score-report` |
| `frontend/src/views/ScoreReportView.vue`、`InterviewTimelineView.vue` | 既有详情入口，候选人中心只 `router.push` |
| `frontend/src/components/InterviewInvitationDrawer.vue`、`interviews/InterviewQuestionSetDrawer.vue`、`interviews/InterviewAnalysisDrawer.vue` | 仍由时间轴页按需打开，不拷贝进候选人中心 |
| `frontend/src/stores/auth.ts` | `hasPermission` |
| `backend/tests/api/v1/test_interviews.py`、`test_invitations.py`、`test_interview_ai.py` | `_user` / `_client_for` / `lifespan_patches` / `cache-control` / `detail=="not found"` |
| `backend/tests/services/test_candidates.py`、`test_resume_scoring.py`、`test_interviews.py`、`test_invitations.py`、`test_interview_transcripts.py`、`test_interview_questions.py`、`test_interview_analyses.py` | 回归，不改 fixture |
| `frontend/tests/AiTasksView.spec.ts` | 菜单权限与 `page_size: 20` 列表 mock |
| `frontend/tests/InterviewTimelineView.spec.ts`、`ScoreReportView.spec.ts` | 既有入口回归 |

禁止创建或修改：`backend/alembic/**`、任何 `models/*.py`、既有写路径 endpoint/service。

---

## Task 1 — 后端 repository 查询

**Consumes：** 规格 §3.1–§3.4、§5.1、§5.4。
**Produces：** `backend/app/repositories/candidate_center.py`、`backend/tests/repositories/test_candidate_center.py`
**Commit：** `feat(candidate-center): query assigned applications`

### RED

新建 `backend/tests/repositories/test_candidate_center.py`。用 `sqlalchemy.dialects.postgresql.dialect()` compile `list`/`count`/`display-round` 语句（不连库、不加 aiosqlite）。

| 测试名 | 关键断言 |
|---|---|
| `test_assigned_interview_exists_sql_is_nested_exists` | `assigned=true` 的 WHERE 含两层 `EXISTS`：外层 `interview_rounds.application_id`，内层 `interview_round_interviewers.interview_round_id`；外层 FROM 不含 `JOIN interview_round_interviewers` |
| `test_assigned_sql_does_not_exclude_cancelled_or_abnormal` | compile SQL 不含 `CANCELLED` / `ENDED_ABNORMALLY` 排除谓词；不含 `interview_task_state` |
| `test_assigned_false_omits_exists_filter` | `assigned=false` 的 list SQL 无 `interview_round_interviewers` 的 EXISTS 过滤 |
| `test_display_round_assigned_true_requires_interviewer` | `assigned=true` 展示轮次子查询：`max(sequence_no)` 且含 `InterviewRoundInterviewer` EXISTS |
| `test_display_round_assigned_false_is_max_sequence_any_round` | `assigned=false` 展示轮次子查询：`max(sequence_no)`，子查询内无面试官 EXISTS |
| `test_keyword_sql_only_hits_allowed_columns` | keyword SQL 含 `candidates.name`、`candidates.phone`、`candidates.email`、`jobs.code`、`jobs.name` 的 `ILIKE`；不含 `extracted_text`、`standardized_text`、`raw_jd_text`、`question`、`quote` |
| `test_status_and_pipeline_filters_are_equality` | `status` / `pipeline_status` / `job_id` 为等值谓词 |
| `test_sort_whitelist_updated_at_and_created_at_desc` | `updated_at_desc` → `job_applications.updated_at DESC`；`created_at_desc` → `created_at DESC` |
| `test_pagination_uses_offset_limit` | `page=2, page_size=20` → `OFFSET 20` 且 `LIMIT 20`；count 语句无 OFFSET |
| `test_list_selects_application_id_once` | SELECT 主键为 `job_applications.id`，无对面试官表的非 EXISTS JOIN |

运行（必须失败：模块不存在）：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/repositories/test_candidate_center.py -q
```

### GREEN

实现 `assigned_interview_exists()` 返回规格 §3.1 的 `exists()` 子句。
`list_candidate_center_application_rows(..., assigned, status, pipeline_status, job_id, keyword, sort, page, page_size)` 一次 SELECT：应聘 + 候选人 + 岗位 + 展示轮次 id（按当前 `assigned` 语义的子查询/窗口函数关联），禁止 N+1。
`count_candidate_center_applications` 使用同一 WHERE。
非法 `sort` 由 service 在进 repository 前拒绝；repository 只接收已校验枚举。

再跑同上命令必须通过。然后提交 Task 1。

---

## Task 2 — 后端 service / schema

**Consumes：** Task 1；规格 §3.4、§4、§5.1。
**Produces：** `backend/app/schemas/candidate_center.py`、`backend/app/services/candidate_center.py`、`backend/tests/services/test_candidate_center.py`
**Commit：** `feat(candidate-center): map list and detail aggregates`

### RED

`backend/tests/services/test_candidate_center.py` 用 `SimpleNamespace` / `AsyncMock` 桩 repository 行（与 `tests/services/test_candidates.py` 相同手法）。

| 测试名 | 关键断言 |
|---|---|
| `test_list_item_has_split_status_fields` | 同时有 `status` 与 `pipeline_status`，不合成第三种应聘状态 |
| `test_list_display_round_follows_assigned_true` | 桩两轮：seq=1 有面试官且 `CANCELLED`，seq=2 无面试官；`assigned=true` 的 item.`round_id` 为 seq=1 |
| `test_list_display_round_follows_assigned_false` | 同上数据 `assigned=false` 的 item.`round_id` 为 seq=2 |
| `test_list_empty_display_round_statuses_are_none` | 无轮次时 `round_id is None`，安排/邀约/转写/题纲/分析均为 `"none"` |
| `test_invitation_status_priority` | `invitation_confirmed_at` 非空 → `"confirmed"`，即使消息全 `VOIDED`；否则按 `RECORDED_SENT > READY > DRAFT > 全 VOIDED → voided > none` |
| `test_transcript_status_priority` | `WITHOUT_TRANSCRIPT` → `"without_transcript"`，即使已有 confirmed 指针；否则 confirmed > draft > original > none |
| `test_question_status_is_set_status_or_none` | 无 set → `"none"`；有 set → `"DRAFT"` / `"READY"` / `"ARCHIVED"` 原样 |
| `test_analysis_status_ready_and_stale` | 无 current_version → `"none"`；current 且转写确认指针不一致 → `"stale"`；一致 → `"ready"`；可含 `overall_score`，JSON 无 `overall_summary` |
| `test_list_payload_strips_sensitive_keys` | `model_dump()` 键集不含 `extracted_text`、`standardized_text`、`question`、`quote`、`raw_output`、`meeting_password`、任何 `*_encrypted` |
| `test_score_summary_omits_evidence_and_raw` | 详情评分有 `total_score`/`score_band`/`summary`/维 `name,weight,score`；无 `raw_output`、无维 `evidence`/`gap`/`risk`；`get_current_ai_result` 的 `application_id` 等于当前应聘 |
| `test_detail_mismatch_raises_not_found` | `candidate_id` 与 application 不一致 → `CandidateNotFoundError` |
| `test_other_applications_are_summaries_only` | 其他应聘只有 `application_id/job_id/job_name/job_code/status/pipeline_status/created_at`；其 `round_id` / `result_id` 不出现在当前详情的面试或评分块 |
| `test_detail_rounds_stay_on_current_application` | 详情轮次列表每项 `application_id` 均为当前 id |
| `test_list_query_rejects_unknown_sort` | `sort="score_desc"` → 校验错误，不调用 repository |
| `test_does_not_use_interview_task_state_as_filter` | service 源码（`inspect.getsource`）不含把 `interview_task_state` 当作 assigned 条件 |

运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/services/test_candidate_center.py -q
```

必须因模块缺失失败。

### GREEN

Schema：`CandidateCenterListQuery` 字段仅 `assigned: bool = True`、`status`、`pipeline_status`、`job_id`、`keyword`、`page`、`page_size`、`sort`；`model_config extra="forbid"`。
`status` 仅 `APPLICATION_STATUSES`；`pipeline_status` 仅 `PIPELINE_STATUSES`；`sort` 仅 `updated_at_desc` / `created_at_desc`。
Service 派生函数名锁定：`derive_invitation_status`、`derive_transcript_status`、`derive_question_status`、`derive_analysis_status`、`build_score_summary`。分析 STALE 规则写在 `derive_analysis_status` 内（与规格 `_is_stale` 相同），不 import `interview_analyses._is_stale`。
评分只读 `get_current_ai_result(..., result_type=TASK_TYPE_RESUME_SCORE)`。
详情简历摘要只映射 `ResumeVersion` 元数据，不读 `extracted_text`。

再跑同上命令通过后提交 Task 2。

---

## Task 3 — 后端 API

**Consumes：** Task 2；规格 §5.2–§5.3；`endpoints/jobs.py`、`endpoints/candidates.py`、`endpoints/interview_ai.py`、`tests/api/v1/test_interviews.py`。
**Produces：** `backend/app/api/v1/endpoints/candidate_center.py`；修改 `backend/app/api/v1/router.py`；`backend/tests/api/v1/test_candidate_center.py`
**Commit：** `feat(candidate-center): expose read-only APIs`

### RED

API 测试复制 `test_interviews.py` 的 `_user`、`_client_for`、`lifespan_patches`（override `get_current_user` / `get_db_session`，patch `app.main.create_database_engine` 等）。

| 测试名 | 关键断言 |
|---|---|
| `test_list_requires_recruitment_manage` | 无权限或仅 `interview.execute` → 403，`detail=="forbidden"`；`list_candidate_center_applications` 未被调用 |
| `test_list_manage_ok_defaults_assigned_true` | `GET /api/v1/candidate-center/applications` 无 query 时，service 收到 `assigned is True`、`page==1`、`page_size==20`；200 且 body 有 `items/total/page/page_size` |
| `test_list_assigned_false_forwards_flag` | `?assigned=false` 时 service `assigned is False` |
| `test_list_rejects_unknown_query_param` | `?foo=1` → 400 |
| `test_list_rejects_invalid_status` | `?status=interviewing` → 400（那是 pipeline 值，不是 `JobApplication.status`） |
| `test_list_rejects_invalid_pipeline_status` | `?pipeline_status=in_progress` → 400 |
| `test_list_does_not_set_no_store` | 列表 200 的 `Cache-Control` 不是 `no-store` |
| `test_list_body_has_no_sensitive_keys` | 响应 JSON 字符串不含 `extracted_text`、`raw_output`、`question_encrypted`、`quote` |
| `test_detail_requires_recruitment_manage` | 仅 `interview.execute` → 403 |
| `test_detail_mismatch_is_404_not_found` | service 抛 `CandidateNotFoundError` → 404，`detail=="not found"` |
| `test_detail_sets_no_store` | 详情 200 → `response.headers.get("cache-control") == "no-store"`（与 `test_invitations.py` 相同断言） |
| `test_router_registers_two_get_routes` | `candidate_center.router` 路径含 `/applications` 与 `/candidates/{candidate_id}/applications/{application_id}`；`inspect.getsource` 中 `require_permission("recruitment.manage")` 出现次数 ≥ 2 |
| `test_router_included_in_api_v1` | `app.api.v1.router` 源码含 `candidate_center` |

运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/api/v1/test_candidate_center.py -q
```

必须失败。

### GREEN

`APIRouter(prefix="/candidate-center", tags=["candidate-center"])`。
列表用 `CandidateCenterListQuery` 作依赖（forbid extra）。
404 映射照抄 `endpoints/candidates.py`：`CandidateNotFoundError` → `HTTPException(404, detail="not found")`。
详情：`response.headers["Cache-Control"] = "no-store"`。
`router.py` 增加 `include_router(candidate_center.router)`，与 `candidates.router` 并列。

再跑 API 文件通过后提交 Task 3。

---

## Task 4 — 后端回归

**Consumes：** Task 1–3。
**Produces：** 无新文件。不提交空 commit。

运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/repositories/test_candidate_center.py tests/services/test_candidate_center.py tests/api/v1/test_candidate_center.py tests/services/test_candidates.py tests/services/test_resume_scoring.py tests/api/v1/test_interviews.py tests/api/v1/test_invitations.py tests/api/v1/test_interview_ai.py tests/api/v1/test_jobs_permissions.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

通过标准：候选中心三文件全绿；既有候选人/评分/面试/邀约/题纲分析 API 不红；全量与当前基线一致（允许既有 8 skip）。失败则停在本任务，不开始前端。

---

## Task 5 — 前端 API client

**Consumes：** Task 3 路径；`frontend/src/api/client.ts`、`frontend/src/api/jobs.ts`、`frontend/src/api/interviews.ts`。
**Produces：** `frontend/src/api/candidateCenter.ts`；`frontend/tests/candidateCenterApi.spec.ts`
**Commit：** `feat(candidate-center): add frontend client`

### RED

`frontend/tests/candidateCenterApi.spec.ts`：`vi.mock` axios 或 spy `apiClient.get`（与现有 api 模块同样从 `./client` 默认导出）。

| 测试名 | 关键断言 |
|---|---|
| `listCandidateCenterApplications defaults assigned true` | 无参调用时 `get` 的 URL 为 `/candidate-center/applications`，params 含 `assigned: true`、`page: 1`、`page_size: 20` |
| `listCandidateCenterApplications forwards assigned false` | params.`assigned === false` |
| `getCandidateCenterApplicationDetail hits nested path` | URL 为 `/candidate-center/candidates/${candidateId}/applications/${applicationId}` |
| `types omit sensitive fields` | TypeScript 接口 `CandidateCenterListItem` / `CandidateCenterDetail` 的键名集合测试：源文件文本不含 `extracted_text`、`raw_output`、`question_encrypted` |

运行：

```powershell
cd frontend
pnpm vitest run tests/candidateCenterApi.spec.ts
```

必须失败。

### GREEN

`listCandidateCenterApplications` / `getCandidateCenterApplicationDetail` 用既有 `apiClient`。类型字段与后端 DTO 对齐（camel 文件名、snake JSON 字段，与 `JobApplication` 接口相同）。
再跑该 vitest 通过后提交 Task 5。

---

## Task 6 — 前端菜单与列表

**Consumes：** Task 5；`AdminLayout.vue`、`JobsListView.vue`、`frontend/tests/AiTasksView.spec.ts`。
**Produces：** 修改 `frontend/src/layouts/AdminLayout.vue`、`frontend/src/router/index.ts`；新建 `frontend/src/views/CandidateCenterListView.vue`、`frontend/tests/CandidateCenterListView.spec.ts`
**Commit：** `feat(candidate-center): add list page and menu`

### RED

| 测试名 | 关键断言 |
|---|---|
| `shows candidate center nav for recruitment.manage` | 挂载 `AdminLayout`（仿 `AiTasksView.spec.ts` 的 `mountLayout`）权限含 `recruitment.manage` 时文本含「候选人中心」，链接 `to="/candidate-center"`；位于「简历库」之后 |
| `hides candidate center nav without manage` | 仅 `interview.execute` + `profile.read` 时无「候选人中心」 |
| `manage-only route rejects execute` | `authStore` 仅 `interview.execute` 时 `push('/candidate-center')` 后 `currentRoute.name === 'forbidden'`（同 `AiTasksView` `rejects non-admin from the admin route`） |
| `list loads with assigned true by default` | mock `listCandidateCenterApplications` 首次调用 args.`assigned === true` |
| `toggle all applications sends assigned false` | 点击 `data-test="assigned-filter-all"` 后再次调用 `assigned === false` |
| `renders display round and derived statuses` | 表格 `data-test="candidate-center-table"` 显示候选人名、岗位名、`status`、`pipeline_status`、轮次名、安排/邀约/转写/题纲/分析状态值 |
| `paginates with page_size 20` | mock 返回 `total: 21`；`el-pagination` 存在；不把 21 条一次当作无分页全量 |
| `row click goes to detail route` | `router.push` name 为 `candidate-center-detail`，params 含 `candidateId` 与 `applicationId` |
| `list has no write actions` | 文本不含「生成邀约」「记发送」「出题」「开始面试」「淘汰」 |

运行：

```powershell
cd frontend
pnpm vitest run tests/CandidateCenterListView.spec.ts
```

必须失败。

### GREEN

`AdminLayout` 的 `mainNavItems` 在简历库后 `push({ name: 'candidate-center', label: '候选人中心', path: '/candidate-center' })`。
Router：`meta: { requiresAuth: true, permission: 'recruitment.manage' }`。
列表 filter 初始 `{ assigned: true, keyword: '', status: '', pipeline_status: '', page: 1, page_size: 20 }`，`el-pagination` 对齐 `JobsListView.vue`。
`pipeline_status` 展示复用 `pipelineStatusLabel`（`frontend/src/api/resumes.ts`），不复制另一份文案表。
无行内写按钮。

再跑该 vitest 通过后提交 Task 6。

---

## Task 7 — 前端详情

**Consumes：** Task 6；规格 §2.6、§4.2–§4.3。
**Produces：** `frontend/src/views/CandidateCenterDetailView.vue`、`frontend/tests/CandidateCenterDetailView.spec.ts`
**Commit：** `feat(candidate-center): add detail page`

### RED

| 测试名 | 关键断言 |
|---|---|
| `loads detail by candidate and application ids` | 调用 `getCandidateCenterApplicationDetail(candidateId, applicationId)`，与路由 params 一致 |
| `renders resume metadata without body` | 有 `version_label` / `original_filename`；HTML 不含 `extracted_text` 内容样本；有跳转 `name: 'resume-review'` |
| `score summary links to score-report page` | 按钮/链接 `router.push({ name: 'score-report', params: { applicationId } })`；**不**请求 `/resume-score-report`（完整报告由既有页自己拉） |
| `timeline link uses application-interviews` | `router.push({ name: 'application-interviews', params: { applicationId } })` |
| `transcript link uses interview-transcript` | 对某轮 `name: 'interview-transcript', params: { roundId }` |
| `other applications navigate to candidate-center-detail` | 其他行 params 为**另一组** `candidateId`（同人）+ 其他 `applicationId`；点击后不会把其他行的 `round_id` 渲染进当前轮次列表 |
| `does not mount invitation or analysis drawers` | `wrapper.findComponent` 找不到 `InterviewInvitationDrawer` / `InterviewQuestionSetDrawer` / `InterviewAnalysisDrawer` |
| `has no generate or decision buttons` | 无 `data-test="generate-invitation"`、无「录用」「Offer」「筛选决策」 |
| `shows real missing states as missing` | 评分块在 `score_summary === null` 时显示无评分，不显示「已完成」 |

运行：

```powershell
cd frontend
pnpm vitest run tests/CandidateCenterDetailView.spec.ts
```

必须失败。

### GREEN

详情只读渲染当前应聘；其他应聘用摘要表 + `candidate-center-detail` 跳转。
打开既有页面，不复制抽屉。
再跑该 vitest 通过后提交 Task 7。

---

## Task 8 — 前端验证与全量回归

**Consumes：** Task 1–7。
**Produces：** 无新文件。不提交空 commit。

```powershell
cd frontend
pnpm vitest run
pnpm type-check
pnpm build
cd ..\backend
.\.venv\Scripts\python.exe -m pytest -q
```

通过标准：既有 10 个前端 spec + 本计划 3 个新 spec 全绿；`vue-tsc -b` 无错；`vite build` 成功；后端全量与 Task 4 相同（允许既有 8 skip）。

---

## 提交边界

| 顺序 | Commit | 含文件 |
|---|---|---|
| 1 | `feat(candidate-center): query assigned applications` | repository + 其测试 |
| 2 | `feat(candidate-center): map list and detail aggregates` | schemas + service + 其测试 |
| 3 | `feat(candidate-center): expose read-only APIs` | endpoint + `router.py` + API 测试 |
| 4 | `feat(candidate-center): add frontend client` | `candidateCenter.ts` + api spec |
| 5 | `feat(candidate-center): add list page and menu` | AdminLayout、router、ListView、list spec |
| 6 | `feat(candidate-center): add detail page` | DetailView + detail spec |

禁止混入：Alembic、ORM 改表、真实 Dify、Offer、筛选决策、邀约发送、转写校对、出题、单轮分析写路径、阶段 7/8 未完成闭环的补写、`.gitignore`、push。

每个任务流程固定为：写上表失败测试 → 跑指定命令确认 RED → 最小实现 → 跑同一命令 GREEN → 再提交。禁止先实现后补测试。

---

## 规格覆盖自检

| 规格条款 | 计划任务 |
|---|---|
| §1 只读、仅 manage、不扩展 execute | 全局约束；Task 3/6 403 |
| §1.2 无迁移新表、不改状态、不伪造完成 | 文件映射禁止 alembic/models；Task 2/7 缺失即缺失 |
| §3.1 EXISTS 默认 assigned=true | Task 1/3/6 |
| §3.2 取消/异常结束计入 | Task 1 SQL 不排除；Task 2 cancelled 展示轮次 |
| §3.3 白名单筛选分页关键词 | Task 1 compile；Task 2/3 400 |
| §3.4 展示轮次随 assigned；状态只跟展示轮次 | Task 1 子查询；Task 2 派生 |
| §3.4 列表无正文密文 raw | Task 2/3 键集 |
| §4.1 candidate+application 404 | Task 2/3 |
| §4.2 简历摘要、评分摘要、全部轮次、no-store | Task 2/3/7 |
| §4.2 复用 score-report 页与 interviews 页（非数据 path 混用） | Task 7 |
| §4.3 其他应聘只摘要 | Task 2/7 |
| §5.1 稳定符号与职责切分 | 稳定接口名表 |
| §5.4 批量展示轮次、无 N+1、无面试官重复行 | Task 1 |
| §5.5 五批 TDD | Task 1–3 后端；Task 6–7 前端；Task 4/8 回归 |
| §6.2 首批不做写操作/Dify/Offer | 提交边界 |

对照完成后：本文件无 TODO、TBD、placeholder，无「类似处理」，无「补充测试」作验收替代。
