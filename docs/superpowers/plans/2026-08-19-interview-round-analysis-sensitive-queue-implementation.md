# 单轮分析进敏感队列 — TDD 实施计划

> **For agentic workers:** 按任务顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。  
> **Task 5 UAT runbook：只记录、禁止执行**（零 worker、零 generate、零 Dify、零触碰受保护 ID）。

**规格：** `docs/superpowers/specs/2026-08-19-interview-round-analysis-sensitive-queue-design.md`  
**基线：** `main` @ `0fea0bf`  
**方法：** TDD。符号名锁定为规格 §10；禁止临时改名。

## 全局约束

- **不**改 Dify workflow / YAML / 题纲 live 门禁 / 简历·JD Dify 回退；分析 **保持** `run_dify`→`run_mock`。
- **不**做 Alembic、不改表结构、不改前端、综合分析、人工决策、`.env` 真实值。
- **不**新增按任意 `ai_task_id` 执行的 HTTP/CLI。
- **不**触碰、retry、cancel、mark-stale、SQL/Redis 干预：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- **不**削弱转写确认门禁、STALE 动态判定、`persist_failed`、`_reassert_running_ownership_for_terminal`。
- 自动化：**零**真实 Dify HTTP；**不**启动常驻/全量 worker。
- 本计划各任务 **默认不提交**。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| 白名单真源 | `app/models/ai_task.py` 内 **`SENSITIVE_AI_TASK_TYPES`** |
| 统一入队 | **`enqueue_sensitive_interview_ai_task(task_id: UUID, *, countdown: int = 0) -> None`** |
| 题纲兼容别名 | **`enqueue_sensitive_question_task(task_id: UUID, *, countdown: int = 0) -> None`** → 调用统一入队（参数表必须一致） |
| 敏感 Celery `name=` | `app.workers.ai_tasks.process_sensitive_ai_task` |
| 默认 Celery `name=` | `app.workers.ai_tasks.process_ai_task` |
| 转投助手 | **`_maybe_reroute_sensitive_from_default(task_id: UUID) -> dict \| None`**（由 `_maybe_reroute_question_from_default` **重命名**；禁止长期双名并存） |
| 自动重试 | `_enqueue_retry_for_task(task: AITask, *, countdown: int) -> None` |
| 分析 dispatch | `dispatch_persisted_analysis_generation_task(session: AsyncSession, *, task_id: UUID) -> None` |
| 转投 `reason` | **`interview_ai_requires_sensitive_queue`**（全仓库唯一合法转投 reason） |
| 旧 reason（禁止残留） | `question_generate_requires_sensitive_queue`（GREEN 后 **零** 出现于 `backend/`） |
| 拒绝 `reason` | `unsupported_task_type` |
| 转投失败审计 | `action=ai_task.sensitive_reroute_failed`；`changes` ⊆ `{ai_task_id, task_type, error_type}` |
| `TaskType` 精确六值 | 见 Task 4 |
| Windows UAT | `--pool=solo --concurrency=1 --prefetch-multiplier=1`（**仅文档**） |

## 规格覆盖映射

| 规格章节 | 本计划 Task |
|---|---|
| §3.1 不新建 Celery 任务 | Task 1（复用既有入口） |
| §3.2 敏感入口白名单 | Task 1 |
| §3.3 默认转投 + 统一 reason + 审计 | Task 2 |
| §3.4 自动重试 | Task 3 |
| §4.1–4.2 dispatch + 统一 enqueue + 管理重试 | Task 3 |
| §5 `TaskType` Literal + 序列化 | Task 4 |
| §6 门禁/STALE/persist/ownership/mock 不变 | Task 5（自动化回归） |
| §7 / §9.2 Windows UAT | Task 5（**runbook 只记录、禁止执行**） |
| §1.3 / §4.3 非目标与受保护 ID | 全局约束 + 各 Task 禁止项 |

---

