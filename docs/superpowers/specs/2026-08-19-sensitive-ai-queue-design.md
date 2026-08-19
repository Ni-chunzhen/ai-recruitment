# 敏感 AI 任务专用队列设计规格

基线：当前工作区 `main` @ `2c663fa`。
本规格只定义 **`INTERVIEW_QUESTION_GENERATE` 的 Celery 队列隔离与单任务 UAT 执行路径**。不写业务实现、不改 `.env` 真实值、不启动 worker、不调用 Dify、不创建/处理 AI task。

关联规格：`docs/superpowers/specs/2026-08-18-interview-question-live-dify-design.md`（live 门禁与 Dify 抽象，本规格不重复修改）。

---

## 1. 范围

### 1.1 目标

1. 为 **题纲生成** 提供与默认 `celery` 队列物理隔离的 **`ai_sensitive` 专用队列**，使 UAT 可在不启动全量 worker、不消费共享队列其他消息的前提下，**仅执行一条已知的 `INTERVIEW_QUESTION_GENERATE` 任务**。
2. 普通 AI 任务（JD 解析、维度推荐、简历解析、简历评分）**继续**走既有 `process_ai_task` + 默认 `celery` 队列，行为不变。
3. 题纲任务的处理、重试、审计、持久化 **全部复用** 既有 `_handle_process` 链路，**禁止**复制 worker 业务逻辑。
4. 部署上支持 **双 worker 进程隔离**：普通 worker 只消费 `celery`；敏感 worker 只消费 `ai_sensitive`，且 UAT 敏感 worker 必须 **`concurrency=1`、`prefetch=1`、目标结束后即停止**。

### 1.2 第一期范围

| 任务类型 | 队列 | Celery 入口 | Dify live |
|---|---|---|---|
| `INTERVIEW_QUESTION_GENERATE` | **`ai_sensitive`** | 新 `process_sensitive_ai_task` | 受既有 live 门禁约束 |
| `INTERVIEW_ROUND_ANALYZE` | 不进入敏感队列 | 仍 `process_ai_task`（若将来入队） | **继续 mock** |
| `JD_PARSE` / `SCORE_DIMENSION_RECOMMEND` / `RESUME_PARSE` / `RESUME_SCORE` | 默认 `celery` | `process_ai_task` | 既有规则不变 |

### 1.3 非目标

- **不**处理、不修复、不通过 SQL/Redis/临时脚本/worker 干预现有非目标 **`running`** 任务 `3556206d-138b-40f6-9b23-97fce178a32e`（轮次 `7cfc9f5b-0f97-4f96-a640-b35959f2d64a`，非 `UAT-CC-20260818*` 数据）。
- **不**新增「按任意 `ai_task_id` 执行」的 HTTP 管理入口或 CLI。
- **不**持久化 Celery AsyncResult UUID；**不**做 Alembic 迁移。
- **不**为 `INTERVIEW_ROUND_ANALYZE` 开通 live 或敏感队列（分析继续 mock）。
- **不**改前端、不改 JD/简历 Dify 回退规则、不放开通用 `DIFY_API_KEY` 给题纲。
- **不**要求普通 worker 消费 `ai_sensitive`，**禁止**任一 worker 使用 `-Q celery,ai_sensitive` 或等价「消费全部队列」配置。

---

## 2. 源码事实（实现必须对齐）

### 2.1 现状：共享默认队列

```7:21:backend/app/workers/celery_app.py
celery_app = Celery(
    "ai_recruitment",
    broker=settings.celery_broker_url,
    include=["app.workers.ai_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    ...
)
```

- **无** `task_routes` / `task_queues`；所有任务默认进入 broker 队列名 **`celery`**。
- 已注册 Celery 任务名（`name=` 字符串，即路由键）：
  - `app.workers.ai_tasks.process_ai_task`
  - `app.workers.ai_tasks.purge_expired_ai_raw_payloads`

### 2.2 现状：入队与处理

