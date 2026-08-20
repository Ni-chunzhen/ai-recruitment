# AsyncSession 持久化 MissingGreenlet 与 stale-running 恢复设计规格

基线：当前工作区 `main` @ `83cb473`（敏感队列已合入）。
本规格只定义：**AsyncSession 下集合关系赋值修复**、**worker 持久化异常脱敏终态**、**管理员 stale-running 恢复接口**。不写业务实现、不改 `.env`、不启动 worker、不调用 Dify、不处置既有卡住任务。

关联：

- `docs/superpowers/specs/2026-08-19-sensitive-ai-queue-design.md`（队列隔离；本规格 **不** 改路由/队列）
- `docs/superpowers/specs/2026-08-18-interview-question-live-dify-design.md`（live 门禁；本规格 **不** 改）
- 现场栈：`persist_question_generation_result` L910 → `MissingGreenlet`（UAT retry 后 task `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca` 滞留 `running`；**本规格明确不处置该 ID**）

---

## 1. 范围

### 1.1 目标

1. 消除已确认根因：在 **AsyncSession** 上对 **未加载** 的集合关系做同步赋值，触发 sync lazyload → `sqlalchemy.exc.MissingGreenlet`。
2. 修复后，题纲 / 分析成功路径在真实 AsyncSession（含 Celery worker）下可完成版本+子行持久化并进入 `succeeded`。
3. worker 在 `_after_task_success` / 业务持久化抛出 **非契约校验** 异常时，必须将 task + 当前 attempt 写入脱敏 **`failed`** 终态并 commit，**禁止**再把异常冒泡成 Celery「unexpected」且 DB 留在 `running`。
4. 提供 **管理员** stale-running 恢复接口：满足时间门槛 + 乐观锁后标记 `failed`，**不入队**、不调用 provider。
5. 规定 worker 最终写入与管理员恢复之间的 **竞态防护**，避免晚到的成功/失败写覆盖已恢复状态。

### 1.2 非目标

- **不**改 Dify workflow / YAML / live 门禁 / 模型配置。
- **不**改 Celery `task_routes`、敏感队列名、`enqueue_*` 分支语义。
- **不**做 Alembic 迁移、不改表结构、不改前端。
- **不**新增按任意 ID「重新执行 / 强制跑 worker」的 API 或 CLI。
- **不**在本规格实施或 UAT 中处置、修复、retry、cancel、SQL/Redis 干预任务 **`dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`**。
- **不**处理非目标 `running` 任务 `3556206d-138b-40f6-9b23-97fce178a32e`（既有约束延续）。
- **不**扩大 cancel 语义为「可取消 running」（cancel 仍仅 `pending`；stale 恢复是独立入口）。

---

## 2. 已确认根因（锁定）

### 2.1 机制

1. `create_*_version` + `create_*_items/dimensions` 经 `session.add` + `await flush()` 后，父对象进入 **persistent**，集合关系（默认 lazy）仍为 **未加载**。
2. 子行仅设置 FK 列（如 `question_version_id`），**未**通过 `item.version = version` 维护双向集合，故内存中父集合不会自动填充。
3. 代码执行 `parent.collection = rows`（同步赋值）时，SQLAlchemy 为计算历史先 `get()` 旧集合 → **同步** `session.execute` lazy SELECT。
4. 在 AsyncSession / async 方言下该路径调用 `await_only` → **`MissingGreenlet`**。
5. 异常若逃出 `_handle_process`，Celery 记 unexpected failure；此前已 commit 的 `pending→running` 与 attempt 行 **不回滚** → DB **stale `running`**；业务版本往往未 commit（题纲 version 计数仍为 0）。

隔离复现（`sqlite+aiosqlite:///:memory:`，未触开发库）：flush 后 `version.items = [item]` 必现 `MissingGreenlet`；`set_committed_value(version, "items", [item])` 与 `await session.refresh(version, ["items"])` 不炸。

### 2.2 三条受影响持久化路径（必须全部改）

| # | 符号 | 文件（基线） | 赋值语句 | 关系 |
|---|---|---|---|---|
| P1 | `persist_question_generation_result` | `app/services/interview_questions.py` ~L910 | `version.items = items` | `InterviewQuestionVersion.items` |
| P2 | 人工编辑题纲成功路径（与 P1 同模式） | `app/services/interview_questions.py` ~L1060 | `version.items = items` | 同上 |
| P3 | `persist_analysis_generation_result` | `app/services/interview_analyses.py` ~L1073–L1078 | `dim_row.evidence = [...]`；`analysis_version.dimensions = dim_rows` | `InterviewRoundAnalysisDimension.evidence`；`InterviewRoundAnalysisVersion.dimensions` |

