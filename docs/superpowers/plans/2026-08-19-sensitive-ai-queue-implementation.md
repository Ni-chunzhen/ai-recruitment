# 敏感 AI 任务专用队列 — TDD 实施计划

> **For agentic workers:** 按任务顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。

**规格：** `docs/superpowers/specs/2026-08-19-sensitive-ai-queue-design.md`
**基线：** `main` @ `2c663fa`；现状 `celery_app` **无** `task_routes`；题纲 `dispatch_persisted_question_generation_task` → `enqueue_ai_task` → `process_ai_task`；自动重试硬编码 `process_ai_task.apply_async`。
**方法：** TDD。符号名锁定为规格 §9，禁止临时改名。

## 全局约束

- 不改 Alembic、数据库、前端、Dify live 门禁、`_handle_process` 业务/校验/脱敏逻辑、`.env` 真实值。
- **不**新增按任意 `ai_task_id` 执行的 HTTP/CLI。
- **不**处理非目标 `running` task `3556206d-138b-40f6-9b23-97fce178a32e`。
- 自动化测试 **零**真实 Dify HTTP；不启动全量/常驻 worker（Task 4 仅文档化运维命令）。
- **时序安全：** Task 1 的 `process_sensitive_ai_task` 骨架必须 **无条件安全失败**（`NotImplementedError`），**不得**调用 `_process_ai_task_async` / `_handle_process` / Dify。Task 2 即使已向敏感队列 `apply_async`，**仍不得启动任何 worker**。**仅 Task 3** 完成任务类型门禁后，敏感入口才允许实际处理题纲。
- 不写入真实 Key、broker 密码、JD/简历/题纲正文。
- 本计划各任务 **默认不提交**。若用户另行授权 commit，仅包含该任务「允许提交」文件清单。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| Settings 属性 | `celery_sensitive_queue_name: str` |
| env | `CELERY_SENSITIVE_QUEUE_NAME`（默认空 → 属性默认 **`ai_sensitive`**） |
| 默认队列 | `celery` |
| 敏感 Celery `name=` | `app.workers.ai_tasks.process_sensitive_ai_task` |
| 默认 Celery `name=` | `app.workers.ai_tasks.process_ai_task`（不变） |
| Python 函数 | `process_sensitive_ai_task`、`_enqueue_retry_for_task` |
| services | `enqueue_sensitive_question_task`、`enqueue_ai_task`（后者行为不变：只投默认任务） |
| 审计 action | `ai_task.sensitive_reroute_failed` |
| 返回 status | `rerouted` / `reroute_failed` / `rejected`（敏感入口非题纲） |
| `reason`（转投） | `question_generate_requires_sensitive_queue` |
| `reason`（敏感拒绝） | `unsupported_task_type` |

## 规格映射

| 规格 | 本计划 |
|---|---|
| §1 范围 / 非目标 | 全局约束 + Task 4 |
| §3.1–3.2 敏感任务注册与类型门禁 | Task 1 + Task 3 |
| §3.3–3.3.1 默认入口转投 + `record_audit` | Task 3 |
| §3.4 `_enqueue_retry_for_task` + 无循环导入 | Task 3 |
| §3.5 / §5 `task_routes`/`task_queues`/`-Q` 同一真源 | Task 1 + Task 4 |
| §4 分发 / 管理重试 | Task 2 |
| §6 Dify/UAT 数据边界 | Task 4（不改门禁；仅约束） |
| §7 失败停止 / 回滚 | Task 4 runbook |
| §8 测试与 UAT | 各 Task RED/GREEN + Task 4 |

---

## Task 1 — 配置与 Celery 注册 / 路由

**Consumes：** 规格 §3.1、§3.5、§5.1。
**Produces：** Settings、`celery_app` 路由、敏感任务注册骨架（可先 stub）、配置测试。

**允许改的文件：**

- `backend/app/core/config.py`
- `backend/app/workers/celery_app.py`
- `backend/app/workers/ai_tasks.py`（仅注册 `process_sensitive_ai_task` **安全失败骨架** + 必要 import；**不得**在本任务接通处理链）
- `backend/.env.example`
- `backend/tests/core/test_config.py`（追加）
- `backend/tests/workers/test_sensitive_ai_queue.py`（新建）

**禁止：** 改 `dispatch_*`、`retry_ai_task`、`_handle_process` 重试、Dify、`.env`；**禁止**骨架调用 `_process_ai_task_async`；**禁止**启动任何 Celery worker。

### RED