| 符号 | 位置 | 行为 |
|---|---|---|
| `enqueue_ai_task(task_id, countdown=0)` | `app/services/ai_tasks.py` | `process_ai_task.apply_async(args=[str(task_id)], countdown=countdown)` |
| `process_ai_task(self, task_id: str)` | `app/workers/ai_tasks.py` | `asyncio.run(_process_ai_task_async(UUID(task_id)))` |
| `_process_ai_task_async(task_id)` | 同上 | 开 DB session → `_handle_process(session, task_id)` |
| `_handle_process(session, task_id)` | 同上 | claim `pending` → attempt → `_run_provider` → persist / retry |
| `dispatch_persisted_question_generation_task` | `app/services/interview_questions.py` | commit 后校验 PENDING 题纲 task → **`enqueue_ai_task(task.id)`** |
| 自动重试 | `_handle_process` 失败路径 | `process_ai_task.apply_async(args=[str(task.id)], countdown=...)` |
| 管理重试 | `retry_ai_task` | commit 后 **`enqueue_ai_task(task.id)`**（`ai_task.manage`） |

题纲 API 入口（唯一业务创建+分发路径）：

```113:139:backend/app/api/v1/endpoints/interview_ai.py
@router.post("/interview-rounds/{round_id}/question-set/generate", ...)
async def generate_question_set(...):
    task = await request_question_generation(...)
    return await _commit_then_dispatch(..., dispatch=dispatch_persisted_question_generation_task)
```

权限：`recruitment.manage`（写）；题纲读仍 `recruitment.manage` 或分配到轮次的 `interview.execute`。

### 2.3 现状：题纲前置（不变）

`request_question_generation` 仍强制：

- 轮次状态 ∈ `{SCHEDULED, CONFIRMED, IN_PROGRESS}`；
- 应聘已绑 **confirmed** 简历版本；
- 冻结 `job_version_id` + 完整 `score_dimensions` snapshot；
- 无 inflight 题纲 task（同 hash 幂等除外）。

### 2.4 现状：live 门禁（不因队列改动而放宽）

`interview_question_live_http_allowed()`（`app/services/ai_providers/dify.py`）要求同时成立：

- `ENVIRONMENT=development`；
- `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=true`；
- 题纲专用 Key + Workflow ID + `DIFY_API_BASE_URL` 非空；
- 四键输入合法；
- `job_title` / `jd_text` / `resume_text` 三者 **以同一授权前缀开头**（`UAT-CC-20260818` 或 `FICTIONAL-LIVE-20260818`）。

隔离数据集前缀 `UAT-CC-20260818-DIFY` **满足** `startswith("UAT-CC-20260818")`，无需改门禁常量即可用于 UAT；运营上仍只使用该隔离数据集（轮次 `683665ef-7801-4b35-b9ba-124b51cd441b`）。

### 2.5 现状：审计脱敏（不变）

题纲 stage8 仍走 `_write_stage8_raw` / `_stage8_public_payload`；live HTTP 成功/失败路径对 `provider=="dify"` 的题纲任务做最小脱敏（公开 JSONB 不含 JD/简历/题干正文；`SENSITIVE_AUDIT_KEYS` 含 `jd_text`）。**队列改动不得削弱此规则。**

---

## 3. 架构设计

### 3.1 新 Celery 任务：命名与注册

在 **`app/workers/ai_tasks.py`** 同一模块注册（与 `process_ai_task` 并列，`include` 不变）：

| 属性 | 值 |
|---|---|
| Python 函数名 | `process_sensitive_ai_task` |
| Celery `name=` | **`app.workers.ai_tasks.process_sensitive_ai_task`** |
| 装饰器 | `@celery_app.task(name="app.workers.ai_tasks.process_sensitive_ai_task", bind=True)` |
| 参数 | **`task_id: str`**（仅 `ai_tasks.id`，与现有一致） |
| 返回值 | `dict`（透传 `_handle_process` 或拒绝结果） |

**禁止**引入第二套处理实现；合法题纲任务必须调用既有：

```python
asyncio.run(_process_ai_task_async(UUID(task_id)))
# 等价于 _handle_process(session, task_id)
```

### 3.2 敏感入口行为（类型门禁）

`process_sensitive_ai_task` **在调用 `_process_ai_task_async` 之前** 必须只读加载 `AITask` 行（`get_ai_task_by_id`，无 attempt 亦可）：

| DB `task_type` | 行为 |
|---|---|
| `INTERVIEW_QUESTION_GENERATE` | 调用 `_process_ai_task_async(task_id)` |
| 其他任何类型 | **立即拒绝**：返回 `{"status": "rejected", "reason": "unsupported_task_type", "task_type": ...}`；**不得**调用 `_handle_process`、**不得**调用 `run_dify` / `run_mock` |

