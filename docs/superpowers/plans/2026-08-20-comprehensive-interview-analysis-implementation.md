# 综合面试分析 — TDD 实施计划

> **For agentic workers:** 按 Task 1→5 顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。
> **Task 5 UAT runbook：只写入本计划、禁止执行**（零 Dify live、零默认队列消费、零触碰受保护 ID）。

**规格：** `docs/superpowers/specs/2026-08-20-comprehensive-interview-analysis-design.md`
**基线：** `main` @ `52ca05b`（HiringDecision / `pending_offer` / 单轮 `ai_sensitive`+mock 已合入）
**方法：** TDD。符号名锁定为规格 §11；禁止临时改名。

## 全局约束

- **不**开通 Dify live / workflow YAML / 综合专用 Key / live 开关；`run_dify` 对综合 **无条件** `run_mock`。
- **不**改 `job_applications.pipeline_status` / `status` / `lock_version`；**不** INSERT/UPDATE `HiringDecision`；**不**改 `HiringDecision.analysis_version_id` 语义。
- **不**建 Offer / 通知 / `offer.*`；**不**自动决策。
- **不**让 `interview.execute` 读综合（API 403 + 无 UI）；**不**新增 permission code。
- **不**把转写/JD/简历/quote/维度长文写入 `input_snapshot`、`round_refs`、审计 `changes`、公共 attempt JSONB。
- **不**要求 ≥2 轮合格分析才可生成；1 轮合法但必须带覆盖边界字段。
- **不**在默认 `celery` 执行综合；**禁止**依赖 `-Q celery,ai_sensitive` 混布。
- **不**触碰、retry、cancel、mark-stale、SQL/Redis 干预：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- **不**消费/清理/窥探默认 celery 队列既有消息。
- 自动化：**零**真实 Dify HTTP；Task 1–4 **不**启动常驻 worker。
- 本计划各任务 **默认不提交**；「提交边界」仅当用户明确要求时适用。
- **一期省略**独立综合维度表（规格 §3.2 可选）；摘要仅 `overall_summary_encrypted` + JSONB refs/coverage。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| Task type | `TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE = "INTERVIEW_COMPREHENSIVE_ANALYZE"` |
| 敏感白名单 | `SENSITIVE_AI_TASK_TYPES` **三元**：题纲 ∪ 单轮分析 ∪ 综合 |
| business | `BUSINESS_TYPE_APPLICATION`；`business_id = application_id` |
| 工作流 | `COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION="1.0"`；`COMPREHENSIVE_WORKFLOW_KEY="interview_comprehensive_analyze"`；`COMPREHENSIVE_WORKFLOW_VERSION="1.0"` |
| 表 | `application_comprehensive_analyses` / `application_comprehensive_analysis_versions` |
| ORM | `ApplicationComprehensiveAnalysis` / `ApplicationComprehensiveAnalysisVersion` |
| 版本标签 | `C{n}` |
| 服务模块 | `app.services.comprehensive_analyses` |
| 仓库模块 | `app.repositories.comprehensive_analyses` |
| 生成 | `request_comprehensive_analysis_generation` · `dispatch_persisted_comprehensive_analysis_task` · `persist_comprehensive_analysis_result` |
| 读取 | `list_comprehensive_analysis` · `get_comprehensive_analysis_version_detail` |
| STALE | `is_comprehensive_version_stale(...)`（动态）；单轮复用 `is_analysis_version_stale` |
| 覆盖构建 | `build_coverage_report(...)` |
| Gap codes | `cancelled` · `ended_abnormally` · `not_completed` · `without_transcript` · `transcript_unconfirmed` · `analysis_none` · `analysis_stale` · `excluded_other` |
| 幂等 action | `comprehensive_analysis.generate` |
| 审计 | `comprehensive_analysis.generate_requested` · `comprehensive_analysis.generated` |
| 入队 | `enqueue_sensitive_interview_ai_task` → `process_sensitive_ai_task` |
| 转投 reason | `interview_ai_requires_sensitive_queue` |
| Mock | `mock_interview_comprehensive_analyze` |
| 权限 | **仅** `recruitment.manage` |
| 迁移 | **`015_comprehensive_interview_analysis`**（`down_revision` = `014_hiring_decisions`） |
| API 前缀 | `/applications/{application_id}/comprehensive-analysis` |
| 受保护 running | 上表两 UUID |

## 规格覆盖映射