单元测若使用 MagicMock/`FakeWorkerSession` **不得**视为已覆盖；必须有真实 AsyncSession 用例（§8）。

---

## 3. 修复方案：`set_committed_value`（锁定）

### 3.1 规范写法

对 P1–P3，**禁止**继续使用会触发 lazyload 的集合赋值：

```python
# 禁止（AsyncSession）
version.items = items
analysis_version.dimensions = dim_rows
dim_row.evidence = evidence_subset
```

**锁定替换**：

```python
from sqlalchemy.orm.attributes import set_committed_value

set_committed_value(version, "items", items)
set_committed_value(analysis_version, "dimensions", dim_rows)
set_committed_value(dim_row, "evidence", evidence_subset)
```

语义：把集合标记为已加载的提交态，**不**发 SELECT、**不**走 sync IO。子行仍须事先 `add` + `flush`（或同一 flush 前加入 session），FK 完整。

### 3.2 允许的等价替代（仅当测试证明等价）

- `await session.refresh(parent, attribute_names=["items"])` **仅**在赋值确有必要时用于只读回填；**不得**再依赖「先 refresh 再 `= items`」作为唯一修复（多一次查询）。
- **禁止**为绕过问题而改用 sync `Session`、或在 worker 内嵌套新的 `asyncio.run`。
- **禁止** `expire_on_commit` 全局乱改作为本问题的「修复」。

### 3.3 不在范围

- 不改 relationship 定义为 `lazy="selectin"` 作为本规格唯一修复（可选后续优化，**不能**替代 P1–P3 的赋值修复）。
- 不改 cascade / delete-orphan 业务语义。

---

## 4. Worker：持久化异常 → 脱敏 `failed` 终态（锁定）

### 4.1 捕获范围

在 `_handle_process` 中，当 `outcome.ok and outcome.result is not None` 时：

```text
try:
    persist_meta = await _after_task_success(...)
except _STAGE8_OUTPUT_INVALID_EXCEPTIONS:
    # 既有：output_invalid（保持）
except Exception as exc:
    # 本规格新增：脱敏 failed 终态
```

`MissingGreenlet`、其它 ORM/DB/未预期异常均走 **新增** 分支（契约校验类仍走既有 `output_invalid`）。

### 4.2 终态字段（task + 当前 attempt）

| 字段 | 值 |
|---|---|
| `status` | **`failed`**（不是 `output_invalid`，不是留 `running`） |
| `error_code` | 固定短码，锁定为 **`persist_failed`** |
| `error_category` | **`non_retryable`** |
| `error_message` | **脱敏**：允许 `type(exc).__name__` 与可选白名单短语（如 `orm_persistence`）；**禁止** JD/简历/题纲/转写正文、snapshot 全文、`str(exc)` 若可能含 SQL 参数或明文、token、Key、broker URL |
| `finished_at` / `updated_at` | 当前 UTC |
| attempt 对应字段 | 与 task 一致的 status / category / 脱敏 message；`http_status` 可保留 provider 已写入值 |

Stage8 可写 public/`_write_stage8_raw` 元数据：`validation_error_code` **不要**冒充契约失败；可用 `persist_error_type=<ExcName>` 类键，仍禁止正文。

### 4.3 Commit 与返回

- 必须 `await session.commit()` 后再返回。
- 返回 `{"status": "failed", "attempt_no": ...}`（或与现有 failed 返回形状一致）。
- **禁止**在此分支自动 `_enqueue_retry_for_task` / `apply_async`。
- **禁止**让异常继续冒泡到 Celery（本分支已处理）。

### 4.4 与「管理员恢复」的关系

本分支处理的是 **同一次 worker 执行内** 的持久化失败。管理员接口处理的是 **已经卡住的历史 `running`**。二者互补。

---

## 5. 管理员 stale-running 恢复接口（锁定）

### 5.1 入口

| 项 | 锁定 |
|---|---|
| Method / Path | **`POST /api/v1/admin/ai-tasks/{task_id}/mark-stale-failed`**（完整路径字符串锁定；实现、OpenAPI、测试不得改名或另开别名） |
| 权限 | **`ai_task.manage`**（与 admin retry/cancel 一致） |
| Body | JSON：**仅** `{ "expected_updated_at": "<ISO-8601 datetime>" }`。**禁止** `reason` 或任何自由文本字段（即使限长，仍可能写入敏感业务正文） |
| 成功响应 | 既有 `AITaskAdminDetailOut`（或等价 admin detail）；status=`failed`；`error_code`=`stale_running_recovered` |

### 5.2 前置条件（全部满足才更新）

