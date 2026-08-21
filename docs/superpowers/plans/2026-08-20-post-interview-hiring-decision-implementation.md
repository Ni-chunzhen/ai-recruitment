# 面后录用建议（HiringDecision）— TDD 实施计划

> **For agentic workers:** 按任务顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。  
> **Task 5 UAT runbook：只记录、禁止执行**（零 Dify、零通知、零 worker、零触碰受保护 ID）。

**规格：** `docs/superpowers/specs/2026-08-20-post-interview-hiring-decision-design.md`  
**基线：** `main` @ `620ffa7`  
**方法：** TDD。符号名锁定为规格 §10；禁止临时改名。

## 全局约束

- **不**复用 `ScreeningDecision` 表 / `POST …/screening-decisions` / 初筛 reason 目录。
- **不**写 `hired`；**不**建 Offer 表/API/`offer.*`；**不**发 SMTP/站内通知。
- **不**调用 / 入队 AI task；**不**调用 Dify；**不**因决策 regenerate 分析。
- **不**存自由文本 `reason`、quote/summary、敏感属性列。
- **不**做撤销 / `pending_offer`→`interviewing` / Offer 流程。
- **不**让 `interview.execute` 读写 HiringDecision（含前端对 execute 隐藏整块 UI）。
- **不**触碰、retry、cancel、mark-stale、SQL/Redis 干预：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- 自动化：**零**真实 Dify HTTP；**不**启动 worker。
- 本计划各任务 **默认不提交**；「提交边界」仅当用户明确要求时适用。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| 流水新态 | `PIPELINE_PENDING_OFFER = "pending_offer"` |
| 决策枚举 | `HIRING_RECOMMEND_HIRE="recommend_hire"` · `HIRING_REJECT="reject"` · `HIRING_HOLD="hold"` |
| 模型 / 表 | `HiringDecision` / `hiring_decisions` |
| 服务 | `create_hiring_decision` · `list_hiring_decisions` · `list_hiring_reason_catalog` |
| 审计 action | `application.hiring_decision` |
| 幂等索引 | `uq_hiring_decisions_idempotency` |
| 应用索引 | `ix_hiring_decisions_application_id` |
| STALE 真源 | `app.services.interview_analyses.is_analysis_version_stale`（由既有 `_is_stale` **提升为公开同名语义**；`_is_stale` 改为调用它或成为别名） |
| 写/读权限 | **仅** `recruitment.manage` |
| 迁移 revision | **`014_hiring_decisions`**（文件 `backend/alembic/versions/014_hiring_decisions.py`；`down_revision` = 当前链尾 `013_…`） |
| 受保护 running | 上表两 UUID（不触碰） |

## 规格覆盖映射

| 规格章节 | 本计划 Task |
|---|---|
| §3 模型与迁移 · §3.1 `pending_offer` | Task 1 |
| §4 状态机 · §6 证据门禁 · §7 锁/幂等/事务 · §6.3 审计 | Task 2 |
| §5 API/权限 · §5.3 reason 目录 · 白名单联动 | Task 3 |
| 前端人工决策 + 历史（规格交付联动） | Task 4 |
| §8 测试/UAT · §1.3 / §9 非目标 | Task 5 |

---

## Task 1 — 模型、枚举、`pending_offer`、Alembic 014

**Consumes：** 规格 §3、§10。  
**Produces：** ORM + 常量 + 迁移 upgrade/downgrade；**无**业务 API。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/models/resume.py` | 新增 `PIPELINE_PENDING_OFFER`；扩展 `PIPELINE_STATUSES`；新增 Hiring 决策/reason 常量、`list_hiring_reason_catalog()`、`HiringDecision` 模型类（紧邻 `ScreeningDecision` 之后） |
| `backend/app/models/__init__.py` | 导出新常量与 `HiringDecision` |
| `backend/alembic/versions/014_hiring_decisions.py` | **新建** upgrade/downgrade |
| `backend/tests/db/test_migration_014_hiring_decisions.py` | **新建** 迁移/模型结构断言 |
| `backend/tests/models/test_hiring_decision_constants.py` | **新建** 常量与 catalog 断言 |

### 精确签名 / 结构

```python
# models/resume.py
PIPELINE_PENDING_OFFER = "pending_offer"
# PIPELINE_STATUSES 必须六元：
# pending_parse, pending_hr_screen, interviewing, pending_offer, rejected, talent_pool