| 规格章节 | 本计划 Task |
|---|---|
| §3 模型与迁移 · §3.1 task 常量 · §11 符号 | Task 1 |
| §4.1–4.2 输入 · §5 覆盖 · §6 状态机/STALE/副作用 · §8 幂等审计 | Task 2 |
| §4.3–4.4 mock/队列 · §2 敏感白名单扩展 · §9.1 队列/隐私测 | Task 3 |
| §7 API/权限/前端 · §6.1 pending_offer 只读 | Task 4 |
| §9 测试/UAT · §1.3 / §10 非目标 · 回归 | Task 5 |

---

## Task 1 — 模型与 Alembic 015

**Consumes：** 规格 §3、§11。
**Produces：** ORM + task 常量扩容 + 迁移 upgrade/downgrade + 结构/关系测试；**无** service / API / worker 行为变更（除常量被 import 外）。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/models/ai_task.py` | 新增 `TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE`；扩 `TASK_TYPES`、`SENSITIVE_AI_TASK_TYPES`、模型级 `CheckConstraint` 字面量列表 |
| `backend/app/models/comprehensive_analysis.py` | **新建** 两表 ORM + gap/workflow 常量（或把常量放本文件顶部） |
| `backend/app/models/__init__.py` | 导出新模型与 task 常量 |
| `backend/app/schemas/ai_task.py` | `TaskType` Literal **七值**（原六 + 综合） |
| `backend/alembic/versions/015_comprehensive_interview_analysis.py` | **新建** upgrade/downgrade |
| `backend/tests/models/test_comprehensive_analysis_constants.py` | **新建** 常量/无禁止列断言 |
| `backend/tests/db/test_migration_015_comprehensive_analysis.py` | **新建** 迁移与 FK/唯一约束断言 |
| `backend/tests/integrations/test_migration_015_pg.py`（若项目沿用 013 风格 PG 集成） | **新建或追加**：ck 接受综合、拒绝未知 `task_type`；表存在 |

### 精确结构

```python
# models/ai_task.py
TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE = "INTERVIEW_COMPREHENSIVE_ANALYZE"
TASK_TYPES = frozenset({..., TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE})
SENSITIVE_AI_TASK_TYPES = frozenset(
    {
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
    }
)
# CheckConstraint task_type IN (...含综合...)

# models/comprehensive_analysis.py
COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION = "1.0"
COMPREHENSIVE_WORKFLOW_KEY = "interview_comprehensive_analyze"
COMPREHENSIVE_WORKFLOW_VERSION = "1.0"
COMPREHENSIVE_GAP_CODES = frozenset({
    "cancelled", "ended_abnormally", "not_completed", "without_transcript",
    "transcript_unconfirmed", "analysis_none", "analysis_stale", "excluded_other",
})

class ApplicationComprehensiveAnalysis(Base):
    __tablename__ = "application_comprehensive_analyses"
    # id PK; application_id UNIQUE FK job_applications CASCADE
    # current_version_id NULL FK versions SET NULL (use_alter); created_at; updated_at

class ApplicationComprehensiveAnalysisVersion(Base):
    __tablename__ = "application_comprehensive_analysis_versions"
    # id; analysis_id CASCADE; version_no>0; version_label;
    # uq (analysis_id, version_no); uq (analysis_id, version_label); uq ai_task_id
    # input_snapshot_hash String(64); round_refs JSONB NOT NULL; coverage_report JSONB NOT NULL
    # overall_score Numeric NULL (1–5 check 可选); overall_summary_encrypted Text NOT NULL
    # created_by SET NULL; created_at
    # 禁止列：jd_text/resume_text/transcript_text/明文 summary/quote*/hiring_decision_id