## Task 1 — `SENSITIVE_AI_TASK_TYPES` + 敏感入口只允两类

**Consumes：** 规格 §3.1–§3.2、§10。  
**Produces：** 单一真源白名单；敏感入口允题纲与分析；拒其它；**不**回投默认队列。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/models/ai_task.py` | **新增** `SENSITIVE_AI_TASK_TYPES`（紧挨 `TASK_TYPES`） |
| `backend/app/workers/ai_tasks.py` | import 该常量；改 `_process_sensitive_ai_task_async` 门禁；**禁止**改 `_handle_process` persist/脱敏 |
| `backend/tests/workers/test_sensitive_ai_queue.py` | 追加/改写下列 RED 用例；更新既有 `test_process_sensitive_rejects_non_question_without_handle` 为白名单语义 |

### 精确签名

```python
# app/models/ai_task.py
SENSITIVE_AI_TASK_TYPES: frozenset[str] = frozenset(
    {
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,  # "INTERVIEW_QUESTION_GENERATE"
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,      # "INTERVIEW_ROUND_ANALYZE"
    }
)

# app/workers/ai_tasks.py（既有符号，行为变更）
async def _process_sensitive_ai_task_async(task_id: UUID) -> dict: ...
# 若 task is None → {"status": "missing"}（保持既有）
# 若 task.task_type in SENSITIVE_AI_TASK_TYPES → return await _process_ai_task_async(task_id)
# 否则 → {"status": "rejected", "reason": "unsupported_task_type", "task_type": task.task_type}
# 函数体内禁止 process_ai_task.apply_async
```

### RED

| 测试函数（写入 `test_sensitive_ai_queue.py`） | 精确断言 |
|---|---|
| `test_sensitive_ai_task_types_exactly_question_and_analyze` | `SENSITIVE_AI_TASK_TYPES == {TASK_TYPE_INTERVIEW_QUESTION_GENERATE, TASK_TYPE_INTERVIEW_ROUND_ANALYZE}`；`len==2` |
| `test_process_sensitive_allows_interview_round_analyze` | fake ANALYZE；`_process_ai_task_async` 恰一次；返回透传 |
| `test_process_sensitive_still_allows_question_generate` | 题纲；`_process_ai_task_async` 恰一次 |
| `test_process_sensitive_rejects_non_whitelist_without_handle` | `RESUME_SCORE` → `rejected` + `unsupported_task_type`；零 handle/dify/mock |
| `test_process_sensitive_never_requeues_default_process_ai_task` | `_process_sensitive_ai_task_async` 源码不含 `process_ai_task.apply_async`；既有 back-to-default 测保持绿 |

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/workers/test_sensitive_ai_queue.py -q --tb=short -k "sensitive_ai_task_types or process_sensitive_allows or process_sensitive_still or process_sensitive_rejects or never_requeues or never_apply_async_back"
```

- RED：缺常量 / 分析仍 rejected。  
- GREEN：同上命令全绿。

### GREEN 步骤

1. 在 `models/ai_task.py` 定义 `SENSITIVE_AI_TASK_TYPES`。
2. worker：`if task.task_type not in SENSITIVE_AI_TASK_TYPES: rejected`。
3. 不改 Celery 注册名、不新建队列。

**提交边界（仅当用户明确要求）：** 上表三文件。禁止 `.env`。

---

## Task 2 — 默认入口转投 + 统一 reason 全调用点

**Consumes：** Task 1；规格 §3.3。  
**Produces：** 题纲与分析均转投；reason 全仓库统一；失败审计不变。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/workers/ai_tasks.py` | `_maybe_reroute_question_from_default` **重命名**为 `_maybe_reroute_sensitive_from_default`；条件 `in SENSITIVE_AI_TASK_TYPES`；**两处**返回 `reason` 改新串；`_process_default_ai_task_async` 调新名 |
| `backend/tests/workers/test_sensitive_ai_queue.py` | 改旧 reason 断言；新增分析转投用例 |

### 精确签名

```python
async def _maybe_reroute_sensitive_from_default(task_id: UUID) -> dict | None:
    # 成功: {"status":"rerouted","reason":"interview_ai_requires_sensitive_queue","task_id":str(task_id)}
    # 失败: {"status":"reroute_failed","reason":"interview_ai_requires_sensitive_queue","task_id":str(task_id),"error_type":...}
    # apply_async(args=[str(task_id)], countdown=0) 恰好一次；禁止 claim / services.enqueue_* / 手写 queue=