| 测试函数 | 断言 |
|---|---|
| `test_celery_sensitive_queue_name_default` | `Settings(_env_file=None)`（或等价不读本地 `.env`）+ `delenv CELERY_SENSITIVE_QUEUE_NAME` → `celery_sensitive_queue_name == "ai_sensitive"` |
| `test_celery_sensitive_queue_name_reads_env` | `monkeypatch.setenv("CELERY_SENSITIVE_QUEUE_NAME", "uat_sensitive_q")` + `get_settings.cache_clear()` → 属性等于 `uat_sensitive_q` |
| `test_env_example_sensitive_queue_var_empty` | `.env.example` 含 `CELERY_SENSITIVE_QUEUE_NAME=`（右侧空）；邻近注释说明默认 `ai_sensitive`、与 worker `-Q` / `task_routes` 同一真源、变更需重启 |
| `test_task_routes_sensitive_uses_settings_default` | `celery_app.conf.task_routes` 中键 `app.workers.ai_tasks.process_sensitive_ai_task` 的 `queue` **等于** 当前 Settings `celery_sensitive_queue_name`（默认 `ai_sensitive`）；`process_ai_task` / `purge_expired_ai_raw_payloads` **不**出现在敏感路由 |
| `test_task_routes_sensitive_follows_override` | 覆盖 env 为 `uat_sensitive_q` 后 **重建/重载** conf（测试内按实现约定：`get_settings.cache_clear()` + 重导入或调用工厂）；route `queue == "uat_sensitive_q"` |
| `test_task_queues_sensitive_name_matches_settings_if_declared` | 若实现声明 `task_queues`：敏感队列名 == Settings；否则本测 skip 或断言 conf 无硬编码第二字面量队列名冲突 |
| `test_process_sensitive_ai_task_registered` | Celery registry 含 name `app.workers.ai_tasks.process_sensitive_ai_task`；签名接受 `task_id: str`（`bind=True`） |
| `test_process_sensitive_ai_task_stub_never_runs_process_async_or_dify` | 直接调用骨架（或 `.run(task_id=...)`）：**必须**抛出 `NotImplementedError`（或规格允许的同等无条件安全失败）；patch `_process_ai_task_async` / `_handle_process` / `run_dify` / `_run_provider` 断言 **零调用**，即使传入虚构题纲 `task_id` |
| `test_config_override_requires_process_restart_documented` | 源码或 `.env.example` 注释含「重启」语义（API + Celery worker）；测试只断言注释/文档字符串存在，不测进程热更新 |

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/core/test_config.py tests/workers/test_sensitive_ai_queue.py -q --tb=short -k "celery_sensitive or task_routes or process_sensitive or env_example_sensitive or stub_never"
```

期望：RED（缺 Settings / 无 routes / 未注册 / 骨架未安全失败）。

### GREEN

1. `Settings` 增加 `celery_sensitive_queue_name: str = Field(default="ai_sensitive", validation_alias="CELERY_SENSITIVE_QUEUE_NAME")`（空 env 时仍默认 `ai_sensitive`；若 pydantic 空串覆盖默认，则用 validator/`default_factory` 把 `""` 规范为 `ai_sensitive`，并有测试锁定）。
2. `celery_app.conf.update`：`task_routes={ "app.workers.ai_tasks.process_sensitive_ai_task": {"queue": settings.celery_sensitive_queue_name} }`；如需显式 `task_queues`，敏感队列名 **同一** Settings 值；默认任务 **不**改路由。
3. 注册 **无条件安全失败骨架**（唯一允许形态；**禁止**调用 `_process_ai_task_async`）：

```python
@celery_app.task(name="app.workers.ai_tasks.process_sensitive_ai_task", bind=True)
def process_sensitive_ai_task(self, task_id: str) -> dict:  # noqa: ARG001
    raise NotImplementedError(
        "process_sensitive_ai_task gated in Task 3; refuse execution until type gate lands"
    )