```

迁移 `upgrade()` 必须：

1. 创建两表 + 索引/唯一约束/FK（对齐 ORM；`current_version_id` 可用 `use_alter` 二次加 FK，镜像 `interview_round_analyses`）。
2. 替换/扩展 `ck_ai_tasks_task_type` 纳入 `INTERVIEW_COMPREHENSIVE_ANALYZE`（策略对齐 013 严格校验）。
3. **不** ALTER `hiring_decisions`；**不**改单轮分析表。

迁移 `downgrade()` 必须：

1. 恢复 `ck_ai_tasks_task_type` 到六类型（014/013 终态）。
2. drop 综合两表（先 versions 后 set，或按 FK 顺序）。
3. **不**删 hiring / 单轮分析数据。

### RED

| 测试函数 | 预期失败（RED） / 精确断言（GREEN 目标） |
|---|---|
| `test_task_types_include_comprehensive` | 缺常量 → ImportError/AssertionError；绿：`INTERVIEW_COMPREHENSIVE_ANALYZE in TASK_TYPES`；`len(TASK_TYPES)==7` |
| `test_sensitive_whitelist_is_exactly_three` | 仍为二元 → fail；绿：`SENSITIVE_AI_TASK_TYPES` 恰三元含综合 |
| `test_task_type_literal_includes_comprehensive` | Literal 无综合 → 静态测 fail；绿：`get_args(TaskType)` 含该字符串 |
| `test_comprehensive_models_have_no_plaintext_body_columns` | 模型缺或含禁列 → fail；绿：无 `overall_summary` 明文列；无 `quote`/`jd_text`/`resume_text` 列名 |
| `test_migration_015_upgrade_creates_tables_and_ck_accepts_comprehensive` | 无迁移文件 → fail；绿：upgrade 后两表存在；INSERT 合法综合 `task_type` 成功；未知 `task_type` 被 ck 拒绝 |
| `test_migration_015_downgrade_restores_six_type_ck_and_drops_tables` | downgrade 后表不存在；六类型 ck 恢复 |
| `test_version_ai_task_id_unique_and_application_unique` | 约束缺失 → fail；绿：重复 `ai_task_id` / 重复 `application_id` 违反唯一 |

### GREEN 实现要点

1. 写 `comprehensive_analysis.py` ORM + 常量。
2. 扩 `ai_task.py` 与 `TaskType` Literal。
3. 写 `015_*.py`（`down_revision="014_hiring_decisions"`）。
4. 导出 `__init__.py`。
5. 以迁移测为门禁；实施机可另跑 `alembic upgrade head` / `downgrade -1`（不写入本 Task 必需自动化）。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/models/test_comprehensive_analysis_constants.py tests/db/test_migration_015_comprehensive_analysis.py -q --tb=short
```

若存在 PG 集成测：

```text
.venv\Scripts\python.exe -m pytest tests/integrations/test_migration_015_pg.py -q --tb=short
```

### 禁止范围

- 禁止 service / endpoint / frontend / worker 行为改动（除常量被后续 Task 使用）。
- 禁止 `.env`、Dify YAML、改 014。
- 禁止启动 worker、触碰受保护 task。

### 提交边界（仅当用户明确要求）

上表模型/迁移/测试文件。禁止前端、service、`.env`。

---

## Task 2 — 综合分析服务与持久化

**Consumes：** Task 1；规格 §4.1–4.2、§5、§6、§8。
**Produces：** 可测的生成请求（创建 PENDING task、**不**强制 enqueue）、覆盖报告、动态 STALE、persist、list/detail；**无** HTTP；**无** provider live。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/repositories/comprehensive_analyses.py` | **新建** get/create set、list versions、get by id/task、next version_no、for_update |
| `backend/app/services/comprehensive_analyses.py` | **新建** 全部门禁/覆盖/STALE/幂等/persist/list |
| `backend/app/schemas/comprehensive_analysis.py` | **新建** 内部/对外 dataclass 或 Pydantic 结果契约（coverage、round_ref 白名单校验辅助） |
| `backend/tests/services/test_comprehensive_analyses.py` | **新建** 全部门禁与副作用用例 |

### 精确接口

```python
# services/comprehensive_analyses.py
IDEMPOTENCY_ACTION_GENERATE = "comprehensive_analysis.generate"
FORBIDDEN_SNAPSHOT_KEYS = frozenset({
    "text", "quote", "overall_summary", "analysis", "strengths", "risks",
    "suggested_follow_ups", "jd_text", "jd_content", "resume_text",
    "segment_text", "transcript_text",
})  # 另禁任意 *_encrypted 拷贝进 snapshot

@dataclass(frozen=True)
class CoverageGap:
    round_id: UUID
    sequence_no: int | None
    reason_code: str
    status: str | None = None

@dataclass
class CoverageReport:
    eligible_round_count: int
    total_round_count: int
    included_rounds: list[dict[str, object]]
    gaps: list[CoverageGap]
    coverage_insufficient: bool
    single_round_only: bool
    missing_round_count: int

def build_coverage_report(...) -> CoverageReport: ...
def is_comprehensive_version_stale(version, *, rounds_by_id, analyses_by_round, transcripts_by_round) -> bool: ...
# 真源：任一 round_ref.analysis_version_id != analysis.current_version_id
#    或 is_analysis_version_stale(V, transcript)
#    或 round 缺失/跨应聘