async def _process_default_ai_task_async(task_id: UUID) -> dict:
    rerouted = await _maybe_reroute_sensitive_from_default(task_id)
    if rerouted is not None:
        return rerouted
    return await _process_ai_task_async(task_id)
```

### 统一 reason — 全部调用点清单（GREEN 后必须满足）

| # | 位置 | 要求 |
|---|---|---|
| R1 | `workers/ai_tasks.py` 转投成功返回 dict | `"reason": "interview_ai_requires_sensitive_queue"` |
| R2 | `workers/ai_tasks.py` 转投失败返回 dict | 同上 |
| R3 | `tests/workers/test_sensitive_ai_queue.py` 全部转投断言 | 断言新 reason |
| R4 | 全 `backend/` 文本检索 | **零** `question_generate_requires_sensitive_queue` |

```text
cd backend
.venv\Scripts\python.exe -c "from pathlib import Path; old='question_generate_requires_sensitive_queue'; hits=[str(p) for p in Path('.').rglob('*') if p.is_file() and p.suffix in {'.py','.md','.txt','.toml'} and old in p.read_text(encoding='utf-8', errors='ignore')]; print(hits or 'OK_ZERO')"
```

期望：`OK_ZERO`。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_default_entry_reroutes_analyze_once` | ANALYZE；`rerouted` + 新 reason；sensitive `apply_async` 一次；零 `_handle_process`；未 claim |
| `test_default_entry_reroutes_question_with_unified_reason` | 题纲；新 reason；断言不含旧 reason |
| `test_default_entry_reroute_failed_audits_without_claim` | `reroute_failed` + 新 reason；审计 `ai_task.sensitive_reroute_failed`；changes ⊆ 三键；`actor_user_id is None` |
| `test_default_entry_non_sensitive_still_processes` | `RESUME_SCORE` → 进入 `_process_ai_task_async` |
| `test_repo_has_no_legacy_question_reroute_reason` | worker + 本测试文件文本无旧 reason（GREEN 后必过） |

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/workers/test_sensitive_ai_queue.py -q --tb=short -k "reroutes_analyze or unified_reason or reroute_failed or non_sensitive_still or legacy_question_reroute"
```

GREEN 另跑 reason 清零检索。

### GREEN 步骤

1. 重命名转投助手；条件用白名单。  
2. R1/R2 同步替换 reason。  
3. 更新全部测试；无旧函数名残留。

**提交边界（仅当用户明确要求）：** 上表两文件。

---

## Task 3 — 统一 enqueue + 分析 dispatch + 自动/管理重试

**Consumes：** Task 1–2；规格 §3.4、§4。  
**Produces：** 通用入队 + 题纲兼容别名；分析 dispatch；两类自动/管理重试均敏感。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/services/ai_tasks.py` | 新增统一入队；别名委托；`retry_ai_task` 用白名单 |
| `backend/app/services/interview_analyses.py` | dispatch：`enqueue_ai_task` → `enqueue_sensitive_interview_ai_task` |
| `backend/app/services/interview_questions.py` | 可继续别名或改统一名；须仍敏感入队 |
| `backend/app/workers/ai_tasks.py` | `_enqueue_retry_for_task` 用 `SENSITIVE_AI_TASK_TYPES` |
| `backend/tests/workers/test_sensitive_ai_queue.py` | 追加 enqueue/retry 用例 |
| `backend/tests/services/test_interview_analyses.py` | dispatch 断言 |
| `backend/tests/services/test_interview_questions.py` | 源码/行为断言对齐 |
| `backend/tests/services/test_mark_stale_failed_ai_task.py` | patch 别名仍可用 |

