# AsyncSession 持久化修复与 stale-running 恢复 — TDD 实施计划

> **For agentic workers:** 按任务顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。

**规格：** `docs/superpowers/specs/2026-08-19-async-persistence-stale-task-recovery-design.md`
**基线：** `main` @ `83cb473`（敏感队列已合入）。
**方法：** TDD。稳定符号与路径以规格 §5 / §9 为准，禁止临时改名或路径别名。
**计划修订：** 锁定 worker 竞态为 **唯一** `SELECT … FOR UPDATE` 帮助函数；mark-stale 成功响应用 **独立最小 Schema**（不复用含残缺 `TaskType` Literal 的 summary/detail）。

## 全局约束

- **不**改 Dify / live 门禁 / YAML / Celery `task_routes` / 敏感队列名 / `enqueue_*` 语义。
- **不**做 Alembic、不改表结构、不改前端、不改 `.env` 真实值。
- **不**新增按任意 ID「强制执行 worker」的 API/CLI；cancel 仍仅 `pending`。
- **不**处置、fixture、脚本、UAT 步骤中操作 `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`。
- **不**处理非目标 `running` `3556206d-138b-40f6-9b23-97fce178a32e`。
- **不**在本计划修复 `AITaskSummaryOut` / `AITaskAdminDetailOut` 的 `task_type` Literal（已知缺题纲/分析）；mark-stale **必须**避开复用这些 Schema，以免「DB 已更新、响应 ValidationError」。
- 自动化测试：**零**真实 Dify HTTP；**不**启动 Celery worker；**不**写开发库 `recruit`（AsyncSession 测用 `sqlite+aiosqlite:///:memory:` 或 `TEST_DATABASE_URL`/`recruit_test`，并拒绝 business DB 名）。
- 请求体 **禁止** `reason` / 自由文本；审计仅固定元数据 + `error_code=stale_running_recovered`。
- 本计划各任务 **默认不提交**。若用户另行授权 commit，仅包含该任务「允许提交」文件清单。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| 集合修复 | `sqlalchemy.orm.attributes.set_committed_value` |
| Worker 持久化失败 `error_code` | `persist_failed` |
| Worker 终态所有权帮助函数 | **`_reassert_running_ownership_for_terminal`**（worker 内部；唯一竞态实现） |
| 竞态实现方式 | **仅** 终态前重新 `SELECT … FOR UPDATE` 检查；**禁止**「条件 UPDATE 或 FOR UPDATE」二选一表述；**禁止**仅用无锁的条件 `UPDATE` 代替本帮助函数 |
| 管理员 Path | **`POST /api/v1/admin/ai-tasks/{task_id}/mark-stale-failed`** |
| 管理员 Body Schema | `MarkStaleFailedAITaskIn`：**仅** `expected_updated_at` |
| 管理员成功响应 Schema | **`MarkStaleFailedAITaskOut`**：字段 **仅** `id`、`status`、`error_code`、`updated_at`、`finished_at`（`id`=task UUID） |
| 禁止复用响应 | **禁止** `AITaskSummaryOut` / `AITaskOut` / `AITaskAdminDetailOut` / `AITaskAdminListItemOut` 作为 mark-stale 成功 `response_model` |
| 管理员 `error_code` | `stale_running_recovered` |
| 审计 `action` | `ai_task.stale_running_recovered` |
| 服务函数 | `mark_stale_failed_ai_task` |
| 年龄常量 | `STALE_RUNNING_MIN_AGE = timedelta(minutes=5)` |
| 竞态返回 | `skipped_stale_owner` |
| 权限 | `ai_task.manage` |
| P1 | `persist_question_generation_result`（`version.items = items`） |
| P2 | `create_manual_question_version`（同赋值） |
| P3 | `persist_analysis_generation_result`（`dim_row.evidence = …`；`analysis_version.dimensions = dim_rows`） |

## 规格映射