async def request_comprehensive_analysis_generation(
    session, *, application_id: UUID, idempotency_key: str, actor: User, request_context: RequestContext
) -> AITask:
    # 1. load application；非 in_progress 或 pipeline!=interviewing → State/Validation
    # 2. manage-only（无 manage → Forbidden）
    # 3. build_coverage_report + collect eligible round_refs（仅结构化元数据）
    # 4. eligible_round_count < 1 → Validation（文案含 analysis/coverage）
    # 5. hash input；idempotency；inflight 同 hash 复用 / 异 hash Conflict
    # 6. INSERT AITask PENDING：task_type=COMPREHENSIVE；business_type=application；
    #    input_snapshot 含 round_refs + coverage_report + workflow 元数据；禁 FORBIDDEN 键
    # 7. audit generate_requested；return task（不 enqueue）

async def dispatch_persisted_comprehensive_analysis_task(session, *, task_id: UUID) -> None:
    # re-read PENDING + COMPREHENSIVE → enqueue_sensitive_interview_ai_task(task.id)

async def persist_comprehensive_analysis_result(
    session, *, task_id: UUID, payload: dict, actor: User | None = None, request_context: RequestContext | None = None
) -> ApplicationComprehensiveAnalysisVersion:
    # 幂等：已有 version by ai_task_id → return
    # 加密 overall_summary；写 version；推进 current_version_id
    # coverage_report 以 snapshot 服务端权威为准（忽略模型改写 gaps）
    # 禁止改 pipeline/status/lock_version；禁止 HiringDecision

async def list_comprehensive_analysis(session, *, application_id: UUID, actor: User) -> ...: ...
async def get_comprehensive_analysis_version_detail(session, *, application_id: UUID, version_id: UUID, actor: User) -> ...: ...
```

`round_refs` 单项白名单字段：`round_id`、`sequence_no`、`analysis_version_id`、`analysis_version_no`、`overall_score`、`dimensions[{dimension_key,dimension_name,weight,score,insufficient_information:bool}]`、`evidence_refs[{dimension_key,segment_no,transcript_segment_id}]`。

覆盖规则（锁定）：

- `single_round_only = (eligible_round_count == 1)`
- `coverage_insufficient = (eligible < total) or (len(gaps)>0) or (eligible==1 and total>=2)`
- 仅 1 轮且 total==1：`coverage_insufficient` 可为 false，但 **`single_round_only` 必须 true**（规格：二者至少其一显式表达边界）

### RED

| 测试函数 | 预期失败 / GREEN 断言 |
|---|---|
| `test_rejects_pending_offer_generate` | 无门禁 → 误成功；绿：State/Validation；零 AITask |
| `test_rejects_non_in_progress` | 同上 |
| `test_rejects_zero_eligible_rounds` | 全缺口 → 拒绝；文案匹配 `(?i)(analysis|coverage)` |
| `test_allows_single_eligible_round_with_single_round_only` | 1 合格轮创建 PENDING；snapshot.`coverage_report.single_round_only is True` |
| `test_gaps_enumerate_cancelled_without_transcript_none_stale` | gaps 含对应 `reason_code` |
| `test_round_refs_forbid_transcript_and_jd_keys` | 故意注入禁键的 builder 须抛错；正常路径 snapshot 递归无禁键 |
| `test_inflight_same_hash_reuses_task` | 第二次同 hash 返回同 task id |
| `test_inflight_different_hash_conflicts` | 409/Conflict |
| `test_idempotency_same_key_same_hash_returns_existing` | 复用 |
| `test_is_comprehensive_version_stale_when_round_current_moves` | current 指针变化 → True |
| `test_is_comprehensive_version_stale_when_transcript_confirmed_moves` | 转写确认指针变化 → True（经 `is_analysis_version_stale`） |
| `test_persist_does_not_mutate_pipeline_or_hiring_decision` | persist 前后 `pipeline_status`/`status`/`lock_version` 不变；`HiringDecision` 计数不变 |
| `test_persist_is_idempotent_on_ai_task_id` | 二次 persist 同 version id |
| `test_audit_generate_requested_has_no_sensitive_keys` | changes ⊆ 允许键；无 summary/quote/text |
| `test_execute_forbidden_on_request` | 无 manage → Forbidden（服务层） |

### GREEN 实现要点

1. Repository CRUD。
2. `build_coverage_report` + 合格轮收集（COMPLETED ∧ current ∧ ¬stale ∧ 非取消）。
3. `request_*` / `dispatch_*` / `persist_*` / list/detail + 动态 STALE。
4. 审计走 `record_audit`；敏感键被 `SENSITIVE_AUDIT_KEYS` 拦截的不得写入。
5. **本 Task 可不接线 worker persist 调用点**（Task 3 接线）；可提供纯函数/服务供测。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/services/test_comprehensive_analyses.py -q --tb=short
```

