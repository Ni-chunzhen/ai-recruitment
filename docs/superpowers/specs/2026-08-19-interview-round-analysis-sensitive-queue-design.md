# 单轮分析进敏感队列设计规格

基线：当前工作区 `main` @ `0fea0bf`（题纲敏感队列、AsyncSession/stale 恢复已合入；分析仍走默认 `celery`）。

本规格只定义：**将 `INTERVIEW_ROUND_ANALYZE` 纳入既有 `ai_sensitive` 敏感队列与入口/转投/重试路径**，并 **补齐 `TaskType` Literal**。不写业务实现、不改 `.env` 真实值、不启动 worker、不调用 Dify、不创建/处理 AI task。

关联（本规格不重复改写其已锁定语义，仅声明继承或差分）：

| 文档 | 关系 |
|---|---|
| `docs/superpowers/specs/2026-08-19-sensitive-ai-queue-design.md` | **差分覆盖**：其 §1.2 / §1.3 / §6.1 中「ANALYZE 不进敏感队列」条款 **由本规格取代**；队列名、`process_sensitive_ai_task`、`task_routes`、双 worker 隔离、转投审计骨架 **继承** |
| `docs/superpowers/specs/2026-08-16-stage-8-batch-1-interview-ai-design.md` | 分析门禁、契约、脱敏、七表语义 **继承** |
| `docs/superpowers/specs/2026-08-19-async-persistence-stale-task-recovery-design.md` | `set_committed_value`、`persist_failed`、终态所有权、mark-stale-failed **继承且不得削弱** |
| `docs/superpowers/specs/2026-08-18-interview-question-live-dify-design.md` | 题纲 live 门禁 **不变**；本规格 **不** 为分析开通 live |

---

## 1. 范围

### 1.1 目标

1. **`INTERVIEW_ROUND_ANALYZE` 与题纲同走** `settings.celery_sensitive_queue_name`（默认 **`ai_sensitive`**）：业务 dispatch、默认队列误投递转投、自动重试、管理重试，全部进入 **`process_sensitive_ai_task`**。
2. **敏感入口白名单仅两类**：`INTERVIEW_QUESTION_GENERATE` 与 `INTERVIEW_ROUND_ANALYZE`；其它类型 **拒绝**（不 claim、不跑 provider）。
3. 分析 **继续无条件 mock**（`run_dify` 对 ANALYZE 短路 `run_mock`）；**禁止** 为分析开通 Dify live、敏感专用 Key、或改 YAML/workflow。
4. **不削弱** 既有：转写确认门禁、分析 STALE 动态判定、`persist_failed` 脱敏终态、worker 终态所有权（`_reassert_running_ownership_for_terminal` / `skipped_stale_owner`）。
5. 补齐 Schema **`TaskType` Literal**，使含 `task_type` 的 admin/业务响应不再依赖 `# type: ignore` 掩盖缺字面量。

### 1.2 第一期范围（本规格后）

| 任务类型 | 队列 | Celery 入口 | Provider |
|---|---|---|---|
| `INTERVIEW_QUESTION_GENERATE` | **`ai_sensitive`** | `process_sensitive_ai_task` | 既有 live 门禁 / mock（不变） |
| `INTERVIEW_ROUND_ANALYZE` | **`ai_sensitive`** | `process_sensitive_ai_task` | **仅 mock** |
| `JD_PARSE` / `SCORE_DIMENSION_RECOMMEND` / `RESUME_PARSE` / `RESUME_SCORE` | 默认 `celery` | `process_ai_task` | 既有规则不变 |

### 1.3 非目标（硬性）