### 精确签名（入队 — 与题纲兼容）

```python
def enqueue_sensitive_interview_ai_task(
    task_id: UUID, *, countdown: int = 0
) -> None:
    from app.workers.ai_tasks import process_sensitive_ai_task
    process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=countdown)


def enqueue_sensitive_question_task(
    task_id: UUID, *, countdown: int = 0
) -> None:
    """Backward-compatible alias; MUST single-delegate (no second apply_async copy)."""
    enqueue_sensitive_interview_ai_task(task_id, countdown=countdown)


async def dispatch_persisted_analysis_generation_task(
    session: AsyncSession, *, task_id: UUID
) -> None:
    # 既有 PENDING+ANALYZE 校验不变
    # enqueue_sensitive_interview_ai_task(task.id)  # 禁止 enqueue_ai_task


def _enqueue_retry_for_task(task: AITask, *, countdown: int) -> None:
    if task.task_type in SENSITIVE_AI_TASK_TYPES:
        process_sensitive_ai_task.apply_async(args=[str(task.id)], countdown=countdown)
    else:
        process_ai_task.apply_async(args=[str(task.id)], countdown=countdown)


async def retry_ai_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    actor: User,
    request_context: RequestContext,
) -> AITaskSummaryOut:
    # commit 后：
    #   SENSITIVE_AI_TASK_TYPES → enqueue_sensitive_interview_ai_task(task.id)
    #   else → enqueue_ai_task(task.id)
```

别名兼容：位置参数 `task_id` + 仅关键字 `countdown=0` 与旧签名一致；`inspect.signature` 两函数相等。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_enqueue_sensitive_interview_ai_task_targets_sensitive_celery` | `apply_async(args=[str(tid)], countdown=7)` |
| `test_enqueue_sensitive_question_task_is_compatible_alias` | 别名 → 统一函数恰一次且 countdown 透传 |
| `test_enqueue_sensitive_question_task_signature_matches_interview` | `inspect.signature` 相等 |
| `test_dispatch_analysis_uses_sensitive_enqueue_not_default` | 源码含统一入队、不含 `enqueue_ai_task`；行为 patch 命中 |
| `test_dispatch_question_still_sensitive` | 题纲仍敏感 |
| `test_enqueue_retry_analyze_uses_sensitive` | ANALYZE → 仅 sensitive apply_async |
| `test_enqueue_retry_question_still_sensitive` | 题纲 → sensitive |
| `test_enqueue_retry_non_sensitive_uses_default` | RESUME_SCORE → default |
| `test_retry_ai_task_analyze_enqueues_sensitive` | failed ANALYZE retry → 统一敏感入队 |
| `test_retry_ai_task_question_enqueues_sensitive` | 题纲 failed → 敏感 |
| `test_worker_module_has_no_toplevel_services_ai_tasks_import` | 既有测保持绿 |

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/workers/test_sensitive_ai_queue.py tests/services/test_interview_analyses.py tests/services/test_interview_questions.py tests/services/test_mark_stale_failed_ai_task.py -q --tb=short -k "enqueue_sensitive_interview or compatible_alias or signature_matches or dispatch_analysis or dispatch_question_still or enqueue_retry or retry_ai_task_analyze or retry_ai_task_question or no_toplevel_services"
```

### GREEN 步骤

1. 实现统一入队 + 别名单行委托。  
2. 分析 dispatch / retry / `_enqueue_retry_for_task` 切白名单。  
3. 更新 patch 点。

**提交边界（仅当用户明确要求）：** 上表允许文件。

---

## Task 4 — `TaskType` 六精确值 + cancel/retry HTTP 200