1. 任务存在。
2. 当前 `status == running`。
3. **年龄门槛**：`now - task.updated_at >= 5 minutes`（比较用 UTC aware；实现可用 `timedelta(minutes=5)` 常量名建议 `STALE_RUNNING_MIN_AGE`）。
4. **乐观锁**：请求体 `expected_updated_at` 与行上 `updated_at` **相等**（实现须统一到同一时钟精度策略，测试锁定：客户端读 detail 的 `updated_at` 原样回传）。

任一不满足 → **409**（或 404 仅当不存在），**不**改行、**不**写成功审计。

### 5.3 条件更新（必须）

```sql
UPDATE ai_tasks
SET status = 'failed',
    error_code = 'stale_running_recovered',
    error_category = 'non_retryable',
    error_message = <脱敏短文案>,
    finished_at = now,
    updated_at = now
WHERE id = :id
  AND status = 'running'
  AND updated_at = :expected_updated_at
  AND updated_at <= now() - interval '5 minutes'
```

（ORM 等价 `update(...).where(...)`；`rowcount != 1` → 409。）

对 **当前仍为 `running` 的最新 attempt**（若有）：同步标为 `failed` + 同 error_code/category/脱敏 message；若无 running attempt，仅更新 task（测试锁定行为）。

### 5.4 审计

`record_audit`：

| 字段 | 值 |
|---|---|
| `action` | **`ai_task.stale_running_recovered`** |
| `result` | `success` |
| `resource_type` | `ai_task` |
| `actor_user_id` | 管理员 id |
| `resource_id` | `str(task.id)` |
| `request_context` | HTTP 请求上下文 |
| `changes` | 仅允许：`ai_task_id`、`task_type`、`previous_status`、`new_status`、`expected_updated_at`、固定 **`error_code`=`stale_running_recovered`**。**禁止** `reason`、业务正文、凭据或任意自由文本 |

### 5.5 不入队边界

- 本接口 **不得** 调用 `enqueue_ai_task` / `enqueue_sensitive_question_task` / 任何 `apply_async`。
- 恢复后若需重跑：走既有 **`retry`**（`failed` 合法），由调用方另授权；本接口本身只标记失败。

### 5.6 明确不处置的 ID

实现、测试、UAT runbook **不得**把 `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca` 写进自动脚本或默认 fixture。人工是否另案处理 **不在本规格范围**。

---

## 6. Worker 最终写入的竞态防护（锁定）

场景：管理员已将 task 标为 `failed`（stale 恢复），但旧 worker 进程稍后仍试图写入 `succeeded` / `output_invalid` / `failed`。

### 6.1 规则

worker 在 **claim 之后的每一次终态 commit**（success / output_invalid / provider failed / persist_failed / auto-retry 回 pending）之前，必须满足：

1. 使用 **条件更新** 或 `SELECT … FOR UPDATE` 后检查：仅当 DB 中该行仍为 **本次执行所期望的 inflight 状态**（通常为 `running`，且 attempt 仍为当前 `attempt_id`）时才写入终态。
2. 若发现状态已变为 `failed` / `cancelled` / `succeeded` 等 **非本 worker 持有** 状态：
   - **不得**覆盖为 `succeeded`；
   - 记录安全日志（task_id、observed_status、error_type）；
   - 返回明确结果键，如 `{"status": "skipped_stale_owner", "observed_status": ...}`；
   - 对「管理员已 stale-recover」情况：优先 **保留管理员结果**，attempt 若仍 `running` 可标 failed 与 task 对齐（若条件更新允许），但 **禁止** 写成 succeeded。

### 6.2 与既有 cancel 晚到响应

已有 `task.status == cancelled` 分支（late response）保留；本规格将其视为竞态防护族的一部分，stale-recover 的 `failed` 同等对待（不可被成功覆盖）。

---

## 7. 测试要求（真实 AsyncSession）

### 7.1 必须新增 / 强化

| 测试 | 断言 |
|---|---|
| P1 AsyncSession | 内存或隔离测试库；flush 后 `set_committed_value` 路径完成 persist；**无** `MissingGreenlet`；items 可读 |
| 回归：禁止旧赋值 | 可选：针对最小 Version/Item 模型断言 `parent.items = [...]` 在 AsyncSession 下抛 `MissingGreenlet`（文档化根因） |
| P3 | 分析 persist 在 AsyncSession 下同样不炸 |
| Worker persist_failed | mock `_after_task_success` 抛 `MissingGreenlet`（或 RuntimeError）；断言 DB task/attempt → `failed` + `persist_failed`；Celery 返回非 unexpected |
| 管理员恢复 | 年龄不足 / `expected_updated_at` 不匹配 / 非 running → 409；成功 → failed + 审计 + **零** enqueue |
| 竞态 | 模拟 running→（admin failed）后 worker 成功路径 → 不得变 succeeded |