### 禁止范围

- 禁止 HTTP 路由、前端、`.env`、Dify YAML。
- 禁止改 HiringDecision / 单轮分析门禁语义。
- 禁止 enqueue 到默认 `process_ai_task`。
- 禁止启动 worker、触碰受保护 task。

### 提交边界（仅当用户明确要求）

repository/service/schema + `test_comprehensive_analyses.py`。禁止 endpoint、前端；worker persist 接线归 Task 3。本 Task 源码不得留下未完成占位注释；未接线则测试不依赖 worker。

---

## Task 3 — AI task、敏感队列与 mock

**Consumes：** Task 1–2；规格 §4.3–4.4、§2 敏感扩展。
**Produces：** 综合任务全程 `ai_sensitive`；强制 mock；worker persist 接线；脱敏回归。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/services/ai_providers/base.py` | `validate_ai_result` 支持综合（Pydantic 结果模型） |
| `backend/app/schemas/interview_ai.py`（或新建 `comprehensive_ai.py`） | **新增** `InterviewComprehensiveAnalyzeResult`（`overall_summary`、可选 `overall_score`；**无** gaps 权威字段） |
| `backend/app/services/ai_providers/mock.py` | `mock_interview_comprehensive_analyze` + `run_mock` 分支 |
| `backend/app/services/ai_providers/dify.py` | `run_dify`：综合 **无条件** `run_mock`；**禁止**新增 live Key/开关分支 |
| `backend/app/workers/ai_tasks.py` | 加载 provider 输入（仅用 snapshot round_refs，**不**解密转写）；成功调用 `persist_comprehensive_analysis_result`；失败路径脱敏；重试走敏感 |
| `backend/app/services/ai_tasks.py` | `retry_ai_task`：综合 ∈ 敏感 → `enqueue_sensitive_interview_ai_task`（若已按白名单统一则可只补测试） |
| `backend/app/core/config.py` | **禁止**新增综合 Dify Key/live 字段；若触达 `dify_api_key_for`，综合不得返回专用 Key 路径（保持非 live） |
| `backend/tests/workers/test_sensitive_ai_queue.py` | 追加综合白名单/转投/重试用例 |
| `backend/tests/workers/test_comprehensive_sensitive_mock_e2e.py` | **新建** 敏感路径 mock e2e（对齐 `test_analysis_sensitive_mock_e2e.py`） |
| `backend/tests/services/test_comprehensive_provider_contracts.py` | **新建** validate/mock 契约 + 禁键 |

### 精确行为

```python
# dify.run_dify
if task_type == TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE:
    return await run_mock(task_type=task_type, input_snapshot=input_snapshot)

# worker：综合 provider 输入 = task.input_snapshot 的 round_refs/dimensions 元数据
# 禁止调用 load_analysis_provider_input（单轮转写解密）

# _maybe_reroute_sensitive_from_default：task_type in SENSITIVE_AI_TASK_TYPES（已含综合）
# _enqueue_retry_for_task：敏感类型 → process_sensitive_ai_task.apply_async
```

### RED

| 测试函数 | 预期失败 / GREEN 断言 |
|---|---|
| `test_sensitive_whitelist_includes_comprehensive` | 更新原「恰二元」测为恰三元 |
| `test_process_sensitive_allows_comprehensive` | 综合 → `_process_ai_task_async` 一次 |
| `test_default_entry_reroutes_comprehensive` | 默认入口返回 `rerouted` + `interview_ai_requires_sensitive_queue` |
| `test_retry_comprehensive_uses_sensitive_enqueue` | admin/自动 retry 调用敏感入队，非 `process_ai_task` |
| `test_run_dify_comprehensive_never_http` | mock 路径；无 httpx/请求调用 |
| `test_mock_comprehensive_output_validates` | `validate_ai_result` 通过 |
| `test_sensitive_path_comprehensive_no_plaintext_in_public_payload` | attempt 公共 JSONB 无转写/摘要明文；provider=mock |
| `test_dispatch_uses_enqueue_sensitive_interview_ai_task` | `dispatch_persisted_comprehensive_analysis_task` 源码/行为指向敏感入队 |
| `test_config_has_no_comprehensive_dify_live_settings` | Settings 无 `DIFY_INTERVIEW_COMPREHENSIVE_*` / `*_LIVE_ENABLED` 字段 |

### GREEN 实现要点

1. 结果模型 + mock + `run_dify` 短路。
2. worker 分支 persist；输入仅结构化。
3. 确认转投/重试/白名单三元。
4. **零** `.env` / YAML 改动。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/workers/test_sensitive_ai_queue.py tests/workers/test_comprehensive_sensitive_mock_e2e.py tests/services/test_comprehensive_provider_contracts.py tests/services/test_comprehensive_analyses.py -q --tb=short -k "comprehensive or sensitive_ai_task_types or process_sensitive_allows or reroute or retry_comprehensive or run_dify_comprehensive or no_plaintext or no_comprehensive_dify"
```