HIRING_RECOMMEND_HIRE = "recommend_hire"
HIRING_REJECT = "reject"
HIRING_HOLD = "hold"
HIRING_DECISIONS = frozenset({HIRING_RECOMMEND_HIRE, HIRING_REJECT, HIRING_HOLD})

# reason codes — 与规格 §5.3 十二码完全一致（禁止增删改名）
HIRING_REASON_MEETS_ROLE_BAR = "meets_role_bar"
HIRING_REASON_STRONG_ROUND_EVIDENCE = "strong_round_evidence"
HIRING_REASON_HIRE_OTHER = "hire_other"
HIRING_REASON_SKILL_GAP = "skill_gap"
HIRING_REASON_EXPERIENCE_INSUFFICIENT = "experience_insufficient"
HIRING_REASON_COMMUNICATION_INSUFFICIENT = "communication_insufficient"
HIRING_REASON_INCOMPLETE_OR_WEAK_EVIDENCE = "incomplete_or_weak_evidence"
HIRING_REASON_REJECT_OTHER = "reject_other"
HIRING_REASON_NEED_ANOTHER_ROUND = "need_another_round"
HIRING_REASON_NEED_MORE_EVIDENCE = "need_more_evidence"
HIRING_REASON_AWAITING_STAKEHOLDER = "awaiting_stakeholder"
HIRING_REASON_HOLD_OTHER = "hold_other"

HIRING_REASON_CODES = frozenset({...上列十二码...})
HIRING_REASON_CATALOG: tuple[tuple[str, str, frozenset[str]], ...] = (
    # (code, label, allowed_decisions)
    (HIRING_REASON_MEETS_ROLE_BAR, "达到岗位录用标准", frozenset({HIRING_RECOMMEND_HIRE})),
    # ... 其余按规格 §5.3 顺序 ...
)

def list_hiring_reason_catalog() -> list[dict[str, object]]:
    # 每项: code, label, allowed_decisions(list)；禁止 requires_description 键

class HiringDecision(Base):
    __tablename__ = "hiring_decisions"
    __table_args__ = (
        Index(
            "uq_hiring_decisions_idempotency",
            "application_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_hiring_decisions_application_id", "application_id"),
    )
    # 列：规格 §3.2 全列；禁止 reason / quote* / summary* / offer_id / ai_result_id
    # FK: application_id → job_applications.id CASCADE
    #     round_id → interview_rounds.id RESTRICT
    #     analysis_version_id → interview_round_analysis_versions.id RESTRICT
    #     decided_by → users.id SET NULL