说明：拒绝路径 **不写** attempt、**不改** task status（除非将来单独规格要求）；仅记录 worker 日志。

### 3.3 默认入口：题纲安全转投（禁止执行）

`process_ai_task`（默认队列）在调用 `_handle_process` 之前，若只读发现 `task_type == INTERVIEW_QUESTION_GENERATE`，**不得**执行题纲。

**转投固定调用（同模块，禁止 services）：**

```python
process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=0)
```

- **不得**调用 `app.services.ai_tasks.enqueue_sensitive_question_task`（避免 worker→services.ai_tasks 依赖）。
- 该 `apply_async` **经静态 `task_routes`** 进入队列名 **`settings.celery_sensitive_queue_name`**（默认 `ai_sensitive`）；**不得**手写 `queue=` 覆盖路由，除非与 Settings 同一值且有测试锁定。

| 步骤 | 要求 |
|---|---|
| 禁止 | **不得**调用 `_handle_process` / `run_dify` / `run_mock`；类型判定可短开 session 只读 `AITask`，**不得** claim `pending→running` |
| 成功转投 | 同上 `process_sensitive_ai_task.apply_async(...)` **恰好一次**；返回 `{"status": "rerouted", "reason": "question_generate_requires_sensitive_queue", "task_id": ...}` |
| 转投失败 | **保留** DB 既有 **`pending`**（不 claim、不写 attempt、不改 status）；写持久化审计（§3.3.1）；返回 `{"status": "reroute_failed", ...}`；**另**打安全日志（无正文/Key） |
| 防循环 | 转投 **只发生一次**：`process_sensitive_ai_task` **不得**再投回 `process_ai_task` |

从而历史误投递到默认 `celery` 的题纲消息会被 **安全转投** 到敏感队列，而不是被丢弃或在普通 worker 上执行。

#### 3.3.1 转投失败的持久化审计（源码已具备，非新造）

只读核实：worker **未 claim、无 HTTP actor/IP** 时，仍可使用既有：

```39:63:backend/app/services/audit.py
async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    result: str,
    resource_type: str,
    request_context: RequestContext,
    actor_user_id: UUID | None = None,
    resource_id: str | None = None,
    changes: dict | None = None,
) -> None:
```

| 约束 | 源码事实 |
|---|---|
| HTTP 非必需 | `actor_user_id` **可为 `None`**；`RequestContext.ip_address` 默认可 `None` |
| Worker 已有合成上下文 | `_worker_request_context(task)` → `RequestContext(request_id=f"ai-task:{task.id}")`（`app/workers/ai_tasks.py`） |
| Session | 转投路径为判定类型本就需短生命周期 `AsyncSession`；失败时在 **同一 session** `await record_audit(...)` 后 `commit`；**仍不** claim / 不写 attempt |
| Import | worker 可 `from app.services.audit import record_audit, RequestContext`；`audit` **不** import workers / `services.ai_tasks` → **无** worker↔services.ai_tasks 循环 |
| **禁止** | 新审计表、Alembic、或假设必须有 HTTP `request.state` |

锁定调用（转投失败时）：

```python
await record_audit(
    session,
    action="ai_task.sensitive_reroute_failed",
    result="failure",
    resource_type="ai_task",
    request_context=_worker_request_context(task),  # 或等价 RequestContext(request_id=f"ai-task:{task.id}")
    actor_user_id=None,
    resource_id=str(task.id),
    changes={
        "ai_task_id": str(task.id),
        "task_type": task.task_type,
        "error_type": type(exc).__name__,
    },
)
```

**`changes` 仅允许**上列三键（或子集）；**禁止** JD/简历/题干/snapshot 正文、`api_key`、token、broker URL、异常 `str(exc)` 全文（若含敏感信息）。`record_audit` 内 `_scrub_value` 会 scrub 含 `password`/`token`/`api_key` 等 marker 的字符串，**不得**依赖 scrub 才塞入正文。

### 3.4 按 `task_type` 选择重试 Celery 入口（worker 内部）

自动重试发生在 **`_handle_process` 内部**（现状硬编码 `process_ai_task.apply_async`）。实现必须改为 **按已加载的 `task.task_type` 选择 Celery 入口**：