**Consumes：** 规格 §5。  
**Produces：** Literal 六值 == `TASK_TYPES`；admin cancel/retry 对题纲/分析 **HTTP 200** 且 body `task_type` 正确。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/schemas/ai_task.py` | 仅扩展 `TaskType` |
| `backend/tests/schemas/test_ai_task_schema.py` | **新建** |
| `backend/tests/api/v1/test_admin_ai_tasks.py` | 追加 200 断言 |
| `backend/tests/models/test_interview_ai_models.py` | 对齐集合（若已有） |

**禁止：** 改 `MarkStaleFailedAITaskOut`；真实受保护 UUID；Dify/迁移/前端。

### 精确六值

```python
TaskType = Literal[
    "JD_PARSE",
    "SCORE_DIMENSION_RECOMMEND",
    "RESUME_PARSE",
    "RESUME_SCORE",
    "INTERVIEW_QUESTION_GENERATE",
    "INTERVIEW_ROUND_ANALYZE",
]
# set(get_args(TaskType)) == TASK_TYPES == 上表六字符串集合
```

既有端点（不改路径）：

- `POST /api/v1/admin/ai-tasks/{task_id}/cancel` → `AITaskAdminDetailOut`
- `POST /api/v1/admin/ai-tasks/{task_id}/retry` → `AITaskAdminDetailOut`

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_task_type_literal_six_exact_values` | 集合恰六值（上表） |
| `test_task_type_literal_matches_orm_task_types` | Literal == `TASK_TYPES` |
| `test_ai_task_summary_out_accepts_question_and_analyze` | Summary 两类型 validate 成功 |
| `test_ai_task_admin_detail_out_accepts_question_and_analyze` | AdminDetail/ListItem 两类型 validate 成功 |
| `test_admin_cancel_pending_question_returns_200_with_task_type` | pending 题纲；`POST .../cancel` → **`status_code == 200`**；`body["task_type"]=="INTERVIEW_QUESTION_GENERATE"`；`body["status"]=="cancelled"`；无 ValidationError |
| `test_admin_retry_failed_analyze_returns_200_with_task_type` | failed/output_invalid ANALYZE；patch 敏感入队；`POST .../retry` → **`status_code == 200`**；`body["task_type"]=="INTERVIEW_ROUND_ANALYZE"`；`body["status"]=="pending"` |
| `test_mark_stale_out_still_omits_task_type` | `"task_type" not in MarkStaleFailedAITaskOut.model_fields` |

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/schemas/test_ai_task_schema.py tests/api/v1/test_admin_ai_tasks.py tests/models/test_interview_ai_models.py -q --tb=short -k "task_type_literal or summary_out_accepts or admin_detail_out_accepts or cancel_pending_question_returns_200 or retry_failed_analyze_returns_200 or mark_stale_out_still"
```

### GREEN 步骤

1. 扩展 Literal。  
2. 可选清理 `# type: ignore[arg-type]`。  
3. API 测仅用合成 UUID。

**提交边界（仅当用户明确要求）：** 上表允许文件。

---

## Task 5 — 不变性自动化回归 + UAT runbook（只记录、禁止执行）

**Consumes：** Task 1–4；规格 §6、§7、§9。  
**Produces：**（A）自动化回归；（B）Windows solo UAT **仅文档**。

### 硬性禁令

| 禁止 | 说明 |
|---|---|
| **禁止执行 UAT runbook** | 不得启 celery、不得 analysis/generate、不得调 Dify、不得写 `recruit`、不得碰两条 running |
| 禁止改生产 persist/ownership/门禁 | 仅加测试 |
| 禁止 ANALYZE live | `run_dify` 保持 mock |

### 具体文件（仅自动化可改）