```

迁移 `upgrade()` 必须：

1. `op.create_table("hiring_decisions", …)` 列类型与 ORM 一致（`overall_score` 用 `sa.Float()` 或 `Numeric`——**锁定 `sa.Float()`** 对齐分析 `overall_score`）。
2. 创建 `uq_hiring_decisions_idempotency`（部分唯一）与 `ix_hiring_decisions_application_id`。
3. **不** ALTER `screening_decisions`；**不**写 `hired`；**不**建 offer 表。

迁移 `downgrade()` 必须：

1. `op.drop_index("uq_hiring_decisions_idempotency", …)`（及 application 索引）。
2. `op.drop_table("hiring_decisions")`。
3. **不**删除其它表数据。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_pipeline_statuses_include_pending_offer` | `"pending_offer" in PIPELINE_STATUSES`；`len(PIPELINE_STATUSES)==6` |
| `test_hiring_decisions_exactly_three` | `HIRING_DECISIONS == {recommend_hire, reject, hold}` |
| `test_hiring_reason_catalog_twelve_codes_no_free_text_flag` | `len(list_hiring_reason_catalog())==12`；每项无 `requires_description`；allowed_decisions ⊆ HIRING_DECISIONS |
| `test_hiring_decision_model_has_no_reason_text_column` | `"reason" not in HiringDecision.__table__.c`；无 `quote`/`summary`/`offer` 列名子串 |
| `test_migration_014_upgrade_creates_hiring_decisions_and_downgrade_drops` | 用项目既有 alembic 测试夹具：upgrade 后表存在且含 `uq_hiring_decisions_idempotency`；downgrade 后表不存在（对齐 `tests/db/test_migration_012.py` 风格） |

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/models/test_hiring_decision_constants.py tests/db/test_migration_014_hiring_decisions.py -q --tb=short
```

- RED：缺常量/缺迁移文件。  
- GREEN：同上全绿。

### GREEN 步骤

1. 写常量 + `HiringDecision` + catalog。  
2. 写 `014_hiring_decisions.py`。  
3. 导出 `__init__.py`。  
4. 以 pytest 迁移测为门禁；实施机另跑一次 `alembic upgrade head` 与 `downgrade -1` 交叉确认（命令见下）。

**迁移回滚命令（实施机）：**

```text
cd backend
.venv\Scripts\alembic.exe downgrade -1
.venv\Scripts\alembic.exe upgrade head
```

**提交边界（仅当用户明确要求）：** 上表模型/迁移/测试文件。禁止 `.env`、前端、service。

---

## Task 2 — Repository / Service（校验、三态、幂等、锁、同事务）

**Consumes：** Task 1；规格 §4、§6、§7。  
**Produces：** 可测的 `create_hiring_decision` / `list_hiring_decisions`；**无** HTTP 路由。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/services/interview_analyses.py` | 将 `_is_stale` **公开**为 `is_analysis_version_stale(version, transcript) -> bool`；内部调用保持语义不变；既有 `_is_stale` 若保留则一行委托 |
| `backend/app/repositories/hiring_decisions.py` | **新建**：`add_hiring_decision`、`find_hiring_by_idempotency`、`list_hiring_by_application` |
| `backend/app/services/hiring_decisions.py` | **新建**：错误类型 + `create_hiring_decision` + `list_hiring_decisions` + reason 校验 |
| `backend/tests/services/test_hiring_decisions.py` | **新建** 全部门禁/三态/锁/幂等/STALE 用例 |
| `backend/tests/services/test_interview_analyses.py` | 追加：`is_analysis_version_stale` 与旧 `_is_stale` 行为一致（若仍导出别名） |

### 精确签名

```python
# repositories/hiring_decisions.py
async def add_hiring_decision(session: AsyncSession, row: HiringDecision) -> HiringDecision: ...
async def find_hiring_by_idempotency(
    session: AsyncSession, *, application_id: UUID, idempotency_key: str
) -> HiringDecision | None: ...
async def list_hiring_by_application(
    session: AsyncSession, *, application_id: UUID
) -> list[HiringDecision]:  # ORDER BY created_at ASC, id ASC

# services/hiring_decisions.py
class HiringNotFoundError(Exception): ...
class HiringStateError(Exception): ...
class HiringValidationError(Exception): ...
class HiringConflictError(Exception): ...

@dataclass(frozen=True)
class HiringDecisionRequestData:
    decision: str
    reason_code: str
    analysis_version_id: UUID
    lock_version: int
    idempotency_key: str | None = None

@dataclass(frozen=True)
class HiringDecisionResult:
    id: UUID
    application_id: UUID
    decision: str
    reason_code: str
    round_id: UUID
    analysis_version_id: UUID
    overall_score: float | None
    analysis_version_no: int | None
    from_pipeline_status: str
    to_pipeline_status: str
    lock_version: int
    created_at: datetime
    decided_by: UUID | None

async def create_hiring_decision(
    session: AsyncSession,
    *,
    application_id: UUID,
    payload: HiringDecisionRequestData,
    actor: User,
    request_context: RequestContext,
) -> HiringDecisionResult: ...

async def list_hiring_decisions(
    session: AsyncSession, *, application_id: UUID
) -> list[HiringDecisionResult]: ...
```