| `task_type` | 重试入队目标 |
|---|---|
| `INTERVIEW_QUESTION_GENERATE` | **`process_sensitive_ai_task.apply_async`**（经 `task_routes` → `celery_sensitive_queue_name`） |
| 其他 | **`process_ai_task.apply_async`**（默认 `celery`） |

管理重试 `retry_ai_task`（services 层）同样：题纲 → `enqueue_sensitive_question_task`；非题纲 → `enqueue_ai_task`。

**禁止**题纲自动重试再走默认 `process_ai_task`（否则依赖转投，增加一轮无意义延迟与失败面）。

#### 避免 worker ↔ services.ai_tasks 循环导入（三条路径自检）

现状：`app.services.ai_tasks.enqueue_ai_task` **函数内延迟** `from app.workers.ai_tasks import process_ai_task`；若 worker **顶层或自动重试路径**再 import `enqueue_*`，可形成循环。

| 路径 | 入队方式 | 是否 import `services.ai_tasks` |
|---|---|---|
| **默认入口转投**（§3.3） | 同模块 `process_sensitive_ai_task.apply_async` | **否** |
| **自动重试**（`_handle_process`） | 同模块 `_enqueue_retry_for_task` → `process_sensitive_ai_task.apply_async` / `process_ai_task.apply_async` | **否** |
| **管理重试**（`retry_ai_task`） | services 内 `enqueue_sensitive_question_task` / `enqueue_ai_task`（函数内延迟 import worker） | 仅 services→worker，**worker 不反向顶层 import** |

Worker 本地助手（锁定）：

```python
def _enqueue_retry_for_task(task: AITask, *, countdown: int) -> None:
    if task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        process_sensitive_ai_task.apply_async(args=[str(task.id)], countdown=countdown)
    else:
        process_ai_task.apply_async(args=[str(task.id)], countdown=countdown)
```

**禁止**：在 `app/workers/ai_tasks.py` **顶层或转投/自动重试路径** `from app.services.ai_tasks import enqueue_*`。
**允许**：worker 使用既有 `from app.services.audit import record_audit, RequestContext`（与现状一致；`audit` 不依赖 worker）。

### 3.5 `task_routes` / `task_queues` 与同一配置源

`celery_app.conf.update(...)` 中 **`task_routes`、显式 `task_queues`（如需）、以及运维文档中的 worker `-Q`** 全部读取 **同一个** Settings 属性：

| 项 | 值 |
|---|---|
| Settings | `celery_sensitive_queue_name: str`（env `CELERY_SENSITIVE_QUEUE_NAME`） |
| 默认 | **`ai_sensitive`** |
| 路由 | `task_routes = {"app.workers.ai_tasks.process_sensitive_ai_task": {"queue": settings.celery_sensitive_queue_name}}` |
| 队列声明（如需） | `task_queues` 中敏感队列名 **等于** `settings.celery_sensitive_queue_name`；默认队列仍为 Celery 内置 **`celery`** |
| `process_ai_task` / purge | **不**写入 `task_routes` → 继续默认 **`celery`** |

- **禁止**在路由里硬编码与 Settings 不一致的字面量（测试可用默认值断言；覆盖配置时 route 目标必须等于 Settings）。
- **禁止**把 JD/简历任务路由到敏感队列。
- **配置变更需重启** API 进程与 **全部** Celery worker 进程后生效（Settings / `celery_app.conf` 在进程启动时加载）。

### 3.6 复用关系（禁止复制）

以下逻辑 **只存在于** `_handle_process` 及其现有 callees，敏感入口 **不得 fork**：

- pending claim / attempt 编号；
- `_prepare_stage8_provider_input` → `_question_memory_input`；
- `_run_provider` → `run_dify` / live 门禁；
- `_after_task_success` → `persist_question_generation_result`；
- 自动重试 `should_auto_retry` + countdown（经 §3.4 按类型选入口）；
- `_write_stage8_raw` / 加密审计；
- `output_invalid` / 失败路径。

---

## 4. 分发与状态

### 4.1 唯一业务分发点（题纲创建后）

**仅**下列路径在 DB commit 成功后向 **`process_sensitive_ai_task`** 入队：