### 禁止范围

- 禁止 Dify live、YAML、密钥、新增 live 配置项。
- 禁止默认队列执行综合。
- 禁止启动常驻 worker 做 UAT；禁止触碰受保护 task / 默认队列消息。
- 禁止前端（Task 4）。

### 提交边界（仅当用户明确要求）

provider/worker/ai_tasks 接线 + 上表测试。禁止 `.env`、YAML、frontend。

---

## Task 4 — API 与前端

**Consumes：** Task 2–3；规格 §7、§6.1。
**Produces：** manage-only HTTP；候选人中心 manage 综合面板；execute 403 且无 UI。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/schemas/comprehensive_analysis_api.py` | **新建** GenerateRequest/Out、SetOut、VersionDetailOut（含 coverage、`is_current`/`is_stale`） |
| `backend/app/api/v1/endpoints/comprehensive_analyses.py` | **新建** 三路由；`require_permission("recruitment.manage")`；commit-then-dispatch 对齐单轮 |
| `backend/app/api/v1/router.py` | `include_router` |
| `backend/tests/api/v1/test_comprehensive_analyses.py` | **新建** 权限/门禁/pending_offer/202 |
| `frontend/src/api/comprehensiveAnalysis.ts` | **新建** 类型与 `generate` / `getSet` / `getVersion` |
| `frontend/src/views/CandidateCenterDetailView.vue` | manage 区综合面板：状态、覆盖报告、STALE、版本历史、生成按钮（仅 interviewing） |
| `frontend/tests/comprehensiveAnalysis.spec.ts` | **新建** API 客户端路径断言 |
| `frontend/tests/CandidateCenterDetailView.spec.ts` | 追加综合面板用例；更新 execute 边界 |
| `frontend/tests/InterviewTimelineView.spec.ts` | 保持 execute **无**综合；若 manage 时间轴不挂入口可不改；禁止对 execute 露出「综合分析」操作 |

### 精确 API

```text
POST /api/v1/applications/{application_id}/comprehensive-analysis/generate
  body: { "idempotency_key": "<string>" }
  202: { task_id, task_type, status, application_id|business_id, dispatch_status }

GET  /api/v1/applications/{application_id}/comprehensive-analysis
  200: { analysis_id, application_id, current_version_id, versions: [... coverage, is_current, is_stale ...] }

GET  /api/v1/applications/{application_id}/comprehensive-analysis/versions/{version_id}
  200 + Cache-Control: no-store
  detail: overall_summary（解密后仅 manage）、coverage_report、round_refs（无长文本）、is_stale
```

错误：非 manage → 403；`pending_offer` POST → 400/409；0 合格 → 400/409；inflight → 409。

### 前端数据结构（锁定）

```ts
export interface CoverageGap {
  round_id: string
  sequence_no: number | null
  reason_code: string
  status?: string | null
}
export interface CoverageReport {
  eligible_round_count: number
  total_round_count: number
  included_rounds: Array<{
    round_id: string
    sequence_no: number
    analysis_version_id: string
    overall_score?: number | string | null
  }>
  gaps: CoverageGap[]
  coverage_insufficient: boolean
  single_round_only: boolean
  missing_round_count: number
}
```

UI 规则：

- `data-test="comprehensive-analysis-panel"` 仅 `canManage`。
- interviewing：显示生成；`pending_offer`：隐藏生成、可刷新只读历史。
- 展示 `single_round_only` / gaps / stale 标签。
- 文案含「综合分析（辅助）」；**禁止**「发送 Offer」「自动决策」「Dify」「通知候选人」。
- **不得**在该面板调用 `createHiringDecision` 或把综合 version id 填入 hiring `analysis_version_id`。

### RED

| 测试 | 预期失败 / GREEN 断言 |
|---|---|
| `test_generate_requires_manage` | execute → 403 |
| `test_get_requires_manage` | execute → 403 |
| `test_generate_rejects_pending_offer` | 409/400 |
| `test_generate_accepted_for_interviewing` | 202；task_type 综合 |
| `test_detail_sets_no_store` | Header `no-store` |
| 前端 `shows comprehensive panel for manage interviewing` | 存在 panel + generate |
| 前端 `hides generate on pending_offer but allows read` | 无 generate；可有历史 |
| 前端 `hides comprehensive panel for execute` | 无 panel |
| 前端 `forbids offer automation copy in comprehensive panel` | 无 Offer/自动决策/Dify/通知发送文案 |
| `comprehensiveAnalysis.spec.ts` | POST/GET 路径正确；body 仅 `idempotency_key` |

### GREEN 实现要点

1. Schema + endpoint + router。
2. 前端 API + 候选人中心 manage 面板。
3. Vitest 更新 execute/manage 边界（原「无多轮综合」对 execute 保持；manage 允许「综合分析」辅助文案）。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/api/v1/test_comprehensive_analyses.py -q --tb=short

cd frontend
pnpm vitest run tests/comprehensiveAnalysis.spec.ts tests/CandidateCenterDetailView.spec.ts tests/InterviewTimelineView.spec.ts
pnpm type-check
```