### `create_hiring_decision` 算法（锁定顺序）

1. `get_application_by_id`；无 → `HiringNotFoundError`。  
2. `application.lock_version != payload.lock_version` → `HiringConflictError("application was updated by another user; refresh and retry")`。  
3. `status != in_progress` 或 `pipeline_status != interviewing` → `HiringStateError`（含 `pending_offer`/终态）。  
4. `decision` ∉ `HIRING_DECISIONS` 或 `reason_code` 不允许该决策 → `HiringValidationError`。  
5. 若 `idempotency_key`：命中则 **return** 已有行映射（**不**再改状态）；返回的 `lock_version` = **当前** `application.lock_version`。  
6. 加载 `InterviewRoundAnalysisVersion` by `analysis_version_id`；无 → NotFound/Validation。  
7. 经 `round_id` 加载 `InterviewRound`；`round.application_id != application.id` → Validation。  
8. 加载该轮 `InterviewRoundAnalysis`；`current_version_id != analysis_version_id` → `HiringStateError`（文案含 `current`）。  
9. 加载 transcript；`is_analysis_version_stale(version, transcript)` → `HiringStateError`（文案含 `stale`）。  
10. 快照：`overall_score`、`version_no`→`analysis_version_no`、`transcript_version_id`、`job_version_id`、`round_id`。  
11. 计算 `to_pipeline`：`recommend_hire→pending_offer`；`reject→rejected`；`hold→interviewing`。  
12. INSERT `HiringDecision`（`from_pipeline_status="interviewing"`）。  
13. 更新 application：pipeline；`reject` 时 `status=rejected`、`close_action=reject`、`close_reason=reason_code`（仅码）；**禁止** `status=hired`。  
14. `lock_version += 1`；`updated_at=now`。  
15. INSERT `ApplicationStatusLog`（hold 亦写，`from==to==interviewing`，`reason=reason_code` 或 `None`）。  
16. `record_audit(action="application.hiring_decision", …)`；`changes` ⊆ 规格 §6.3 允许键。  
17. `await session.commit()`；return Result。  

**禁止**函数体内出现：`enqueue_`、`process_ai_task`、`process_sensitive`、`run_dify`、`hired`、SMTP/邮件符号。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_recommend_hire_moves_to_pending_offer` | pipeline=`pending_offer`；status=`in_progress`；非 `hired`；有 decision 行 + status_log + audit |
| `test_reject_closes_application` | pipeline=`rejected`；status=`rejected`；`close_reason==reason_code` |
| `test_hold_keeps_interviewing_and_increments_lock` | pipeline 仍 `interviewing`；`lock_version` +1；可再次 create |
| `test_rejects_when_pipeline_pending_offer` | StateError；零新行 |
| `test_rejects_stale_analysis_version` | StateError 且消息匹配 `(?i)stale` |
| `test_rejects_non_current_analysis_version` | StateError 且消息匹配 `(?i)current` |
| `test_rejects_analysis_from_other_application` | Validation/NotFound |
| `test_lock_version_conflict` | ConflictError；零新行 |
| `test_idempotency_returns_same_row_without_second_transition` | 同 key 两次；同 `id`；pipeline 副作用不双写（recommend 后仍一次 pending_offer） |
| `test_audit_changes_exclude_quote_and_summary` | 审计 changes 键集合 ⊆ 允许集；无 quote/summary |
| `test_create_hiring_decision_source_has_no_ai_or_dify_calls` | `inspect.getsource(create_hiring_decision)` 不含 `enqueue_`、`run_dify`、`process_sensitive`、`"hired"` |

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/services/test_hiring_decisions.py tests/services/test_interview_analyses.py -q --tb=short -k "hiring or is_analysis_version_stale or _is_stale"
```