1. `POST /interview-rounds/{round_id}/question-set/generate`
   → `request_question_generation` → commit → **`dispatch_persisted_question_generation_task`**

实现要求：

- 新增 **`enqueue_sensitive_question_task(task_id: UUID, *, countdown: int = 0)`**（位于 `app/services/ai_tasks.py`，测试可 patch）：
  - `from app.workers.ai_tasks import process_sensitive_ai_task`
  - `process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=countdown)`
- **`dispatch_persisted_question_generation_task`** 将 `enqueue_ai_task(task.id)` **替换为** `enqueue_sensitive_question_task(task.id)`。
- **`enqueue_ai_task`** 保持只调用 `process_ai_task`（供 JD/简历/评分/维度等非敏感任务使用）。

### 4.2 内部重试与管理重试（必须同队列）

| 路径 | 题纲（`INTERVIEW_QUESTION_GENERATE`） | 其他 task_type |
|---|---|---|
| `_handle_process` 自动重试 | worker 内 **`_enqueue_retry_for_task`** → `process_sensitive_ai_task`（§3.4） | `process_ai_task` |
| `retry_ai_task`（`POST /admin/ai-tasks/{id}/retry`） | services **`enqueue_sensitive_question_task`** | `enqueue_ai_task` |
| 默认队列误投递 | 同模块 `process_sensitive_ai_task.apply_async` **转投一次**（§3.3），**不**执行、**不**经 services | 正常 `_handle_process` |

题纲自动重试 / 转投 **禁止**经 `enqueue_ai_task` 或再投 `process_ai_task`。
`retry_ai_task` 仍受 **`ai_task.manage`** 与状态约束；**不**新增「执行未失败 task」能力。

### 4.3 明确禁止的分发方式

- **不**新增 HTTP：`POST .../execute`、`POST .../run`、`POST .../dispatch` 等按任意 ID 触 worker 的端点。
- **不**提供 Celery `call` / 脚本直接 `apply_async` 作为产品能力；UAT 文档只描述 **经 generate API 创建** 后的单消息执行。
- **不**把 `3556206d-138b-40f6-9b23-97fce178a32e` 纳入任何重试、取消、迁移或清理流程（该 task 为 **`running`**，且 `cancel_ai_task` 仅接受 **`pending`**）。

### 4.4 状态与 ID 关联（不变）

- 业务主键：`ai_tasks.id`（UUID，入参 `task_id: str`）。
- 业务关联：`business_type=interview_round`，`business_id=round_id`。
- Celery message body：`["<ai_tasks.id>"]`；**不**落库 Celery task UUID。
- 题纲 version：`interview_question_versions.ai_task_id` UNIQUE → 一 task 至多一成功版本。

---

## 5. 配置与部署

### 5.1 最小配置（单一真源）

| 配置项 | Python 属性 | 环境变量 | 默认 |
|---|---|---|---|
| 敏感队列名 | `celery_sensitive_queue_name: str` | `CELERY_SENSITIVE_QUEUE_NAME` | **`ai_sensitive`** |
| Broker | 既有 `CELERY_BROKER_URL` / `celery_broker_url` | 不变 | — |

**同一值必须用于：**

1. `celery_app.conf.task_routes[...]["queue"]`；
2. 显式 `task_queues` 中敏感队列名（若声明）；
3. 敏感 worker 启动参数 `-Q <celery_sensitive_queue_name>`（运维脚本/文档用 Settings 当前值，默认即 `ai_sensitive`）。

- `.env.example` **只**增加变量名与注释；**不写**真实 broker URL。
- **不**为敏感队列单独引入第二 broker（第一期 Redis 同一实例、不同 list key 即可）。
- **更改 `CELERY_SENSITIVE_QUEUE_NAME` 后必须重启** API 与所有 Celery worker；旧队列名上的残留消息 **不会**自动迁移。

### 5.2 Worker 命令（隔离）

令 `SENSITIVE_Q` = Settings `celery_sensitive_queue_name`（默认 `ai_sensitive`）。

**普通 worker**（常驻或非 UAT 环境）：

```bash
celery -A app.workers.celery_app worker -Q celery -l info
```

**敏感 worker**（仅 UAT 短生命周期）：

```bash
celery -A app.workers.celery_app worker -Q "$SENSITIVE_Q" -l info --concurrency=1 --prefetch-multiplier=1
```