| 规格 | 本计划 |
|---|---|
| §2 根因 + P1/P2/P3 | Task 1 |
| §3 `set_committed_value` | Task 1 |
| §4 persist_failed 脱敏终态 | Task 2 |
| §6 worker 终态竞态 | Task 2（锁定 FOR UPDATE 帮助函数） |
| §5 mark-stale-failed | Task 3（路径/Body/5min/条件更新/审计/不入队；**响应 Schema 按本计划收紧**） |
| §7 真实 AsyncSession / 事务测 | Task 1–2 RED |
| §8 UAT 顺序（不含 dde1470f） | Task 4 |
| §12.2 `task_type` Literal 缺陷 | **不修**；Task 3 用独立 Out 规避 |

---

## Task 1 — P1/P2/P3：真实 AsyncSession RED/GREEN + `set_committed_value`

**Consumes：** 规格 §2–§3、§7.1（P1/P3）、§7.3。
**Produces：** 三条路径不再触发 `MissingGreenlet`；真实 AsyncSession 测试通过。

**允许改的文件：**

- `backend/app/services/interview_questions.py`（仅 P1 ~L910、P2 ~L1060 赋值及必要 import）
- `backend/app/services/interview_analyses.py`（仅 P3 ~L1073–L1078 赋值及必要 import）
- `backend/tests/services/test_async_session_collection_assign.py`（**新建**；真实 AsyncSession）
- `backend/tests/services/test_interview_questions.py`（若既有 mock 测因赋值方式失败则对齐 `set_committed_value`，不得削弱断言）
- `backend/tests/services/test_interview_analyses.py`（同上）
- `backend/pyproject.toml`（**仅当**缺 `aiosqlite` 时追加为测试/可选依赖；不得改业务依赖无关项）

**禁止：** worker / admin API / schemas / celery / dify / Alembic / `.env` / 前端；MagicMock-only「伪覆盖」；连接 `recruit`；启动 worker；调用 Dify。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_unloaded_collection_assign_raises_missing_greenlet` | 真实 `AsyncSession`（`sqlite+aiosqlite:///:memory:` + 最小 Version/Item 关系模型或生产模型子集 `create_all`）；`flush` 后对未加载集合执行 `parent.items = [child]` → 抛出 `sqlalchemy.exc.MissingGreenlet` |
| `test_persist_question_generation_result_async_session_survives_items_attach` | 真实 AsyncSession 上调用生产 `persist_question_generation_result`（或可复现到同一赋值语句的最小可调用封装）；**当前**（未 GREEN）期望失败：`MissingGreenlet` **或** pytest 因该异常失败；GREEN 后：无该异常，且 `version.items`（或 refresh 后）长度与写入 items 一致 |
| `test_create_manual_question_version_uses_set_committed_value_for_items` | **静态**：`interview_questions.py` 中 P2 赋值点 **不**含裸 `version.items =`；含 `set_committed_value(version, "items",`（RED 时失败因仍为 `=`） |
| `test_persist_analysis_generation_result_async_session_survives_collection_attach` | 真实 AsyncSession 调用 `persist_analysis_generation_result`（或触及 P3 两处赋值）；RED：`MissingGreenlet`；GREEN：无该异常，dimensions/evidence 可读取 |
| `test_p1_p2_p3_source_has_no_sync_collection_assign` | 静态扫描三处文件片段：禁止匹配 `version.items =`、`analysis_version.dimensions =`、`dim_row.evidence =`（允许仅出现在注释/字符串则测试须排除）；须出现对应 `set_committed_value(...)` |