| 路径 | 动作 |
|---|---|
| `backend/tests/workers/test_analysis_sensitive_mock_e2e.py` | **新建** |
| `backend/tests/services/test_interview_question_live_dify.py` | ANALYZE mock 加强（若缺） |
| `backend/tests/services/test_interview_analyses.py` | 门禁/STALE（若需） |
| `backend/tests/workers/test_ai_task_persist_failed.py` | 保持绿 |
| `backend/tests/workers/test_ai_task_terminal_ownership_txn.py` | 保持绿 |
| `backend/tests/workers/test_interview_ai_worker.py` | 保持绿 |

### RED → GREEN（允许 pytest；禁止 UAT）

| 测试函数 | 精确断言 |
|---|---|
| `test_run_dify_analyze_still_unconditional_mock` | ANALYZE → `run_mock` 一次；`_post_workflow` 零次 |
| `test_analysis_generate_rejects_without_confirmed_transcript` | 非确认转写 → ValidationError；零入队 |
| `test_analysis_stale_flag_still_dynamic` | 指针变化 → `is_stale is True` |
| `test_sensitive_path_analyze_mock_e2e_no_plaintext_in_public_payload` | 敏感路径 mock 成功；公开 JSONB 无正文 |
| `test_sensitive_path_analyze_redacts_stage8_public_fields` | 公开 payload 仅元数据键 |
| `test_analysis_audit_changes_pass_sanitize_audit_changes` | 审计 changes 可过 sanitize |

### 验证命令（自动化）

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/workers/test_analysis_sensitive_mock_e2e.py tests/services/test_interview_question_live_dify.py tests/services/test_interview_analyses.py tests/workers/test_ai_task_persist_failed.py tests/workers/test_ai_task_terminal_ownership_txn.py tests/workers/test_interview_ai_worker.py tests/workers/test_sensitive_ai_queue.py -q --tb=short
```

### Windows UAT runbook（只记录 — **禁止本 Task / 实施阶段执行**）

> 供未来人工授权。实施本计划的 agent：**读到即停止，不得执行下列 1–6 步。**

1. 确认无 worker；只读 `LLEN($SENSITIVE_Q)==0`；不碰 `dde1470f-…` / `3556206d-…`。  
2. 隔离轮次须已 COMPLETED + `CONFIRMED_TRANSCRIPT` + included≥1 + anchors/权重（**本阶段不 generate**）。  
3. 预期 T1：`pending` + `LLEN==1` + `INTERVIEW_ROUND_ANALYZE`。  
4. 预期命令（**本阶段不启动**）：

```text
celery -A app.workers.celery_app worker -Q <SENSITIVE_Q> -l info --pool=solo --concurrency=1 --prefetch-multiplier=1
```

5. 预期 mock 终态；零 Dify HTTP；停 worker。  
6. 预期只读确认受保护 running 未变。

**提交边界（仅当用户明确要求）：** 仅测试文件；禁止伪记录「已跑 UAT」。

---

## 全部 Task GREEN 后回归

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/workers/test_sensitive_ai_queue.py tests/workers/test_analysis_sensitive_mock_e2e.py tests/services/test_interview_analyses.py tests/services/test_interview_questions.py tests/schemas/test_ai_task_schema.py tests/api/v1/test_admin_ai_tasks.py tests/workers/test_ai_task_persist_failed.py tests/workers/test_ai_task_terminal_ownership_txn.py tests/workers/test_interview_ai_worker.py tests/services/test_interview_question_live_dify.py tests/services/test_mark_stale_failed_ai_task.py -q --tb=short
```

另跑 Task 2 旧 reason 清零检索。

---

## 自检清单（本修订）

- [x] 每 Task 含具体文件、精确签名、RED/GREEN、验证命令
- [x] `enqueue_sensitive_interview_ai_task(task_id, *, countdown=0)` 与题纲别名签名一致并单行委托
- [x] 转投 reason 调用点 R1–R4 + 旧 reason 清零检索
- [x] `TaskType` 六精确值；cancel/retry **HTTP 200** + `task_type` 断言
- [x] Task 5 UAT **只记录、禁止执行**
- [x] 无 TBD；规格覆盖映射完整
- [x] 不要求编码、commit、push