```

   必须已注册且可被 `apply_async`；误消费时只会抛错，**绝不**处理题纲/调用 Dify。类型门禁与真实处理 **仅在 Task 3** 替换本骨架。
4. `.env.example` 追加空值行 + 重启/真源注释。

同上 pytest 全绿（含 `test_process_sensitive_ai_task_stub_never_runs_process_async_or_dify`）。

**提交边界（仅当用户明确要求）：** 上列允许文件。禁止 `.env`。

---

## Task 2 — 服务层题纲分发

**Consumes：** Task 1（敏感任务可 `apply_async`；骨架仍 `NotImplementedError`）。规格 §4.1–4.2。
**Produces：** `enqueue_sensitive_question_task`；题纲 dispatch / 管理重试按类型分发。

**允许改的文件：**

- `backend/app/services/ai_tasks.py`
- `backend/app/services/interview_questions.py`（仅 `dispatch_persisted_question_generation_task` 入队调用）
- `backend/tests/services/test_ai_tasks.py`（追加）
- `backend/tests/services/test_interview_questions.py`（更新 enqueue 断言）
- `backend/tests/workers/test_sensitive_ai_queue.py`（追加服务层用例，可选）
- `backend/tests/api/v1/test_admin_ai_tasks.py`（若服务层 retry 测试更合适则放 `test_ai_tasks.py`）

**禁止：** 新增 execute API/CLI；改 worker 转投/门禁（Task 3）；改分析 dispatch（仍 `enqueue_ai_task`）；**即使题纲已入队到敏感队列，也不得启动任何 Celery worker**（消息可留在 broker；骨架未接通前误消费只会 `NotImplementedError`，但本任务仍禁止启动）。

### RED

| 测试函数 | 断言 |
|---|---|
| `test_enqueue_sensitive_question_task_targets_sensitive_celery_name` | patch `process_sensitive_ai_task.apply_async`；调用 `enqueue_sensitive_question_task(uuid, countdown=7)` → `args=[str(uuid)]`、`countdown=7`；**不**调用 `process_ai_task.apply_async` |
| `test_enqueue_ai_task_still_targets_default_process` | 既有/`fake_enqueue` 语义：`enqueue_ai_task` 仍只 `process_ai_task.apply_async` |
| `test_dispatch_persisted_question_generation_uses_sensitive_enqueue` | patch `enqueue_sensitive_question_task`；`dispatch_persisted_question_generation_task` 调用它且 **不**调用 `enqueue_ai_task`；源码静态断言 `enqueue_sensitive_question_task` in dispatch、`enqueue_ai_task` not in dispatch（对齐既有 inspect 风格） |
| `test_retry_ai_task_question_generate_uses_sensitive_enqueue` | 构造 `task_type=INTERVIEW_QUESTION_GENERATE` 且 status `failed`/`output_invalid`；patch 两个 enqueue；`retry_ai_task` 后仅敏感 enqueue 被调 |
| `test_retry_ai_task_resume_or_jd_still_uses_default_enqueue` | 非题纲类型仍 `enqueue_ai_task` |
| `test_no_arbitrary_task_id_execute_endpoint_added` | 静态：`interview_ai` / `admin_ai_tasks` / `ai_tasks` endpoints 源码 **无** 新增 `/execute`、`/run`、`/dispatch` 路径（相对本基线） |

```text
.venv\Scripts\python.exe -m pytest tests/services/test_ai_tasks.py tests/services/test_interview_questions.py tests/workers/test_sensitive_ai_queue.py -q --tb=short -k "enqueue_sensitive or dispatch_persisted_question or retry_ai_task_question or enqueue_ai_task_still"
```

期望：RED（dispatch 仍 `enqueue_ai_task`；无 `enqueue_sensitive_question_task`）。

### GREEN

1. 在 `app/services/ai_tasks.py`：

```python
def enqueue_sensitive_question_task(task_id: UUID, *, countdown: int = 0) -> None:
    from app.workers.ai_tasks import process_sensitive_ai_task
    process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=countdown)