**提交边界：** repository/service/分析 STALE 导出 + 对应测试。禁止 endpoint、前端。

---

## Task 3 — Schema / API / 权限 / pipeline 白名单

**Consumes：** Task 2；规格 §5。  
**Produces：** 三端点仅 `recruitment.manage`；Schema Literal 含 `pending_offer`；候选人中心过滤接纳新态。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/schemas/hiring_decision.py` | **新建** Request/Out/List/ReasonCatalog |
| `backend/app/schemas/resume.py` | `PipelineStatus` Literal **加入** `"pending_offer"` |
| `backend/app/schemas/candidate_center.py` | pipeline 校验白名单 **必须**含 `pending_offer`（与 `PIPELINE_STATUSES` 对齐） |
| `backend/app/api/v1/endpoints/hiring_decisions.py` | **新建** 三路由 |
| `backend/app/api/v1/router.py` | `include_router(hiring_decisions.router)` |
| `backend/app/services/candidate_center.py` | 详情 DTO **增加** `lock_version: int`（供前端 POST；manage-only 已满足） |
| `backend/app/schemas/candidate_center.py` | Detail 增加 `lock_version` |
| `backend/tests/api/v1/test_hiring_decisions.py` | **新建** API 权限与契约 |
| `backend/tests/api/v1/test_candidate_center.py` | 断言 detail 含 `lock_version`；过滤 `pipeline_status=pending_offer` 返回 200（非 400） |

### 精确签名（HTTP）

```python
# schemas/hiring_decision.py
class HiringDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["recommend_hire", "reject", "hold"]
    reason_code: str = Field(min_length=1, max_length=64)
    analysis_version_id: UUID
    lock_version: int
    idempotency_key: str | None = Field(default=None, max_length=128)
    # 禁止 reason / notes / quote 字段

class HiringDecisionOut(BaseModel):
    id: UUID
    application_id: UUID
    decision: Literal["recommend_hire", "reject", "hold"]
    reason_code: str
    round_id: UUID
    analysis_version_id: UUID
    overall_score: float | None
    analysis_version_no: int | None
    from_pipeline_status: PipelineStatus  # 含 pending_offer
    to_pipeline_status: PipelineStatus
    lock_version: int
    created_at: datetime
    decided_by: UUID | None

class HiringDecisionListResponse(BaseModel):
    items: list[HiringDecisionOut]

class HiringReasonCodeItem(BaseModel):
    code: str
    label: str
    allowed_decisions: list[str]
    # 禁止 requires_description

class HiringReasonCodeListResponse(BaseModel):
    items: list[HiringReasonCodeItem]

# endpoints/hiring_decisions.py
router = APIRouter(tags=["hiring-decisions"])