硬性要求：

| 规则 | 说明 |
|---|---|
| 普通 worker **不得** `-Q $SENSITIVE_Q` | 避免误消费题纲 |
| 敏感 worker **不得** `-Q celery` 或 `-Q celery,$SENSITIVE_Q` | 避免消费 JD/简历/评分或「全队列」 |
| `-Q` 与 `task_routes` | **必须**等于同一 `celery_sensitive_queue_name` |
| UAT 敏感 worker | **`concurrency=1`**、**`prefetch-multiplier=1`** |
| UAT 结束 | **停止敏感 worker 进程**；敏感队列中未消费消息 **保留**；普通 worker 不订阅该队列，且默认入口对题纲仅转投不执行 |

### 5.3 启动前准入（队列深度）

使用 broker **只读** `LLEN`（**不** `LRANGE` 消息体、不 purge、不 consume）；键名为 **当前** `celery_sensitive_queue_name`：

| 检查点 | 条件 | 失败动作 |
|---|---|---|
| T0：启动敏感 worker **前** | `LLEN($SENSITIVE_Q) == 0` | **不得启动**敏感 worker |
| T1：`generate` API commit **后**、启动敏感 worker **前** | `LLEN($SENSITIVE_Q) == 1` | 若 ≠1：**不得启动**；查 DB inflight / 是否重复 dispatch |
| T2：目标 task 完成后 | 停止敏感 worker；可选再 `LLEN` 应为 0 | 若 >0：按 §7 停止并调查，**不得**用普通 worker 清队列 |

**无法**从 LLEN 关联具体 `task_id`（不读 body）；关联以 **DB `ai_tasks.id` + 仅一条 pending + 队列深度=1** 的 operational 规则为准。

默认队列 `celery` 的 LLEN **不作** UAT 准入硬条件（可能存在无关 JD/简历任务）；UAT **不得**启动订阅 `celery` 的 worker。

---

## 6. Dify 与数据边界

### 6.1 Live 与 mock

- 队列隔离 **不改变** `run_dify()` 对题纲的门禁分支；关闭 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED` 仍 mock。
- **`INTERVIEW_ROUND_ANALYZE`** 继续无条件 mock；**不得**因敏感 worker 存在而 live 分析。

### 6.2 UAT 数据白名单

受控 live **仅**允许使用已隔离数据集（前缀 **`UAT-CC-20260818-DIFY`**）：

| 实体 | 参考 ID（开发库） |
|---|---|
| 轮次 | `683665ef-7801-4b35-b9ba-124b51cd441b`（`SCHEDULED`） |
| 应聘 | `75b2cd3b-fa24-433b-bd11-33f351c72a48` |
| 岗位 | `93b2cc44-8d3f-4c3f-99c1-2f43ade759c6` |

**禁止**对非前缀历史数据、非目标 running task 关联轮次发 live。

### 6.3 凭据

- 题纲仍 **仅** `DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY` + `DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID`；
- **禁止**回退通用 `DIFY_API_KEY`；
- 规格/仓库/测试 **不写**真实 Key、Workflow ID、broker 密码。

---

## 7. 失败、停止与回滚

### 7.1 立即停止条件

出现下列任一情况，**停止敏感 worker**（不再消费），并按只读方式留存 DB/broker 元数据：

1. **`LLEN($SENSITIVE_Q)` 与预期不符**（T0/T1/T2 失败；`$SENSITIVE_Q` = `celery_sensitive_queue_name`）。
2. **`process_sensitive_ai_task` 收到非题纲 `task_type`**（说明路由或消息污染）。
3. **DB 目标 task 非 `pending`**（已被其他进程 claim、或状态异常）。
4. **默认入口转投失败**（`reroute_failed`）且消息仍只在默认队列侧 — UAT **不得**依赖反复启动普通 worker。
5. **Dify/live 门禁拒绝**（`interview_question_live_unauthorized` 等）— 允许 task 走失败/mock 路径，但 UAT 视为未通过，**不得**反复启动 worker 刷队列。
6. **`output_invalid` / 持久化校验失败** — 不写成功题纲版本；UAT 终止。
7. **审计断言失败**（公开/加密载体出现 JD/简历/题干明文）。

### 7.2 停止后 broker 行为

- 消息留在 **`celery_sensitive_queue_name`**；普通 worker **不订阅**该队列 → **不会**被误消费。
- 默认队列上的题纲误投递由 §3.3 **转投**处理；转投失败时 DB 保持 **`pending`**，禁止 SQL 强改。
- **禁止** Redis `DEL` / Celery purge / 手动 `LRANGE`+删除。
- **禁止** SQL 更新 `3556206d-138b-40f6-9b23-97fce178a32e` 或任何非目标 task 的 status。

### 7.3 回滚开关

1. `.env` 设 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=false`（可保留 Key/ID）。
2. 停止敏感 worker。
3. 代码回滚时：题纲 dispatch **不得**长期滞留「仅 sensitive 无 fallback」而无文档；回滚后若未部署 sensitive 路径，generate API 应仍可通过 **`pending_dispatch`** 状态人工排查（既有 `dispatch_status` 语义）。