```

2. `dispatch_persisted_question_generation_task`：`enqueue_sensitive_question_task(task.id)`。
3. `retry_ai_task`：commit 后按 `task.task_type` 分支；题纲 → `enqueue_sensitive_question_task`；否则 → `enqueue_ai_task`。
4. `enqueue_ai_task` **不变**（JD/简历/评分/维度）。
5. 更新 `test_interview_questions.py` 中 monkeypatch 目标为 `enqueue_sensitive_question_task`（题纲路径）。
6. **不**替换 Task 1 骨架；**不**启动 worker；自动化仅 patch `apply_async`，不消费队列。

**提交边界：** 上列允许文件。

---

## Task 3 — Worker 隔离、转投与自动重试

**Consumes：** Task 1–2。规格 §3.2–3.4、§3.6、§8.1。
**Produces：** **首次**将 `process_sensitive_ai_task` 从 `NotImplementedError` 骨架替换为带类型门禁的实现；`process_ai_task` 题纲转投；`_enqueue_retry_for_task`；转投失败审计。

**时序：** **仅本任务完成后**，敏感入口才允许对题纲调用 `_process_ai_task_async`。此前（Task 1–2）任何执行必须安全失败。

**允许改的文件：**

- `backend/app/workers/ai_tasks.py`
- `backend/tests/workers/test_sensitive_ai_queue.py`
- `backend/tests/workers/test_interview_ai_worker.py`（仅追加/调整与自动重试相关的断言；**不得**削弱既有脱敏/mock 用例）

**禁止：** 复制 `_handle_process` 业务；改 `dify.py` 门禁；worker 顶层 `from app.services.ai_tasks import enqueue_*`；处理 `3556206d-...`。

### RED

| 测试函数 | 断言 |
|---|---|
| `test_process_sensitive_rejects_non_question_without_handle` | mock `_process_ai_task_async` / `_handle_process`；DB 或 fake 加载 `task_type=RESUME_SCORE`（或 JD）；返回 `status=rejected`、`reason=unsupported_task_type`；**未**调用 `_process_ai_task_async` |
| `test_process_sensitive_question_calls_process_async_once` | 题纲类型 → `_process_ai_task_async` **一次** |
| `test_process_ai_task_reroutes_question_once` | 题纲 pending；patch `process_sensitive_ai_task.apply_async`；调用默认入口 → `status=rerouted`、`reason=question_generate_requires_sensitive_queue`；`apply_async` **恰好一次**、`args=[str(id)]`；**未** claim（status 仍 `pending`）；**未**调用 `_handle_process` / `run_dify`；**未**调用 `enqueue_sensitive_question_task` |
| `test_process_ai_task_reroute_failure_keeps_pending_and_audits` | `apply_async` side_effect=异常；返回 `reroute_failed`；DB `pending`；`record_audit` 以 `action=ai_task.sensitive_reroute_failed`、`result=failure`、`resource_type=ai_task`、`actor_user_id is None`、`request_id` 形如 `ai-task:{id}`、`changes` 键 ⊆ `{ai_task_id, task_type, error_type}` |
| `test_process_sensitive_never_apply_async_back_to_default` | 敏感入口路径源码/行为：不 `process_ai_task.apply_async` |
| `test_enqueue_retry_for_task_question_uses_sensitive` | 直接测 `_enqueue_retry_for_task`：题纲 → `process_sensitive_ai_task.apply_async`；非题纲 → `process_ai_task.apply_async` |
| `test_handle_process_question_auto_retry_does_not_use_default_entry` | 题纲任务走 retryable 失败路径（既有 worker 测试手法）；断言重试 `apply_async` 目标为 **`process_sensitive_ai_task`**，**不是** `process_ai_task` |
| `test_worker_module_has_no_toplevel_services_ai_tasks_import` | 静态读 `ai_tasks.py` worker 源：顶层无 `from app.services.ai_tasks import` / `import app.services.ai_tasks`；允许 `app.services.audit` |

```text
.venv\Scripts\python.exe -m pytest tests/workers/test_sensitive_ai_queue.py tests/workers/test_interview_ai_worker.py -q --tb=short -k "sensitive or reroute or enqueue_retry or auto_retry"
```

期望：RED。

### GREEN

1. **替换 Task 1 骨架为门禁实现（首次允许处理题纲）：** 短开 session → `get_ai_task_by_id`；非题纲 → `rejected` 日志返回（**不**调用 `_process_ai_task_async`）；题纲 → `asyncio.run(_process_ai_task_async(UUID(task_id)))`。删除 `NotImplementedError` 无条件失败。
2. **`process_ai_task`：** 在进入 `_handle_process` 前只读类型；题纲则 `process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=0)`（**禁止** `queue=` 硬编码，依赖 `task_routes`）；成功 `rerouted`；`except` → `record_audit(...)` 按规格 §3.3.1 → commit → `reroute_failed`。实现可将类型判定放进 `_process_ai_task_async` 前的新协程，但 **不得**在 claim 之后转投。
3. **`_enqueue_retry_for_task`：** 替换 `_handle_process` 内 `process_ai_task.apply_async` 重试行。
4. Import：`record_audit` 从 `app.services.audit`（可与现有 `RequestContext` 合并）；**禁止** import `enqueue_*`。
5. 更新/替换 `test_process_sensitive_ai_task_stub_never_runs_process_async_or_dify`：Task 3 后该测改为「非题纲仍零 `_process_ai_task_async`」；题纲路径另由 `test_process_sensitive_question_calls_process_async_once` 覆盖。

既有 live/mock 脱敏 worker 测试必须仍绿。

**提交边界：** 上列允许文件。

---

## Task 4 — 回归、运维命令与 UAT runbook（无业务代码或仅文档）

**Consumes：** Task 1–3 GREEN。规格 §1.3、§5.2–5.3、§6–§8。
**Produces：** 回归测试通过；运维/UAT 步骤写入本计划本节（**不**改运行时；可选仅追加测试注释）。

**允许改的文件：**

- `backend/tests/workers/test_sensitive_ai_queue.py`（回归清单用例若未在 Task 1–3 覆盖则补齐）
- `docs/superpowers/plans/2026-08-19-sensitive-ai-queue-implementation.md`（仅本 Task 的 runbook 微调，若实现时发现笔误）

**禁止：** 启动真实敏感 worker、调用 Dify、写库处理 `3556206d-...`、改 `.env` live Key（人工 UAT 另授权）。

### 4.1 自动化回归（零 Dify HTTP）

```text
cd backend
.venv\Scripts\python.exe -m pytest ^
  tests/workers/test_sensitive_ai_queue.py ^
  tests/core/test_config.py ^
  tests/services/test_ai_tasks.py ^
  tests/services/test_interview_questions.py ^
  tests/workers/test_interview_ai_worker.py ^
  tests/services/test_interview_question_live_dify.py ^
  -q --tb=short