### 禁止范围

- 禁止 execute 可读综合。
- 禁止 Offer/通知/自动决策按钮。
- 禁止改 HiringDecision API。
- 禁止 `.env`、Dify、启动 worker。

### 提交边界（仅当用户明确要求）

endpoint/schema/router + 前端 API/视图/测试。禁止 worker 再改、禁止 `.env`。

---

## Task 5 — 回归、审查与 UAT runbook（只记录不执行 UAT）

**Consumes：** Task 1–4 全绿。
**Produces：** 回归命令清单 + 静态安全检查 + **UAT runbook（本文书写明、禁止在本 Task 执行）**。

### 5A — 自动化回归（实施时执行；本计划编写时不跑）

```text
cd backend
.venv\Scripts\python.exe -m pytest ^
  tests/models/test_comprehensive_analysis_constants.py ^
  tests/db/test_migration_015_comprehensive_analysis.py ^
  tests/services/test_comprehensive_analyses.py ^
  tests/services/test_comprehensive_provider_contracts.py ^
  tests/workers/test_sensitive_ai_queue.py ^
  tests/workers/test_comprehensive_sensitive_mock_e2e.py ^
  tests/api/v1/test_comprehensive_analyses.py ^
  tests/services/test_hiring_decisions.py ^
  tests/api/v1/test_hiring_decisions.py ^
  -q --tb=short

cd frontend
pnpm vitest run tests/comprehensiveAnalysis.spec.ts tests/CandidateCenterDetailView.spec.ts tests/InterviewTimelineView.spec.ts tests/InterviewAnalysisDrawer.spec.ts tests/hiringDecisions.spec.ts
pnpm type-check
```

### 5B — 静态安全审查（实施时执行）

```text
cd backend
.venv\Scripts\python.exe -c "from pathlib import Path
root=Path('app')
banned=('DIFY_INTERVIEW_COMPREHENSIVE','comprehensive_live','COMPREHENSIVE_LIVE')
bad=[]
for p in root.rglob('*.py'):
 t=p.read_text(encoding='utf-8')
 for b in banned:
  if b in t: bad.append((str(p),b))
print('ok' if not bad else bad)"

.venv\Scripts\python.exe -c "from pathlib import Path
# HiringDecision 不得引用综合版本表名作为 FK 目标
text=Path('app/models/resume.py').read_text(encoding='utf-8')
assert 'application_comprehensive_analysis' not in text
print('ok')"
```

手工清单（审查勾选）：

- [x] `SENSITIVE_AI_TASK_TYPES` 恰三元
- [x] `run_dify` 综合无条件 mock
- [x] generate 门禁 interviewing+in_progress；pending_offer 只读
- [x] 无 pipeline/HiringDecision 副作用
- [x] execute API 403；前端无综合 panel（execute）
- [x] snapshot/round_refs 无禁键
- [x] 未改 `.env` / Dify YAML
- [x] 未出现受保护 UUID 的 retry/cancel 脚本（仅文档禁令出现）
- [x] UAT runbook 已核对且标注「只记录、不执行」

### 5C — Windows mock UAT runbook（**只记录，禁止执行**）

> **状态标注：只记录、不执行。**
> 下列步骤供日后人工 UAT；**本 Task / 本计划执行窗口禁止启动 worker、禁止 generate、禁止 Redis 队列操作、禁止建数、禁止启动 API。**
> 本批验收约定：**仅 mock**（`AI_PROVIDER=mock`）；**零 Dify HTTP**。