@router.post(
    "/applications/{application_id}/hiring-decisions",
    response_model=HiringDecisionOut,
    status_code=201,
)
async def create_hiring_decision_endpoint(
    application_id: UUID,
    payload: HiringDecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> HiringDecisionOut: ...

@router.get(
    "/applications/{application_id}/hiring-decisions",
    response_model=HiringDecisionListResponse,
)
async def list_hiring_decisions_endpoint(
    application_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> HiringDecisionListResponse:
    # response.headers["Cache-Control"] = "no-store"

@router.get(
    "/hiring-decision-reason-codes",
    response_model=HiringReasonCodeListResponse,
)
async def list_hiring_reason_codes_endpoint(
    _: User = Depends(require_permission("recruitment.manage")),
) -> HiringReasonCodeListResponse: ...
```

错误映射对齐 `resumes._map_error`：NotFound→404；State→409（或项目既有）；Validation→422；Conflict→409。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_post_hiring_decision_requires_manage` | execute-only → 403；manage → 201 |
| `test_get_hiring_history_requires_manage` | execute → 403；manage → 200 + `Cache-Control: no-store` |
| `test_reason_codes_requires_manage_and_has_twelve` | execute 403；manage 200；`len(items)==12`；无 `requires_description` |
| `test_post_body_forbids_free_text_reason_field` | body 含 `reason` → 422（extra forbid） |
| `test_post_recommend_hire_api_contract` | 201；`to_pipeline_status=="pending_offer"`；JSON 无 `reason` 文本字段名（除 `reason_code`） |
| `test_pipeline_status_literal_accepts_pending_offer` | `PipelineStatus` / candidate_center 校验接受 `pending_offer` |
| `test_candidate_center_detail_includes_lock_version` | manage GET detail → `lock_version` 为 int |

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/api/v1/test_hiring_decisions.py tests/api/v1/test_candidate_center.py -q --tb=short
```

**提交边界：** schemas/endpoints/router/candidate_center lock_version + API 测试。禁止前端业务组件（可随后 Task 4）。

---

## Task 4 — 前端：应聘详情人工决策与历史

**Consumes：** Task 3。  
**Produces：** 候选人中心详情内 manage 可见的决策区；**零** Offer 发送能力。

### 具体文件

| 路径 | 动作 |
|---|---|
| `frontend/src/api/hiringDecisions.ts` | **新建** 类型与 `createHiringDecision` / `listHiringDecisions` / `listHiringReasonCodes` |
| `frontend/src/api/resumes.ts` | `PipelineStatus` 联合类型 + `pipelineStatusLabel` 增加 `pending_offer: '待发 Offer（未发送）'`（文案锁定：强调未发送） |
| `frontend/src/api/candidateCenter.ts` | `CandidateCenterDetail.lock_version: number` |
| `frontend/src/views/CandidateCenterListView.vue` | 筛选下拉 **必须**增加 `pending_offer` 选项与标签 |
| `frontend/src/views/CandidateCenterDetailView.vue` | 增加「面后决策」区块 + 历史表 |
| `frontend/src/views/InterviewTimelineView.vue` | `pipelineLabels` 增加 `pending_offer`；**禁止**增加决策写按钮（execute 可能进入） |
| `frontend/tests/CandidateCenterDetailView.spec.ts` | 改写禁文案断言；新增决策 UI 测 |
| `frontend/tests/CandidateCenterListView.spec.ts` | 允许流水标签；仍禁 Offer 发送 |
| `frontend/tests/hiringDecisions.spec.ts` | **新建**：断言 `createHiringDecision` 路径与 body 键集合（无 `reason` 自由文本键） |

### UI 锁定文案与行为

| 元素 | 锁定 |
|---|---|
| 区块标题 | `面后决策`（`data-test="hiring-decision-panel"`） |
| 三按钮 | `建议录用` / `淘汰` / `暂缓`（`data-test="hiring-recommend-hire|hiring-reject|hiring-hold"`） |
| reason | **仅** `el-select` 绑定 catalog；**无** textarea |
| 分析版本 | 从该应聘 rounds 中选一轮 → 调用既有分析 versions API，仅当存在 `is_current && !is_stale` 才可提交；否则按钮 disabled + 提示 |
| `lock_version` | 使用详情返回的 `lock_version`；成功后用响应 `lock_version` 刷新本地并 `loadDetail()` |
| 历史 | `data-test="hiring-decision-history"` 表格：时间、决策、reason_code、分数、流水 from→to |
| 可见性 | 仅当 `pipeline_status==='interviewing'` 显示写操作；`pending_offer`/`rejected` 仅历史只读 |
| **禁止** | 按钮/文案含：`发送 Offer`、`Offer`、`自动决策`、`Dify`、`SMTP`、`hired`；无通知开关 |

分析 versions 调用（复用既有，不新建后端）：

```ts
// 既有 interview AI API 客户端中的 list analysis versions
// 选取 items.find(v => v.is_current && !v.is_stale)?.version_id
```

若前端尚无封装，在 `frontend/src/api/interviewAi.ts`（或现有分析 API 模块）**增加**只读 `listAnalysisVersions(roundId)`——**禁止**在此 Task 调用 generate。

### 精确前端 API

```ts
// api/hiringDecisions.ts
export type HiringDecisionType = 'recommend_hire' | 'reject' | 'hold'

export interface HiringDecisionRequest {
  decision: HiringDecisionType
  reason_code: string
  analysis_version_id: string
  lock_version: number
  idempotency_key?: string
}

export async function createHiringDecision(
  applicationId: string,
  body: HiringDecisionRequest,
): Promise<HiringDecisionOut>

export async function listHiringDecisions(
  applicationId: string,
): Promise<{ items: HiringDecisionOut[] }>

export async function listHiringReasonCodes(): Promise<{ items: HiringReasonCodeItem[] }>
```

### RED（Vitest）

| 测试 | 精确断言 |
|---|---|
| `shows hiring panel for interviewing manage user` | 存在 `hiring-decision-panel`；三按钮；无 textarea |
| `hides write actions when pending_offer` | mock `pipeline_status=pending_offer` → 无三按钮；仍可有历史 |
| `submits recommend_hire with reason_code and analysis_version_id` | stub POST；payload 无 `reason` 自由文本键；含 `lock_version` |
| `forbids offer-send and automation copy` | 全文 `not.toContain('发送 Offer')`；`not.toContain('自动决策')`；`not.toContain('Dify')`；`not.toContain('SMTP')`；允许「建议录用」/「面后决策」，**删除**旧断言 `not.toContain('录用')` / `not.toContain('淘汰')` 的一刀切 |
| `list filter includes pending_offer label` | 列表筛选或标签含待发 Offer（未发送）语义 |

### 验证命令

```text
cd frontend
pnpm vitest run tests/CandidateCenterDetailView.spec.ts tests/CandidateCenterListView.spec.ts tests/hiringDecisions.spec.ts
pnpm type-check
```

**提交边界：** 上表前端文件 + 测试。禁止改 `.env`、禁止 Offer 模块。

---

## Task 5 — TDD 回归 + 隔离 UAT runbook（只记录不执行）

**Consumes：** Task 1–4 全绿。  
**Produces：** 回归命令清单 + UAT runbook 文档段落（写在本计划本节；**禁止**在本 Task 实际跑 UAT / worker / Dify）。

### 5A — 自动化回归（实施时执行）

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/models/test_hiring_decision_constants.py tests/db/test_migration_014_hiring_decisions.py tests/services/test_hiring_decisions.py tests/api/v1/test_hiring_decisions.py tests/api/v1/test_candidate_center.py tests/services/test_resume_scoring.py tests/api/v1/test_screening_reason_codes.py -q --tb=short

cd frontend
pnpm vitest run tests/CandidateCenterDetailView.spec.ts tests/CandidateCenterListView.spec.ts tests/InterviewTimelineView.spec.ts tests/InterviewAnalysisDrawer.spec.ts
pnpm type-check
```

补充源码检索（必须零命中业务路径）：

```text
cd backend
.venv\Scripts\python.exe -c "from pathlib import Path; root=Path('app'); bad=[];
for p in root.rglob('*.py'):
 t=p.read_text(encoding='utf-8');
 if 'hiring_decision' in t.lower() or 'HiringDecision' in t:
  if 'run_dify' in t or 'enqueue_' in t and 'hiring' in t.lower(): bad.append(str(p));
print('ok' if not bad else bad)"
```

（更简：对 `services/hiring_decisions.py` / `endpoints/hiring_decisions.py` 断言无 `run_dify`/`enqueue_`/`hired` 赋值。）

Timeline/AnalysisDrawer 既有「禁 Offer」测 **保持绿**（它们不应出现面后写按钮）。

### 5B — 隔离 UAT runbook（**只记录、禁止执行**）

前缀：`UAT-HD-20260820-*`（隔离数据；非生产候选人）。

| 步骤 | 预期（记录用） | 禁止 |
|---|---|---|
| T0 | manage 登录；确认应聘 `interviewing`+`in_progress`；存在 current 非 STALE 分析版本；记录 `lock_version` | 不调 Dify；不启 worker |
| T1 | `POST …/hiring-decisions` `hold` + 合法 reason + version + lock | 不发通知 |
| T2 | 再 `POST` `recommend_hire`（新 lock + 新 idempotency）→ `pending_offer` | 不写 `hired` |
| T3 | 对 `pending_offer` 再 POST → 4xx State | 无撤销 API |
| T4 | 另一隔离应聘走 `reject` → `rejected` | — |
| T5 | execute 账号 GET history / reason-codes → 403 | — |
| T6 | DB 只读：决策行、status_log、audit 一致；`ai_tasks` 无新增因本操作；无 offer 表行 | 不 SQL 改受保护 running |
| T7 | 确认受保护 UUID 仍 running、未被 cancel/retry | 触碰即失败 |

并发抽检（记录）：两请求同 `lock_version` 并行 → 恰一成功、一 409。

**本 Task 提交边界：** 仅当用户要求提交时，允许把本 runbook 留在计划文件；**禁止**提交「已执行 UAT」伪记录；禁止 `.env`。

---

## 全部 Task GREEN 后总回归

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/models/test_hiring_decision_constants.py tests/db/test_migration_014_hiring_decisions.py tests/services/test_hiring_decisions.py tests/api/v1/test_hiring_decisions.py tests/api/v1/test_candidate_center.py -q --tb=short

cd frontend
pnpm vitest run tests/CandidateCenterDetailView.spec.ts tests/CandidateCenterListView.spec.ts tests/hiringDecisions.spec.ts
pnpm type-check
```

迁移回滚确认（实施机，非 CI 强制）：

```text
cd backend
.venv\Scripts\alembic.exe downgrade -1
.venv\Scripts\alembic.exe upgrade head
```

---

## 提交边界总表（仅用户明确要求时）

| Task | 允许暂存路径 | 禁止 |
|---|---|---|
| 1 | `models/resume.py`、`models/__init__.py`、`alembic/versions/014_hiring_decisions.py`、相关 tests | `.env`、service、前端 |
| 2 | `repositories/hiring_decisions.py`、`services/hiring_decisions.py`、`services/interview_analyses.py`、tests | endpoints、前端 |
| 3 | schemas、`endpoints/hiring_decisions.py`、`router.py`、candidate_center lock_version、API tests | 前端组件 |
| 4 | `frontend/src/api/*`、`CandidateCenter*View.vue`、Timeline 标签、vitest | Offer 模块、`.env` |
| 5 | 计划内 runbook 文本（若需） | 伪造 UAT 结果、worker 日志、受保护 ID 操作 |
| 合并 | 单 commit 信息建议：`feat(hiring): add post-interview hiring decisions and pending_offer` | `push` 除非用户明示 |

---

## 自检清单（计划完成度）

- [x] 五 Task 覆盖：模型迁移 · service · API · 前端 · 回归/UAT runbook
- [x] 每 Task 含精确文件、接口签名、RED/GREEN、验证命令、提交边界
- [x] 迁移 `014_hiring_decisions` 含 upgrade **与** downgrade；回滚命令已写
- [x] 三态迁移、STALE/current 门禁、幂等、乐观锁、同事务审计已落到 Task 2
- [x] API 仅 `recruitment.manage`；固定 reason；`extra=forbid` 无自由文本
- [x] 前端在候选人详情；明确禁止 Offer 发送/自动决策/Dify/SMTP
- [x] UAT **只记录不执行**；不调 Dify、不发通知、不写 hired；覆盖并发/幂等/STALE/权限/隐私
- [x] 无 TBD / 无双主方案；本文件只写计划，未编码、未提交、未 push