DB 安全：fixture 断言 URL database ≠ `recruit`；内存 SQLite 或显式 `recruit_test`。

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/services/test_async_session_collection_assign.py -q --tb=short
```

期望：RED（P1/P3 仍抛 `MissingGreenlet`；静态仍见裸赋值）。

### GREEN

1. `from sqlalchemy.orm.attributes import set_committed_value`。
2. P1：`set_committed_value(version, "items", items)`。
3. P2：同上。
4. P3：`set_committed_value(dim_row, "evidence", [...])`；`set_committed_value(analysis_version, "dimensions", dim_rows)`。
5. **禁止**改用 sync `Session`、全局乱改 `expire_on_commit`、或仅改 `lazy="selectin"` 代替本修复。
6. 更新被裸赋值打断的既有 mock 测（若有）。

同上 pytest 全绿。可选补跑：

```text
.venv\Scripts\python.exe -m pytest tests/services/test_interview_questions.py tests/services/test_interview_analyses.py -q --tb=short -k "persist_question or persist_analysis or manual_question"
```

**提交边界（仅当用户明确要求）：** 上列允许文件。禁止 `.env`、禁止含 `dde1470f` 的脚本。

---

## Task 2 — Worker：`persist_failed` 脱敏终态 + `SELECT … FOR UPDATE` 终态所有权

**Consumes：** Task 1。规格 §4、§6。
**Produces：** `_handle_process` 非契约持久化异常 → `failed`+`persist_failed`；**唯一**竞态实现：`_reassert_running_ownership_for_terminal`；五类终态路径均调用；迟到 worker 不覆盖管理员 `failed`。

**允许改的文件：**

- `backend/app/workers/ai_tasks.py`
- `backend/tests/workers/test_interview_ai_worker.py`（追加）
- `backend/tests/workers/test_ai_task_persist_failed.py`（新建，推荐放 persist_failed + 所有权测）
- `backend/tests/workers/test_ai_task_terminal_ownership_txn.py`（**新建**；真实 AsyncSession 事务竞态）

**禁止：** 改 enqueue/路由/Dify；本任务 **不**实现 admin mark-stale HTTP（事务测可在同一测试内直接写 DB 模拟管理员 `failed`+`stale_running_recovered`）；写 `recruit`；启 worker；调 Dify；persist_failed 分支自动 enqueue；用「无锁条件 UPDATE」或「FOR UPDATE / 条件更新二选一」实现竞态。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_after_task_success_missing_greenlet_marks_failed_persist_failed` | 构造 running task+attempt；patch `_after_task_success`（或 persist）`side_effect=MissingGreenlet()`（或 `RuntimeError("orm boom")`）；跑 `_handle_process`；返回 `status == "failed"`；DB：`failed` / `persist_failed` / `non_retryable`；attempt 对齐；message 可含异常类名、**无**长正文；**零** `_enqueue_retry_for_task` / `apply_async` |
| `test_persist_failed_does_not_use_output_invalid` | `status != output_invalid`；非契约 error_code |
| `test_persist_failed_message_is_scrubbed` | 异常 `str` 含 `"SECRET_RESUME_BODY"` 时，DB/stage8 public **不含**该片段 |
| `test_stage8_output_invalid_path_unchanged` | 契约异常仍 `output_invalid`，不被新分支吞成 `persist_failed` |
| `test_reassert_running_ownership_helper_exists_and_used_by_all_terminal_paths` | 静态：`ai_tasks.py` worker 定义 `_reassert_running_ownership_for_terminal`；且 **success / output_invalid / provider failed / persist_failed / auto-retry→pending** 五处终态写入前均调用该符号（可用 AST/源码计数：调用次数 ≥5，或按分支标注断言） |
| `test_terminal_write_skips_when_task_no_longer_running` | 模拟 stale-recover 后 success 路径 → `skipped_stale_owner`；task 仍 `failed`+`stale_running_recovered`，非 `succeeded` |
| `test_terminal_write_skips_succeeded_overwrite_after_cancelled` | cancelled 不被覆盖成 succeeded（§6.2） |
| `test_late_worker_does_not_overwrite_admin_failed_real_txn` | **真实 AsyncSession 事务**（内存 aiosqlite 或 `recruit_test`，拒绝 `recruit`）：(1) 插入 `running` task + matching running attempt；(2) 第二连接/会话将 task（及 attempt）标为 `failed` + `error_code=stale_running_recovered` 并 commit；(3) 第一会话（或 worker 会话）调用 `_reassert_running_ownership_for_terminal`（或完整 success 终态路径）→ 返回/表现为 **非持有**（如 raise/返回 False/`skipped_stale_owner`）；(4) 再 commit 任何「拟成功」写入后，重读 DB：`status==failed`、`error_code==stale_running_recovered`，**绝非** `succeeded` |