- **不**做 Alembic / 表结构 / 新列 / 新 Check。
- **不**改前端、候选人中心、时间轴文案。
- **不**做多轮综合分析、录用/淘汰/Offer、人工决策 API/表。
- **不**改 Dify workflow YAML、题纲 live 门禁常量、简历/JD Dify 回退规则；**不**为分析增加任何 live 开关或专用 Key。
- **不**新增「按任意 `ai_task_id` 执行」HTTP/CLI。
- **不**持久化 Celery AsyncResult UUID。
- **不**要求普通 worker 订阅敏感队列；**禁止** `-Q celery,ai_sensitive` 或等价全队列消费。
- **不**在本规格实施或 UAT 中处置、retry、cancel、mark-stale、SQL/Redis 干预：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`

---

## 2. 源码事实（实现必须对齐的现状差分）

下列为基线 `0fea0bf` 只读事实；实现须消除「分析仍走默认队列」的差分，**不得**回退题纲已入敏感队列的行为。

| 符号 / 路径 | 现状 | 本规格要求 |
|---|---|---|
| `dispatch_persisted_analysis_generation_task` | `enqueue_ai_task` → `process_ai_task` | 改为敏感入队（§4.1） |
| `dispatch_persisted_question_generation_task` | 已 `enqueue_sensitive_question_task` | **保持**敏感入队 |
| `_process_sensitive_ai_task_async` 门禁 | 仅允许 `INTERVIEW_QUESTION_GENERATE` | 允许 **题纲 ∪ 分析**（§3.2） |
| `_maybe_reroute_question_from_default` | 仅题纲转投 | 题纲 **与** 分析均转投（§3.3） |
| `_enqueue_retry_for_task` | 仅题纲 → sensitive；分析走默认 | 题纲 **与** 分析 → sensitive（§3.4） |
| `retry_ai_task` | 仅题纲 `enqueue_sensitive_question_task` | 题纲 **与** 分析均敏感入队（§4.2） |
| `run_dify` + ANALYZE | 无条件 `run_mock` | **保持**；禁止改成 live |
| `TaskType`（`schemas/ai_task.py`） | 仅 JD/维度/简历四字面量 | **补齐**两阶段 8 类型（§5） |
| 分析门禁 / STALE / persist | 已在 service + worker | **保持**（§6） |

分析 API（不变）：

```236:262:backend/app/api/v1/endpoints/interview_ai.py
@router.post("/interview-rounds/{round_id}/analysis/generate", ...)
async def generate_analysis(...):
    task = await request_analysis_generation(...)
    return await _commit_then_dispatch(..., dispatch=dispatch_persisted_analysis_generation_task)
```

权限不变：写 `recruitment.manage`；读 `recruitment.manage` 或已分配轮次的 `interview.execute`。

---

## 3. 架构设计（差分）

### 3.1 不新建 Celery 任务

- **继续**唯一敏感入口：`app.workers.ai_tasks.process_sensitive_ai_task`。
- **继续** `task_routes` → `settings.celery_sensitive_queue_name`（默认 `ai_sensitive`）。
- **禁止**为分析再注册第二 Celery `name=` 或第二队列。

### 3.2 敏感入口白名单（锁定）

在调用 `_process_ai_task_async` 之前只读加载 `AITask`：

| DB `task_type` | 行为 |
|---|---|
| `INTERVIEW_QUESTION_GENERATE` | 调用 `_process_ai_task_async(task_id)` |
| `INTERVIEW_ROUND_ANALYZE` | 调用 `_process_ai_task_async(task_id)` |
| 其他任何类型 | **立即拒绝**：`{"status": "rejected", "reason": "unsupported_task_type", "task_type": ...}`；**不得** `_handle_process` / `run_dify` / `run_mock`；**不** claim、**不**写 attempt |

锁定集合名（实现可用等价 frozenset，测试须断言集合相等）：

```python
SENSITIVE_AI_TASK_TYPES = frozenset({
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
})
```

拒绝路径仅 worker 日志；**不写**成功审计（与题纲敏感队列规格一致）。

### 3.3 默认入口转投（题纲 + 分析）

`process_ai_task` → `_process_default_ai_task_async` 在 `_handle_process` 之前：

- 若 `task_type ∈ SENSITIVE_AI_TASK_TYPES`：**不得**执行；同模块
  `process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=0)` **恰好一次**。
- **不得**经 `services.ai_tasks.enqueue_*`（避免 worker→services 循环）。
- **不得**手写与 Settings 不一致的 `queue=`。

| 结果 | 要求 |
|---|---|
| 转投成功 | `{"status": "rerouted", "reason": "interview_ai_requires_sensitive_queue", "task_id": ...}`；DB 仍 `pending`（未 claim） |
| 转投失败 | DB 仍 `pending`；`record_audit` `action=ai_task.sensitive_reroute_failed`，`actor_user_id=None`，`changes` 仅允许 `ai_task_id` / `task_type` / `error_type`；返回 `reroute_failed` |
| 防循环 | 敏感入口 **不得**再投回 `process_ai_task` |

**Reason 字符串锁定**：统一为 **`interview_ai_requires_sensitive_queue`**（题纲与分析共用）。既有测试若仍断言旧 reason `question_generate_requires_sensitive_queue`，实现批次须同步改断言；**不得**长期保留两套 reason。

助手命名：可将 `_maybe_reroute_question_from_default` **重命名**为 `_maybe_reroute_sensitive_from_default`（或保留旧名但行为覆盖两类）；测试按行为锁定，不强制函数名字符串，但符号表推荐新名。

### 3.4 自动重试入口（锁定）

```python
def _enqueue_retry_for_task(task: AITask, *, countdown: int) -> None:
    if task.task_type in SENSITIVE_AI_TASK_TYPES:
        process_sensitive_ai_task.apply_async(args=[str(task.id)], countdown=countdown)
    else:
        process_ai_task.apply_async(args=[str(task.id)], countdown=countdown)