---

## 8. 测试与验收

### 8.1 自动化测试（禁止真实 Dify HTTP）

| 用例 | 断言 |
|---|---|
| `task_routes`（默认） | `process_sensitive_ai_task` → queue **`ai_sensitive`**；`process_ai_task` 无敏感路由 |
| `task_routes`（覆盖） | monkeypatch `CELERY_SENSITIVE_QUEUE_NAME` / Settings → route 的 `queue` **等于** `celery_sensitive_queue_name`；文档化 worker `-Q` 订阅同一字符串 |
| `task_queues`（若声明） | 敏感队列名与 Settings **一致** |
| `enqueue_sensitive_question_task` | patch 后 `apply_async` 目标为 **`process_sensitive_ai_task`**，args=`[str(task_id)]` |
| `dispatch_persisted_question_generation_task` | 调用 **`enqueue_sensitive_question_task`**，非 `enqueue_ai_task` |
| `process_sensitive_ai_task` 非题纲 | **不**调用 `_handle_process` / `_process_ai_task_async` |
| `process_sensitive_ai_task` 题纲 | 调用 **`_process_ai_task_async`** 一次 |
| `process_ai_task` + 题纲 ID（转投成功） | **不**调用 `_handle_process` / Dify / `enqueue_sensitive_question_task`；同模块 **`process_sensitive_ai_task.apply_async` 恰好一次**（经 `task_routes`→`celery_sensitive_queue_name`）；返回 `status=rerouted`；DB 仍 `pending`（未 claim） |
| `process_ai_task` + 题纲 ID（转投失败） | **不**调用 `_handle_process`；DB **仍 `pending`**；调用 **`record_audit`**（`action=ai_task.sensitive_reroute_failed`，`actor_user_id is None`，`changes` 仅 `ai_task_id`/`task_type`/`error_type`）；返回 `reroute_failed` |
| `process_ai_task` 转投防循环 | 转投目标为敏感任务；敏感侧 **不得**再 `apply_async` 回 `process_ai_task` |
| `_handle_process` 题纲自动重试 | **`_enqueue_retry_for_task`** → **`process_sensitive_ai_task.apply_async`**；**不**调用 `process_ai_task.apply_async`；**不** import `enqueue_*` |
| `_handle_process` 非题纲自动重试 | 仍 `process_ai_task.apply_async` |
| `retry_ai_task` + 题纲 | services **`enqueue_sensitive_question_task`**（延迟 import worker），非 `enqueue_ai_task` |
| `enqueue_ai_task` + JD/简历 task | 仍 **`process_ai_task`**，队列默认 **celery** |
| Settings 默认 | `celery_sensitive_queue_name == "ai_sensitive"`（`.env.example` 仅空值） |
| 无 worker→services.ai_tasks 循环 | worker 顶层/转投/自动重试路径 **无** `from app.services.ai_tasks import ...`；允许 `from app.services.audit import ...` |

所有 Dify 相关测试继续 monkeypatch `_post_workflow` / `httpx`；**零** outbound HTTP。

### 8.2 受控 UAT 操作步骤（人工，非本规格执行）