```

必须覆盖（可用 `-k` 子集再全量）：

- 路由 / Settings 覆盖一致性；
- 转投一次；转投失败审计；
- 敏感入口非题纲拒绝；题纲复用 `_process_ai_task_async`；
- 自动重试不走默认入口；管理重试题纲走敏感 enqueue；
- 非敏感 `enqueue_ai_task` 仍默认；
- 既有题纲 live/mock 门禁与脱敏用例 **不回退**。

**禁止**测试打开真实 `httpx` 打 Dify。

### 4.2 运维命令（文档化；本任务不执行）

令 `SENSITIVE_Q` = `celery_sensitive_queue_name`（默认 `ai_sensitive`）。

```bash
# 普通 worker — 禁止包含敏感队列
celery -A app.workers.celery_app worker -Q celery -l info

# UAT 敏感 worker — 禁止包含 celery；concurrency=1 prefetch=1
celery -A app.workers.celery_app worker -Q "$SENSITIVE_Q" -l info --concurrency=1 --prefetch-multiplier=1
```

### 4.3 UAT 队列深度与 runbook（人工；agent 默认不执行）

| 检查点 | 条件 | 失败 |
|---|---|---|
| T0 | `LLEN($SENSITIVE_Q)==0`，无 worker | 不得启动敏感 worker |
| T1 | 对轮次 `683665ef-7801-4b35-b9ba-124b51cd441b` `POST .../question-set/generate` 后 `LLEN==1`，DB `pending` | 不得启动 |
| 执行 | 启动敏感 worker（§4.2）；只读轮询 admin AI task + question-set | — |
| T2 | 停止敏感 worker；可选 `LLEN==0` | 不得用普通 worker 清队列 |
| 回滚 | `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=false`；停止敏感 worker | — |

**硬性：** 不处理 `3556206d-138b-40f6-9b23-97fce178a32e`；仅 `UAT-CC-20260818-DIFY` 隔离数据；不 purge Redis；不 SQL。

### 4.4 提交边界

本任务默认 **无** 运行时文件。若仅补测试：允许 `tests/workers/test_sensitive_ai_queue.py`。

---

## 实现顺序与依赖

```text
Task 1 (Settings + routes + NotImplementedError 骨架；禁止 _process_ai_task_async)
  → Task 2 (enqueue_sensitive + dispatch + retry；禁止启动 worker)
  → Task 3 (类型门禁后首次允许处理题纲 + 转投 + _enqueue_retry_for_task)
  → Task 4 (回归 + runbook)
```

---

## 计划自检

- [x] 规格 §1–§8 均有任务映射
- [x] 无 TBD / 占位符
- [x] 任务名 `app.workers.ai_tasks.process_sensitive_ai_task`、队列默认 `ai_sensitive`、Settings `celery_sensitive_queue_name`、审计 `record_audit` + `ai_task.sensitive_reroute_failed` + `_worker_request_context` 与规格/源码一致
- [x] 转投不经 services enqueue；自动重试用 `_enqueue_retry_for_task`；管理重试用 `enqueue_sensitive_question_task`
- [x] Task 1 骨架无条件 `NotImplementedError`，绝不触发 `_process_ai_task_async`/Dify；Task 2 不启动 worker；仅 Task 3 接通题纲处理
- [x] 无真实凭据、broker 密码、JD/简历/题纲正文
- [x] 无 Alembic、无任意 ID 执行 API、不处理 non-target running task
- [x] 每项含精确文件、RED、GREEN、定向命令、提交边界