```

- 分析 retryable 失败 **禁止**再走默认 `process_ai_task`（不得依赖「先默认再转投」）。
- Worker 顶层/转投/自动重试路径 **禁止** `from app.services.ai_tasks import enqueue_*`。

### 3.5 复用关系（禁止 fork）

敏感入口对分析 **必须**复用既有 `_handle_process` 链路，包括但不限于：

- claim / attempt；
- `_prepare_stage8_provider_input` → `_analysis_memory_input` / `load_analysis_provider_input`；
- `_run_provider` → `run_dify`（ANALYZE→mock）；
- `_after_task_success` → `persist_analysis_generation_result`（含 `set_committed_value`）；
- `persist_failed` 与 `_reassert_running_ownership_for_terminal`；
- `_write_stage8_raw` / 公开 JSONB 仅元数据。

**禁止**复制第二套分析 worker。

---

## 4. 分发与管理重试

### 4.1 业务分发点

| 路径 | 入队 |
|---|---|
| `dispatch_persisted_question_generation_task` | 敏感入队（已有，保持） |
| `dispatch_persisted_analysis_generation_task` | **改为**敏感入队（本规格核心差分） |
| JD/简历等 | 仍 `enqueue_ai_task` → `process_ai_task` |

### 4.2 共享 enqueue 符号（锁定）

新增（或将现有题纲 enqueue **重命名并保留薄别名**）统一助手：

| 符号 | 行为 |
|---|---|
| **`enqueue_sensitive_interview_ai_task(task_id, *, countdown=0)`** | `process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=countdown)`（函数内延迟 import worker） |
| `enqueue_sensitive_question_task` | **必须**改为调用上述统一助手（别名），避免两处 `apply_async` 分叉 |

调用方：

- `dispatch_persisted_question_generation_task`
- `dispatch_persisted_analysis_generation_task`
- `retry_ai_task`：当 `task_type ∈ SENSITIVE_AI_TASK_TYPES`

`retry_ai_task` 权限/状态机不变（仍 `ai_task.manage`）；**不**新增执行任意 ID 能力。

### 4.3 明确禁止

- 分析成功路径 **不得**再调用 `enqueue_ai_task`。
- **不得**把分析消息发到默认 `celery` 后指望转投作为主路径（转投仅兜底误投递）。
- **不得**对 `dde1470f-…` / `3556206d-…` 做 retry / cancel / mark-stale / 入队。

---

## 5. `TaskType` Literal 补齐（锁定）

文件：`backend/app/schemas/ai_task.py`

```python
TaskType = Literal[
    "JD_PARSE",
    "SCORE_DIMENSION_RECOMMEND",
    "RESUME_PARSE",
    "RESUME_SCORE",
    "INTERVIEW_QUESTION_GENERATE",
    "INTERVIEW_ROUND_ANALYZE",
]
```

约束：

- 与 ORM/DB Check `ck_ai_tasks_task_type` 及 `TASK_TYPES` 常量集合 **一致**（不得只改 Literal 漏常量，或反之）。
- `AITaskSummaryOut` / `AITaskAdminDetailOut` 等使用 `task_type: TaskType` 的响应，在题纲/分析 cancel、retry、detail 路径上 **不得**再因 Literal 校验失败而在 DB 已更新后抛 ValidationError。
- **本规格不**借机扩大其它 Literal（如 status）；**不**改 mark-stale 最小 Out 策略（若该 Out 刻意不含 `task_type`，保持）。

---

## 6. 业务门禁与持久化约束（继承，不得削弱）

### 6.1 转写确认（启动分析）

`request_analysis_generation` 既有门禁 **全部保留**：

- 轮次 `status == COMPLETED`；
- `transcript_completion_mode == CONFIRMED_TRANSCRIPT`（无转写完成模式不可生成）；
- 存在 transcript 且 `current_confirmed_version_id` 可加载；
- 确认版中 `is_included_in_analysis` 明文片段 ≥1；总字符 ≤ `INTERVIEW_ANALYZE_MAX_CHARS`；
- 冻结 `job_version` 维度：`require_complete_analysis_anchors`（每维恰好 5 非空 anchors）+ `validate_dimension_weights`（合计 100±0.01）。

队列迁移 **不得**放宽上述任一条件。

### 6.2 STALE（读模型）

- STALE **仍为动态**：`analysis_version.transcript_version_id != transcript.current_confirmed_version_id`。
- **不**新增持久化 STALE 状态列；**不**因进敏感队列自动重算或清除 STALE。

### 6.3 终态所有权与 `persist_failed`

继承 `2026-08-19-async-persistence-stale-task-recovery-design.md`：

| 规则 | 锁定 |
|---|---|
| 持久化非契约异常 | task+attempt → `failed` / `error_code=persist_failed` / `non_retryable` / 脱敏 message；**禁止**自动重试入队 |
| 终态写入前 | `_reassert_running_ownership_for_terminal`；非持有则 `skipped_stale_owner`，**禁止**覆盖管理员 `stale_running_recovered` |
| 集合赋值 | `persist_analysis_generation_result` 继续 `set_committed_value`，禁止触发 AsyncSession lazyload |
| mark-stale-failed | 既有 admin 入口；**零** enqueue；本 UAT **不对**两条受保护 running 调用 |

### 6.4 Provider 与脱敏

- ANALYZE：`run_dify` **必须**继续无条件 `run_mock`；敏感 worker 存在 **不得**改为 HTTP。
- 内存可持转写正文；`input_snapshot` / 公开 JSONB **禁止**正文；成功正文进分析表 Fernet 列。
- 审计：`SENSITIVE_AUDIT_KEYS` + `SENSITIVE_VALUE_MARKERS` 职责分离不变；调用方只传 ID/计数/状态/错误码。

---

## 7. 配置与 Windows UAT

### 7.1 配置

- **不**新增环境变量；继续单一真源 `celery_sensitive_queue_name` / `CELERY_SENSITIVE_QUEUE_NAME`（默认 `ai_sensitive`）。
- **不**改 `.env` 真实值写入仓库；规格/测试 **不**含真实 Key、broker 密码、转写/题纲/分析正文。

### 7.2 Worker 隔离（继承 + Windows 锁定）

令 `SENSITIVE_Q` = `celery_sensitive_queue_name`。

**普通 worker**（若环境需要）：仅 `-Q celery`。

**敏感 worker（Windows UAT 唯一允许形态）**：

```text
celery -A app.workers.celery_app worker -Q <SENSITIVE_Q> -l info --pool=solo --concurrency=1 --prefetch-multiplier=1
```

| 规则 | 说明 |
|---|---|
| Windows UAT | **必须** `--pool=solo`；**禁止**依赖 prefork/gevent 作为本规格验收 |
| concurrency / prefetch | **必须** `1` / `1` |
| 敏感 worker | **不得**订阅 `celery` |
| 普通 worker | **不得**订阅 `SENSITIVE_Q` |
| UAT 结束 | **立即停止**敏感 worker |

### 7.3 队列深度准入（分析 UAT）

与题纲 UAT 同构，仅业务 API 换成分析 generate：

| 检查点 | 条件 | 失败 |
|---|---|---|
| T0 | `LLEN($SENSITIVE_Q)==0`；无敏感 worker | 不得启动 |
| T1 | 合法分析 generate 后 `LLEN==1` 且 DB task `pending`、`task_type=INTERVIEW_ROUND_ANALYZE` | 不得启动 |
| T2 | 终态后停 worker；可选 `LLEN==0` | 禁止 purge / 普通 worker 清队 |

只读 `LLEN`；**禁止** `LRANGE` 消息体、purge、消费非目标消息。

### 7.4 分析 UAT 数据前置（文档约束，本规格不执行）

启动分析 generate 前，目标轮次必须已满足 §6.1。开发库 R3（`27346824-…`）若仍为 `SCHEDULED` 且无确认转写，**不得**对本规格 UAT 直接 generate；须另案经正式 API 准备 COMPLETED + CONFIRMED_TRANSCRIPT 的隔离轮次，或使用测试库 fixture。

**仍禁止**触碰 `dde1470f-…`、`3556206d-…`。

---

## 8. 失败、停止与回滚

### 8.1 立即停止敏感 worker

1. `LLEN` 与 T0/T1/T2 预期不符；
2. 敏感入口收到 **非** `SENSITIVE_AI_TASK_TYPES`；
3. 目标 task 非预期 `pending`（被他进程 claim 等）；
4. 转投 `reroute_failed` 且 UAT 依赖普通 worker 清场；
5. `output_invalid` / `persist_failed` / 审计断言失败（公开载体出现转写/分析正文）；
6. 任何试图对两条受保护 running 的操作。

### 8.2 回滚

1. 停止敏感 worker；
2. 代码回滚后：分析 dispatch 若暂回 `enqueue_ai_task`，须同步恢复默认入口「不执行分析」或明确文档化临时风险；**推荐**回滚以整批队列差分为单位，避免「仅题纲敏感、分析半套」。
3. **禁止** SQL/Redis 强改受保护 task。

---

## 9. 测试与验收

### 9.1 自动化（零真实 Dify HTTP；零启动常驻 worker）

| 用例 | 断言 |
|---|---|
| 敏感入口允分析 | `task_type=INTERVIEW_ROUND_ANALYZE` → 调用 `_process_ai_task_async` 一次 |
| 敏感入口拒其它 | 如 `RESUME_SCORE` → `rejected` / `unsupported_task_type`；不跑 handle |
| 敏感入口仍允题纲 | 行为保持 |
| 默认入口转投分析 | ANALYZE → `rerouted` + `reason=interview_ai_requires_sensitive_queue`；`apply_async` 敏感一次；未 claim |
| 默认入口转投题纲 | 同新 reason；旧 reason 断言删除 |
| 分析自动重试 | `_enqueue_retry_for_task` → `process_sensitive_ai_task`；非 `process_ai_task` |
| `dispatch_persisted_analysis_generation_task` | 调用 `enqueue_sensitive_interview_ai_task`（或经题纲别名），**非** `enqueue_ai_task` |
| `retry_ai_task` + ANALYZE | 敏感入队 |
| `run_dify` + ANALYZE | 仍 mock；patch 断言 **零** `_post_workflow` |
| `TaskType` Literal | 含两阶段 8 字面量；构造 `AITaskSummaryOut(task_type=ANALYZE|QUESTION, …)` 校验通过 |
| 门禁回归 | 非 COMPLETED / 非 CONFIRMED_TRANSCRIPT / 无 included 片段 → 仍拒绝启动（既有 service 测保持绿） |
| persist/ownership | 既有 `persist_failed` / `skipped_stale_owner` / `set_committed_value` 测保持绿 |
| 无循环导入 | worker 转投/重试路径不顶层 import `services.ai_tasks` |

### 9.2 UAT（人工；Windows solo；本规格不执行）

1. Git 干净；不改仓库内密钥；不触碰两条受保护 running。
2. T0：`LLEN($SENSITIVE_Q)==0`；无 worker。
3. 仅对 **已满足 §6.1** 的隔离轮次 `POST …/analysis/generate`；记录新 `task_id`（≠ 受保护 ID）。
4. T1：`pending` + `LLEN==1`。
5. 启动 §7.2 solo 敏感 worker；观察 mock 终态；**零** Dify HTTP。
6. 停 worker；确认分析 version 或可解释 `output_invalid`/`persist_failed`；受保护 running 未变。

---

## 10. 符号锁定表

| 符号 | 值 |
|---|---|
| 敏感队列 Settings | `celery_sensitive_queue_name`（默认 `ai_sensitive`） |
| 敏感 Celery 任务 | `app.workers.ai_tasks.process_sensitive_ai_task` |
| 白名单集合 | `SENSITIVE_AI_TASK_TYPES` = `{INTERVIEW_QUESTION_GENERATE, INTERVIEW_ROUND_ANALYZE}` |
| 统一入队 | **`enqueue_sensitive_interview_ai_task`**；`enqueue_sensitive_question_task` 为别名 |
| 分析 dispatch | `dispatch_persisted_analysis_generation_task` → 统一敏感入队 |
| 转投 reason | **`interview_ai_requires_sensitive_queue`** |
| 转投失败审计 | `ai_task.sensitive_reroute_failed`（changes 三键） |
| Provider（分析） | **仅 mock** |
| Windows UAT worker | `--pool=solo --concurrency=1 --prefetch-multiplier=1` |
| 受保护 running（不处理） | `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`；`3556206d-138b-40f6-9b23-97fce178a32e` |
| `TaskType` | 六字面量（含 QUESTION + ANALYZE） |

---

## 11. 与既有敏感队列规格的明确差分

| 条款（旧 `2026-08-19-sensitive-ai-queue-design.md`） | 本规格 |
|---|---|
| §1.2 ANALYZE「不进入敏感队列」 | **废除**；ANALYZE **进入**敏感队列 |
| §1.3「不为 ANALYZE 开通敏感队列」 | **废除**；仍 **不开** live |
| §3.2 敏感入口仅题纲 | **扩展**为题纲 ∪ 分析 |
| §3.3 / §3.4 / §4 仅题纲转投与重试 | **扩展**为两类 |
| §6.1「ANALYZE 继续 mock」 | **保持** |
| 队列名 / Celery 任务名 / 双 worker 隔离 | **保持** |
| 转投 reason 旧字符串 | **统一替换**为 `interview_ai_requires_sensitive_queue` |

冲突时：**以本规格为准**（仅限上表差分范围）；其余继承旧规格。

---

## 12. 自检清单（规格完成度）

- [x] 无 TBD / 无「待定」占位 / 无矛盾双路径主方案
- [x] 无真实 Key、token、broker 密码、转写/题纲/分析正文
- [x] 无迁移、前端、综合分析、人工决策、Dify/YAML 修改范围
- [x] ANALYZE 与题纲同走 `ai_sensitive`、敏感入口、转投、自动/管理重试
- [x] 敏感入口仅题纲+单轮分析
- [x] 分析继续 mock，不接 Dify live
- [x] 转写确认、STALE、终态所有权、`persist_failed` 继承锁定
- [x] `TaskType` Literal 补齐锁定
- [x] Windows UAT 仅 solo + concurrency/prefetch=1
- [x] 明确不触碰 `dde1470f-…`、`3556206d-…`
- [x] 测试与符号表可实施；未要求本文件提交或执行 UAT