1. **环境（文档约定，不在此执行）**
   - `AI_PROVIDER=mock`
   - Windows 敏感 worker **必须**同时满足：`--pool=solo --concurrency=1 --prefetch-multiplier=1`，且 **仅** `-Q ai_sensitive`：
     `celery -A app.workers.celery_app.celery_app worker -Q ai_sensitive --pool=solo --concurrency=1 --prefetch-multiplier=1`
   - **禁止** `-Q celery` 或 `-Q celery,ai_sensitive`。
   - **禁止**对默认队列 `purge` / 消费 / 窥探 / `llen` 操作默认 `celery` 队列消息。

2. **队列与 worker 检查（T0 / T1 / T2 — 仅未来 UAT 勾选，本窗口不跑）**
   - **T0（启动前）**：确认无常驻 worker；确认未对默认 `celery` 队列做任何读写；确认两条受保护 running 未被脚本引用。
   - **T1（仅敏感队列）**：若人工启动 worker，命令行参数必须含 `-Q ai_sensitive` 且 **不含** `celery`；solo/concurrency/prefetch 如上。
   - **T2（收尾）**：停止敏感 worker；**不** purge 默认队列；**不** SQL/Redis 干预受保护 task。

3. **夹具条件（虚构隔离应聘，不在此创建）**
   - A：`in_progress`+`interviewing`；1 轮 COMPLETED+确认转写+current 非 STALE 单轮分析。
   - B：A 的基础上加 CANCELLED / WITHOUT_TRANSCRIPT / stale 轮。
   - C：`pending_offer`；可选已有综合版本。
   - 账号：manage / execute-only。
   - **禁止**使用受保护 task id；**禁止**默认队列投递综合任务。

4. **验收期望（未来执行时勾选）**
   - manage 对 A/B：generate → 覆盖报告可见；`single_round_only` 或 gaps 正确；pipeline 不变；无新 HiringDecision。
   - C：GET 可读（有版本时）；POST 拒绝。
   - execute：综合 API 403；UI 无入口。
   - attempt/公开载荷无转写/JD/简历正文。

5. **硬禁**
   - 不 retry/cancel/mark-stale：
     `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
     `3556206d-138b-40f6-9b23-97fce178a32e`
   - 不 Dify live、不改 YAML/密钥、不发通知/Offer。
   - **本 Task 5 窗口：上述步骤一律不执行。**

### 禁止范围（Task 5）

- **禁止执行** §5C UAT。
- 禁止启动 worker、禁止触碰默认队列与受保护 running。
- 禁止把 runbook 当实施脚本自动跑。

### 提交边界（仅当用户明确要求）

若有测试微调/审查脚本，可单独提交；**不**提交 UAT 产物、日志、`.env`。默认本 Task 可无代码变更（仅确认回归绿）。

---

## 计划自检

### 规格逐节映射

| 规格 | 计划落点 |
|---|---|
| §1 范围/目标/非目标 | 全局约束 + 各 Task 禁止项 + Task 5 |
| §2 源码事实 | Task 1 常量；Task 2 差分 pipeline 门禁；Task 3 敏感扩展 |
| §3 模型/迁移 | Task 1 |
| §4 输入/mock/队列 | Task 2（输入）· Task 3（mock/队列） |
| §5 覆盖报告 | Task 2 · Task 4 UI |
| §6 状态机/STALE/副作用 | Task 2 · Task 4 pending_offer |
| §7 API/权限/前端 | Task 4 |
| §8 审计/幂等 | Task 2 · Task 3 e2e |
| §9 测试/UAT | Task 1–4 RED/GREEN + Task 5 |
| §10 范围外 | 全局约束 |
| §11 稳定符号 | 本计划稳定符号表 |
| §12 规格自检项 | 由 Task 5 审查清单覆盖 |

### 完成度勾选

- [x] 五任务 TDD：文件清单、接口/结构、RED、GREEN、验证命令、禁止范围、提交边界
- [x] 规格 §1–§12 均有映射；无悬空章节
- [x] **无** `TBD` / `TODO` 占位（接线未完成用「Task N 负责」表述）
- [x] **无**真实凭据、密钥、候选人/JD/转写/分析正文
- [x] UAT 仅 runbook；明确不执行；敏感 worker solo/concurrency=1/prefetch=1；禁默认队列与两条 running
- [x] 本文件仅计划；未改业务代码、未提交、未执行 UAT