```text
cd backend
.venv\Scripts\python.exe -m pytest ^
  tests/workers/test_interview_ai_worker.py ^
  tests/workers/test_ai_task_persist_failed.py ^
  tests/workers/test_ai_task_terminal_ownership_txn.py ^
  -q --tb=short -k "persist_failed or skipped_stale_owner or reassert_running or late_worker or output_invalid_path"
```

期望：RED。

### GREEN

1. **`persist_failed` 分支：** 在 `outcome.ok and outcome.result is not None` 的 `try` 中保留 `_STAGE8_OUTPUT_INVALID_EXCEPTIONS`；新增 `except Exception as exc:` → 先调用所有权帮助函数；若仍持有则写 `failed`/`persist_failed`/`non_retryable`/脱敏 message；Stage8 用 `persist_error_type=<ExcName>`，禁止冒充契约 `validation_error_code`；`commit`；返回 `{"status": "failed", "attempt_no": ...}`；**不** enqueue。若帮助函数判定非持有 → `skipped_stale_owner`，不覆盖。
2. **锁定帮助函数（唯一竞态实现）：**

```python
async def _reassert_running_ownership_for_terminal(
    session: AsyncSession,
    *,
    task_id: UUID,
    attempt_id: UUID,
) -> AITask | None:
    """SELECT … FOR UPDATE；仅当 task.status==running 且当前 attempt 仍为该 attempt_id 时返回锁定行，否则 None。"""
```

   - 实现必须 `select(AITask).where(AITask.id == task_id).with_for_update()`（及必要的 attempt 校验：当前 running attempt id 匹配）。
   - 返回 `None`（或明确非持有）时：安全日志（task_id、observed_status、error_type）；调用方返回 `{"status": "skipped_stale_owner", "observed_status": ...}`；**禁止**写成 `succeeded`。
   - **禁止**另写一套「无锁 WHERE status=running 的条件 UPDATE」作为竞态主路径。
3. **强制调用点（五类全部）：** 在写入并 `commit` 之前调用本帮助函数：
   - success → `succeeded`
   - output_invalid（契约 / validation）
   - provider failed（含最终 `failed`，非 auto-retry）
   - persist_failed
   - auto-retry → 回 `pending`（调用后才允许改 pending 并 `_enqueue_retry_for_task`；非持有则 **不** enqueue）
4. 既有 cancel late-response 分支保留；与帮助函数语义一致（非 running 不写成功）。
5. 不削弱既有脱敏 / Stage8 / 敏感队列测试。

同上 pytest 全绿。

**提交边界：** 上列允许文件。

---

## Task 3 — 管理员 `mark-stale-failed`：服务 / 独立 Schema / API / 权限 / 条件更新 / 审计

**Consumes：** Task 2（可选交叉）。规格 §5；**响应形状按本计划收紧**（规避 `TaskType` Literal 缺陷）。
**Produces：** 锁定路径；5 分钟 + `expected_updated_at` 条件更新；审计；零入队；成功响应 **`MarkStaleFailedAITaskOut`**（题纲类型 200 可序列化）。

**允许改的文件：**