1. 确认 Git 干净；**不**改 `.env` 以外的配置（live 开关与 Key 由人工按 live 规格写入本地 `.env`）。
2. T0：`LLEN($SENSITIVE_Q)==0`（`$SENSITIVE_Q` = 当前 `celery_sensitive_queue_name`）；确认 **未**启动任何 worker。
3. 对轮次 `683665ef-7801-4b35-b9ba-124b51cd441b` 调用 **`POST .../question-set/generate`**（`recruitment.manage`）；记录返回 `task_id`。
4. T1：`LLEN($SENSITIVE_Q)==1`；DB 该 task 为 **`pending`**。
5. 启动敏感 worker：`-Q $SENSITIVE_Q --concurrency=1 --prefetch-multiplier=1`（与 `task_routes` 同一配置值）。
6. 轮询 **`GET /admin/ai-tasks/{task_id}`**（`audit.read`）：status/attempt/`provider_run_id`；**不**读 raw/密文。
7. 题纲：**`GET .../question-set`** / version detail（`recruitment.manage` 或授权 `interview.execute`）。
8. 完成后 **停止**敏感 worker；T2：`LLEN($SENSITIVE_Q)==0`（可选）。
9. 关闭 live 开关；确认 mock 回归测试仍绿。

### 8.3 只读验收清单

| 项 | 期望 |
|---|---|
| 轮次仍为 `SCHEDULED` 或合法后续状态 | 是 |
| 题纲 version 存在且 `ai_task_id` 指向本次 task | live/mock 成功时 |
| 无额外 inflight 题纲 task | 是 |
| `3556206d-...` 仍为 **`running`**，未被本 UAT 改动 | 是 |
| 公开 admin API 无 JD/简历/题干/Key | 是 |

---

## 9. 符号锁定表

| 符号 | 值 |
|---|---|
| 默认队列 | `celery` |
| 敏感队列 Settings | **`celery_sensitive_queue_name`**（env `CELERY_SENSITIVE_QUEUE_NAME`，默认 **`ai_sensitive`**） |
| 默认 Celery 任务名 | `app.workers.ai_tasks.process_ai_task` |
| 敏感 Celery 任务名 | **`app.workers.ai_tasks.process_sensitive_ai_task`** |
| 题纲 task_type | `INTERVIEW_QUESTION_GENERATE` |
| 题纲 dispatch | `dispatch_persisted_question_generation_task` → **`enqueue_sensitive_question_task`** |
| 题纲自动重试 | worker **`_enqueue_retry_for_task`** → `process_sensitive_ai_task`（禁止默认入口） |
| 默认入口遇题纲 | 同模块 `process_sensitive_ai_task.apply_async` 一次 → `rerouted`；失败则 `pending` + `record_audit`（§3.3.1）+ `reroute_failed` |
| 非目标 running task（不处理） | `3556206d-138b-40f6-9b23-97fce178a32e` |
| UAT 隔离轮次 | `683665ef-7801-4b35-b9ba-124b51cd441b` |
| UAT 前缀 | `UAT-CC-20260818-DIFY`（满足 live 常量 `UAT-CC-20260818`） |

---

## 10. 自检清单（规格完成度）

- [x] 无 TBD / 无占位符
- [x] 无真实 Key、token、broker 密码、JD/简历正文
- [x] 无 Alembic / 无 Celery UUID 表 / 无前端改动范围
- [x] 第一期仅 `INTERVIEW_QUESTION_GENERATE`；分析 mock；JD/简历仍默认队列
- [x] 新任务命名与 `task_routes`/`task_queues`/`-Q` 共用 `celery_sensitive_queue_name`
- [x] 敏感入口非题纲拒绝；题纲复用 `_handle_process`
- [x] 默认入口转投固定 `process_sensitive_ai_task.apply_async`（不经 services enqueue）
- [x] 转投失败用既有 `record_audit`（actor 可空 + `_worker_request_context`）；无新表/迁移
- [x] 转投 / 自动重试 / 管理重试三条路径均无 worker→services.ai_tasks 顶层循环导入
- [x] 仅 generate 业务 dispatch；无任意 ID 执行 HTTP
- [x] 双 worker 隔离 + UAT concurrency/prefetch/停止条件 + 队列深度准入
- [x] live 门禁、UAT 数据边界、审计脱敏不变
- [x] 失败停止、禁止 purge/SQL/处理 running 非目标 task
- [x] 测试含转投一次、自动重试不走默认入口、配置覆盖一致性
- [x] 禁止真实 Dify 自动化边界