### 7.2 禁止

- 仅用 MagicMock session 宣称 P1 已修。
- 测试读写开发库 `recruit`；须 `TEST_DATABASE_URL` 或 `recruit_test` / 内存 SQLite async，并拒绝 business DB 名。
- 测试调用真实 Dify 或连接 broker 消费。

### 7.3 既有单元测

更新 P1/P2 相关 mock 测若仍直接依赖 `version.items =` 行为，改为与生产相同的 `set_committed_value`（或断言集合已通过该 API 可见）。

---

## 8. 修复后 UAT 顺序（锁定；不含 dde1470f）

在代码合入且 §7 自动化通过后，**另授权**时按序执行（默认仍不启全量 worker）：

1. 确认 live 开关按当时授权；`LLEN($SENSITIVE_Q)==0`；无非目标干预。
2. 使用 **新的** UAT 轮次/任务（或新 generate），**禁止**操作 `dde1470f-…`。
3. 仅敏感 worker：`-Q $SENSITIVE_Q --concurrency=1 --prefetch-multiplier=1`。
4. 期望：task → `succeeded`，题纲 version≥1；或可解释的 `output_invalid`（契约），**不得**再因 MissingGreenlet 留 `running`。
5. 停 worker；live 恢复策略按当时授权。
6. （可选）对 **测试用** 故意卡住的 running fixture 验证 mark-stale-failed；**仍不**对 `dde1470f-…` 操作，除非未来单独书面授权。

---

## 9. 稳定符号

| 符号 | 锁定 |
|---|---|
| 集合修复 API | `sqlalchemy.orm.attributes.set_committed_value` |
| Worker 持久化失败 error_code | `persist_failed` |
| 管理员恢复 Path | **`POST /api/v1/admin/ai-tasks/{task_id}/mark-stale-failed`** |
| 管理员恢复 Body | **仅** `expected_updated_at`；**无** `reason` / 自由文本 |
| 管理员恢复 error_code | `stale_running_recovered` |
| 审计 action | `ai_task.stale_running_recovered` |
| 年龄门槛 | **5 minutes** |
| 权限 | `ai_task.manage` |
| 终态（persist 异常 / stale 恢复） | `failed` + `non_retryable` |
| 不入队 | 恢复接口零 `apply_async` / 零 `enqueue_*` |

---

## 10. 实现文件边界（建议）

允许（实施计划阶段再收紧）：

- `app/services/interview_questions.py`（P1/P2）
- `app/services/interview_analyses.py`（P3）
- `app/workers/ai_tasks.py`（persist_failed + 竞态）
- `app/services/ai_tasks.py`（恢复服务）
- `app/api/v1/endpoints/admin_ai_tasks.py`（路由）
- `app/schemas/ai_task.py`（请求体）
- 相关 tests

禁止：`.env`、Alembic、前端、`dify.py` 门禁、celery 路由、YAML。

---

## 11. 计划自检

- [x] 根因与 P1/P2/P3 三条路径锁定
- [x] `set_committed_value` 为指定方案
- [x] 持久化异常 → 脱敏 `failed` / `persist_failed`
- [x] 管理员恢复路径锁定为 `POST /api/v1/admin/ai-tasks/{task_id}/mark-stale-failed`（无别名/无「可微调」）
- [x] 恢复 Body 仅 `expected_updated_at`；无 `reason` / 自由文本；审计固定 `error_code=stale_running_recovered` + 允许元数据
- [x] 管理员恢复：5 分钟、`expected_updated_at` 条件更新、审计、不入队
- [x] worker 终态与恢复的竞态防护
- [x] 真实 AsyncSession 测试 + 恢复 UAT 顺序
- [x] 明确不改 Dify / 队列 / YAML / 迁移 / 前端；不处置 `dde1470f-…`
- [x] 无真实凭据、正文样例、broker 密码
- [x] 无 TBD 占位符逃避决策

---

## 12. 本规格已知未覆盖（显式）

1. **`dde1470f-…` 的实际回收**：需单独授权（可用本规格接口，但不在默认 UAT）。
2. **`AITaskSummaryOut.task_type` 字面量缺少题纲/分析类型**（retry 响应 ValidationError）：属独立契约缺陷，**不**在本规格修复范围。
3. **attempt 列未写入 provider_run_id**（redact 后 extract 键名不一致）：独立可观测性缺陷，不在本规格。
4. **将 relationship 改为 selectin 的性能优化**：可选后续，非本规格必做。
5. **生产环境 stale 扫描/定时任务**：不做；仅人工/管理员 API。