- `backend/app/services/ai_tasks.py`（`mark_stale_failed_ai_task` + `STALE_RUNNING_MIN_AGE`；返回 `MarkStaleFailedAITaskOut`）
- `backend/app/schemas/ai_task.py`（`MarkStaleFailedAITaskIn` + **`MarkStaleFailedAITaskOut`**）
- `backend/app/api/v1/endpoints/admin_ai_tasks.py`（路径锁定；`response_model=MarkStaleFailedAITaskOut`）
- `backend/tests/services/test_ai_tasks.py`（追加服务层）
- `backend/tests/api/v1/test_admin_ai_tasks.py`（追加 API/权限/**题纲类型 200**）
- 若路由注册需显式列表：仅 `admin_ai_tasks` 相关 include

**禁止：** Body `reason`；路径别名；复用 `AITaskSummaryOut`/`AITaskAdminDetailOut` 等含 `task_type: TaskType` 的响应；调用任何 enqueue/`apply_async`；改 cancel/retry 语义；扩大 `TaskType` Literal（属 §12.2 另案）；处置 `dde1470f`；Alembic；前端；Dify；`.env`。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_mark_stale_failed_schema_only_expected_updated_at` | `MarkStaleFailedAITaskIn` 字段集 == `{expected_updated_at}`；多余 `reason` → ValidationError |
| `test_mark_stale_failed_out_fields_minimal` | `MarkStaleFailedAITaskOut` 字段集 == `{id, status, error_code, updated_at, finished_at}`；**无** `task_type` / attempts / snapshot 字段 |
| `test_mark_stale_failed_rejects_age_under_5_minutes` | running + `updated_at = now-4min` + 正确 expected → StateError/409；行不变 |
| `test_mark_stale_failed_rejects_expected_updated_at_mismatch` | 年龄≥5min、错误时间戳 → 409/StateError；行不变 |
| `test_mark_stale_failed_rejects_non_running` | pending/failed/succeeded → 409/StateError |
| `test_mark_stale_failed_not_found` | 未知 id → 404 |
| `test_mark_stale_failed_success_updates_task_and_running_attempt` | 年龄≥5min + 匹配 expected：`failed` / `stale_running_recovered` / `non_retryable` / 固定脱敏 message；running attempt 同步；`finished_at` 有值；服务返回 `MarkStaleFailedAITaskOut` |
| `test_mark_stale_failed_conditional_update_race` | 第二次旧 `expected_updated_at` → 409；第一次成功 |
| `test_mark_stale_failed_audits_without_reason` | `action=ai_task.stale_running_recovered`；`changes` 键 ⊆ `{ai_task_id, task_type, previous_status, new_status, expected_updated_at, error_code}`；`error_code=stale_running_recovered`；无 `reason`（审计 `changes` 可含 `task_type` 字符串，**不**经 summary Schema 校验） |
| `test_mark_stale_failed_never_enqueues` | 成功路径零 enqueue / `apply_async` |
| `test_admin_mark_stale_failed_path_and_permission` | `POST /api/v1/admin/ai-tasks/{id}/mark-stale-failed`；无 `ai_task.manage` → 403；有权限 → 200 + body 符合 `MarkStaleFailedAITaskOut` |
| `test_admin_mark_stale_failed_path_string_locked` | 静态 path `"/{task_id}/mark-stale-failed"`；无别名；`response_model` 为 `MarkStaleFailedAITaskOut`（非 AdminDetail/Summary） |
| `test_admin_mark_stale_failed_interview_question_generate_returns_200` | 构造 `task_type=INTERVIEW_QUESTION_GENERATE`（或字面 `"INTERVIEW_QUESTION_GENERATE"`）、`status=running`、年龄≥5min；管理员带 `ai_task.manage` POST 合法 `expected_updated_at` → **HTTP 200**；JSON 含 `id`/`status=failed`/`error_code=stale_running_recovered`/`updated_at`/`finished_at`；**无**响应 ValidationError；**不**要求 body 含 `task_type` |

```text
.venv\Scripts\python.exe -m pytest tests/services/test_ai_tasks.py tests/api/v1/test_admin_ai_tasks.py -q --tb=short -k "mark_stale or stale_failed or stale_running"
```

期望：RED。

### GREEN

1. `MarkStaleFailedAITaskIn(expected_updated_at: datetime)`；`extra="forbid"`（若项目惯例支持）。
2. `MarkStaleFailedAITaskOut`：**仅** `id: UUID`、`status: str`（或既有 status Literal，**不得**引入会失败的 `task_type`）、`error_code: str | None`、`updated_at: datetime`、`finished_at: datetime | None`。
3. `mark_stale_failed_ai_task(...) -> MarkStaleFailedAITaskOut`：管理员侧条件 `UPDATE`（`status=running` AND `updated_at=expected` AND `updated_at <= now - STALE_RUNNING_MIN_AGE`）；`rowcount!=1` → `AITaskStateError`；同步 running attempt；`record_audit` 按规格 §5.4；**零** enqueue；组装最小 Out（**禁止** `to_ai_task_out` / AdminDetail 转换）。
4. Endpoint：

```python
@router.post("/{task_id}/mark-stale-failed", response_model=MarkStaleFailedAITaskOut)
async def mark_stale_failed_admin_ai_task_endpoint(...):
    ...
```

   权限与 retry/cancel 相同（`ai_task.manage`）。
5. 固定脱敏 `error_message`（短常量）；不接受客户端文案。

同上 pytest 全绿。

**提交边界：** 上列允许文件。

---

## Task 4 — 完整回归 + 不触碰 `dde1470f-…` 的后续 UAT runbook

**Consumes：** Task 1–3。规格 §7.2、§8、§12。
**Produces：** 自动化回归命令清单；人工 UAT 顺序文档（本任务 **默认不执行** UAT）。

**允许改的文件：**

- 本计划文件（仅修正笔误时）
- 可选：`backend/tests/...` 仅补漏测（不得削弱）

**禁止：** 启动 worker；调用 Dify；写开发库；操作 `dde1470f-…` 或 `3556206d-…`；改 `.env` live；purge Redis；提交/push（除非用户另授权且不含密钥）。

### 4.1 自动化回归（零 Dify HTTP；零 worker）

```text
cd backend
.venv\Scripts\python.exe -m pytest ^
  tests/services/test_async_session_collection_assign.py ^
  tests/workers/test_interview_ai_worker.py ^
  tests/workers/test_ai_task_persist_failed.py ^
  tests/workers/test_ai_task_terminal_ownership_txn.py ^
  tests/services/test_ai_tasks.py ^
  tests/api/v1/test_admin_ai_tasks.py ^
  tests/services/test_interview_questions.py ^
  tests/services/test_interview_analyses.py ^
  tests/workers/test_sensitive_ai_queue.py ^
  -q --tb=short
```

必须覆盖：

- P1/P2/P3 无裸集合赋值；AsyncSession 无 `MissingGreenlet`；
- `persist_failed` 脱敏 + 不入队；`output_invalid` 未回退；
- `_reassert_running_ownership_for_terminal` 被五类终态调用；真实事务：迟到 worker 不覆盖管理员 `failed`；
- mark-stale-failed：5min、`expected_updated_at`、409、审计键集、零 enqueue、路径锁定、无 `reason`；
- **`MarkStaleFailedAITaskOut` 最小字段**；`INTERVIEW_QUESTION_GENERATE` 成功 **200**（不经残缺 `TaskType` summary/detail）；
- 敏感队列既有用例不回退。

**禁止**测试打开真实 `httpx` 打 Dify。

### 4.2 后续 UAT runbook（人工；另授权才执行；默认 agent 不跑）

硬性：**全程禁止** 对 `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca` 做 retry / cancel / mark-stale-failed / SQL / Redis。该 ID 回收属规格 §12 另案。

| 步骤 | 动作 | 通过标准 | 失败则停 |
|---|---|---|---|
| U0 | 确认 live 按当时授权；`LLEN($SENSITIVE_Q)==0`；无非目标干预 | 队列空 | 不启 worker |
| U1 | **新建** UAT 轮次/任务（新 generate）；记录**新** `ai_task_id`（≠ `dde1470f-…`） | DB `pending` | — |
| U2 | 仅敏感 worker：`-Q $SENSITIVE_Q --concurrency=1 --prefetch-multiplier=1` | 消费新任务 | 不得用普通 worker 清敏感队列 |
| U3 | 只读轮询 admin detail + question-set | `succeeded` 且 version≥1；或可解释 `output_invalid`；**不得**因 MissingGreenlet 留 `running` | 留 `running` → 停 worker，**不要**对 `dde1470f` 操作；可用**测试 fixture** 另验 mark-stale-failed |
| U4 | 停敏感 worker；live 恢复按授权 | worker 停 | — |
| U5（可选） | 对**故意制造**的测试用 running（年龄≥5min）调 `POST .../mark-stale-failed` + 正确 `expected_updated_at` | **200** + `MarkStaleFailedAITaskOut`（`failed` / `stale_running_recovered`）；`LLEN` 不因本调用增加 | 仍禁止碰 `dde1470f-…` |

运维命令（文档化，本任务不执行）：

```bash
celery -A app.workers.celery_app worker -Q "$SENSITIVE_Q" -l info --concurrency=1 --prefetch-multiplier=1
```

### 4.3 明确不在本计划交付

- `dde1470f-…` 实际回收；
- **`AITaskSummaryOut.task_type` Literal 补全**（retry 等仍可能踩坑；本计划仅用独立 Out 保护 mark-stale）；
- attempt `provider_run_id` 键名修复；
- relationship `selectin` 优化；
- 生产定时 stale 扫描。

### 4.4 提交边界

默认无运行时文件。若仅补测试：对应 `tests/**` 清单。

---

## 实现顺序与依赖

```text
Task 1 (P1/P2/P3 set_committed_value + 真实 AsyncSession)
  → Task 2 (persist_failed + _reassert_running_ownership_for_terminal + 真实事务竞态)
  → Task 3 (mark-stale-failed + MarkStaleFailedAITaskOut；题纲类型 200)
  → Task 4 (回归清单 + UAT runbook；默认不执行 UAT)
```

---

## 计划自检

- [x] 规格 §1–§9 / §12 均有任务映射；§5.1 路径无「可微调」、无 `reason`
- [x] mark-stale **不**复用含残缺 `TaskType` 的 summary/detail；锁定 `MarkStaleFailedAITaskOut`（仅 id/status/error_code/updated_at/finished_at）
- [x] API RED：`INTERVIEW_QUESTION_GENERATE` → 成功 **200**（`test_admin_mark_stale_failed_interview_question_generate_returns_200`）
- [x] worker 竞态 **唯一**锁定：`_reassert_running_ownership_for_terminal` + `SELECT … FOR UPDATE`；无「条件更新或 FOR UPDATE」二选一
- [x] 五类终态均调用帮助函数：success / output_invalid / provider failed / persist_failed / auto-retry→pending
- [x] 真实事务 RED：`test_late_worker_does_not_overwrite_admin_failed_real_txn`
- [x] 四独立任务完整；每项含允许文件、禁止范围、精确 RED、GREEN、验证命令、提交边界
- [x] 无 TBD / TODO / 占位符逃避决策
- [x] 无真实凭据、broker 密码、JD/简历/题纲/转写正文样例
- [x] 明确不改 Dify/队列/YAML/迁移/前端；不启 worker；不写 `recruit`；不碰 `dde1470f-…`
- [x] `set_committed_value`、`persist_failed`、`stale_running_recovered`、5 minutes、`expected_updated_at`、审计 action、零入队均锁定
