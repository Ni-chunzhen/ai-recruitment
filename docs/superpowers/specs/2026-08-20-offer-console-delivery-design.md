# Offer Console 投递（阶段 10 / 方案 A）设计规格

基线：当前工作区已合入面后 `HiringDecision` + `pending_offer`、综合面试分析（迁移 **015**）；**无** Offer 表 / **无** `offer.*` 权限 / **无** SMTP / **无** MailProvider / **无** `mail_outbound` 队列。

本规格只定义：**阶段 10 最小安全 Offer 能力**——独立三表、人工草稿与确认发送、`ConsoleMailProvider` 唯一投递、独立 `mail_outbound` 队列与独立 worker 模块、1/5/30 分钟重试与发送尝试审计、权限/脱敏/前端、迁移 **016**、测试与 Windows UAT runbook。

本文件是规格，**不**含逐步实现计划；**不**改业务代码、**不**执行迁移、**不**启动 API/worker、**不**调用 Dify/SMTP、**不**读写/清理 Redis 默认队列或敏感队列、**不**触碰受保护历史 running。

方案锁定（方案 A）：**独立 `mail_outbound` 队列 + 独立邮件任务**；一期 **仅** `ConsoleMailProvider`（无真实外发），同时保留重试与 attempt 审计，并与 `ai_sensitive`、默认 `celery`、`ai_tasks` **完全隔离**。

关联（本规格不重复改写其已锁定语义，仅声明继承或差分）：

| 文档 | 关系 |
|---|---|
| 只读架构审计结论（Offer/通知能力审计会话） | **采纳**：方案 A；`pending_offer` 挂载；三表；Console-only；发送后不写 `hired`；复用 `recruitment.manage` |
| `docs/superpowers/specs/2026-08-20-post-interview-hiring-decision-design.md` | `pending_offer` 语义 **差分扩展**：一期仍不写 `hired`；本规格 **首次** 允许在 `pending_offer` 上创建/发送 Offer（Console）；HiringDecision API **不改** |
| `docs/superpowers/specs/2026-08-20-comprehensive-interview-analysis-design.md` | 综合分析门禁 **不变**（`pending_offer` 只读）；本规格 **不** 触发综合 regenerate |
| `docs/superpowers/specs/2026-08-19-interview-round-analysis-sensitive-queue-design.md` / 敏感队列规格 | AI 队列隔离 **继承且禁止复用**；邮件 **不得** 进入 `ai_sensitive` 或默认 `celery` |
| 面试邀约（`models/invitation.py` + `services/invitations.py`） | **范式参考**（版本加密、邮箱脱敏、幂等、审计、manage 写）；**禁止**复用邀约表/API；邀约仍为人工 `RECORDED_SENT`，与本规格「系统 attempt 投递」语义分离 |
| `docs/superpowers/specs/2026-08-18-candidate-center-design.md` | 候选人中心仅 `recruitment.manage` **继承**；Offer 入口挂详情 manage 区 |

---

## 1. 范围

### 1.1 目标

1. 新增 **`offers` / `offer_versions` / `offer_send_attempts`** 三表与独立服务/API；**不**复用 `InterviewInvitation*`、**不**复用 `AITask` / `ai_task_attempts`。
2. **仅**同时满足下列条件可 **创建** Offer：
   - `job_applications.pipeline_status == pending_offer`
   - `job_applications.status == in_progress`
   - 该应聘存在至少一条 `HiringDecision.decision == recommend_hire`，且创建时以 **最新一条**（按 `created_at` 降序、同刻再按 `id` 降序）`recommend_hire` 作为证据引用（写入 Offer 上的 `hiring_decision_id` 快照外键）
3. Offer 状态机锁定：`draft` → `ready` → `sending` → `sent` | `failed` | `voided`（见 §4）。
4. **确认发送** 后：冻结当前版本；创建 `OfferSendAttempt`；入队 **仅** `mail_outbound`；由 **独立** mail worker 调用 **唯一** provider `ConsoleMailProvider`。
5. 发送成功或失败后：**不**修改 `pipeline_status`（保持 `pending_offer`）；**不**写 `APPLICATION_STATUS_HIRED` / `hired`；**不**改 `HiringDecision`。
6. 权限：**仅** `recruitment.manage` 可创建/编辑/预览/确认发送/作废/列 attempt；**`interview.execute` 零 API、零 UI**（403 / 不可见）。
7. 失败自动重试间隔锁定为 **1 / 5 / 30 分钟**（见 §5.4）；达上限后 **仅** 人工创建新 attempt（禁止无限自动重试）。
8. 审计、API 响应、worker 日志、Console 输出元数据：**不得**出现邮件正文、明文邮箱、完整失败协议/堆栈原文。

### 1.2 第一期交付物（本规格后实现须覆盖）

| 层 | 交付 |
|---|---|
| Alembic | 迁移 **016**（`down_revision = 015_comprehensive_interview_analysis`）：建三表及必要索引/约束 |
| 模型 / 常量 | `Offer` / `OfferVersion` / `OfferSendAttempt`；状态/provider/错误码常量；`PIPELINE_STATUSES` **不**新增态 |
| Settings | `celery_mail_queue_name`（env `CELERY_MAIL_QUEUE_NAME`，默认 `mail_outbound`）；**无** SMTP 配置项 |
| Service | 创建、更新草稿、标 ready、确认发送、作废、list/get、预览解密门禁、人工重试 attempt |
| Provider | `ConsoleMailProvider` **唯一**实现；接口可扩展但一期 **禁止** 注册 SMTP |
| Worker | **新模块**（如 `app.workers.mail_tasks`）；Celery 任务名独立；`celery_app.include` 增加该模块且 **task_routes** 指向 mail 队列 |
| API | manage-only CRUD/发送/attempt 查询；错误映射 401/403/404/409/422 |
| 前端 | 候选人中心详情 `pending_offer` 区：Offer 面板（状态、脱敏收件人、预览、二次确认发送、attempt 表）；execute **不可见** |
| 测试 / UAT | 见 §9；规格只定义，本文件不执行 |

### 1.3 非目标（硬性）

- **不**接入 SMTP、第三方邮件 SaaS、站内通知、短信/IM。
- **不**做 Offer 附件、电子签、候选人接受/拒绝、入职流转。
- **不**写 `hired`；**不**新增流水态 `offer_sent`；**不**做 `pending_offer` → `interviewing` 撤销。
- **不**调用 Dify；**不**用 AI 生成正文；**不**从综合分析/单轮分析自动触发发送。
- **不**新增 `offer.*` 权限码（一期复用 `recruitment.manage`）。
- **不**把邮件任务写入 `ai_tasks`、**不**复用 `process_ai_task` / `process_sensitive_ai_task`、**不**入队 `ai_sensitive` 或默认 `celery`。
- **不**复用邀约 `record_sent` 作为 Offer「已发送」语义。
- **不**在默认 `celery` 队列执行本功能；**禁止** worker 使用 `-Q celery,ai_sensitive,mail_outbound` 或任意「消费全部队列」配置作为交付依赖。
- **不**在本规格实施或 UAT 中处置、retry、cancel、mark-stale、SQL/Redis 干预下列受保护 running：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- **不**消费、清理、窥探默认 `celery` 队列既有未知消息；**不**对 `ai_sensitive` 做 purge。

---

## 2. 源码事实（实现必须对齐）

| 符号 / 路径 | 现状 | 本规格 |
|---|---|---|
| Offer / MailProvider / SMTP Settings | **不存在** | **新增** Offer 三表 + Console provider + mail 队列配置；**禁止** SMTP Settings |
| `PIPELINE_PENDING_OFFER`（`models/resume.py`） | 已存在；语义「建议已确认、Offer 未创建/发送」 | **保持**流水字面量；发送成功后 **仍** 为 `pending_offer`（Offer 行状态表达已发送） |
| `APPLICATION_STATUS_HIRED` | 常量存在，无写入 | **禁止**本规格路径写入 |
| `create_hiring_decision` | `recommend_hire` → `pending_offer`；不建 Offer | **不改**；本规格在其后独立建 Offer |
| `InterviewInvitation*` / `record_sent` | 人工登记已外发；正文加密；邮箱脱敏 | **范式参考 only**；表/API **隔离** |
| `_mask_email`（`services/invitations.py`） | `local[0]***@domain` | Offer **必须** 等价脱敏规则（可抽共享工具，但不得把明文写入 Offer 表） |
| `encrypt_secret` / `decrypt_secret`（`services/crypto.py`） | 邀约/面试敏感字段 | Offer 正文 **必须** 使用 |
| `celery_app`（`workers/celery_app.py`） | `include=["app.workers.ai_tasks"]`；敏感路由 → `ai_sensitive` | **扩展** include + **仅** mail 任务路由 → `mail_outbound`；AI 路由 **不变** |
| `AI_TASK_RETRY_COUNTDOWNS` | `{1:10, 2:30}` **秒** | **禁止**复用；邮件用独立 **分钟** 表 |
| `PERMISSION_DEFINITIONS` | 无 `offer.*` | **不**新增；读写 **仅** `recruitment.manage` |
| 候选人中心详情 | `pending_offer` 文案「不提供发送能力」 | **改为** manage 可见 Offer 面板；仍禁 SMTP/Dify/自动发送文案 |
| 综合分析 generate | `pending_offer` 拒绝写 | **保持** |

邀约脱敏参考（行为对齐，勿耦合业务）：

```143:149:backend/app/services/invitations.py
def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().partition("@")
    if not local or not domain:
        return None
    return f"{local[0]}***@{domain}"
```

Celery 现状（须差分扩展，禁止把 mail 塞进 AI 模块）：

```7:28:backend/app/workers/celery_app.py
celery_app = Celery(
    "ai_recruitment",
    broker=settings.celery_broker_url,
    include=["app.workers.ai_tasks"],
)
# task_routes 仅 process_sensitive_ai_task → celery_sensitive_queue_name
```

---

## 3. 模型与迁移

### 3.1 迁移标识（锁定）

| 项 | 值 |
|---|---|
| 文件 | `backend/alembic/versions/016_offer_console_delivery.py`（实现时可微调文件名，**revision id 必须**含 `016` 语义） |
| `revision` | `016_offer_console_delivery` |
| `down_revision` | `015_comprehensive_interview_analysis` |
| 内容 | **仅**三表 + 索引/Check/FK；**不**改 `ai_tasks` check；**不**改 `PIPELINE_STATUSES` DB 枚举（流水仍为应用层字符串） |

### 3.2 常量（锁定）

```python
OFFER_STATUS_DRAFT = "draft"
OFFER_STATUS_READY = "ready"
OFFER_STATUS_SENDING = "sending"
OFFER_STATUS_SENT = "sent"
OFFER_STATUS_FAILED = "failed"
OFFER_STATUS_VOIDED = "voided"
OFFER_STATUSES = frozenset({...上述全部...})

MAIL_PROVIDER_CONSOLE = "console"
# 一期唯一合法 provider 值；代码路径禁止 smtp / ses / sendgrid 等

OFFER_ATTEMPT_STATUS_PENDING = "pending"
OFFER_ATTEMPT_STATUS_RUNNING = "running"
OFFER_ATTEMPT_STATUS_SUCCEEDED = "succeeded"
OFFER_ATTEMPT_STATUS_FAILED = "failed"
OFFER_ATTEMPT_STATUS_DEAD = "dead"  # 自动重试耗尽，待人工

MAIL_RETRY_COUNTDOWNS_SECONDS = {1: 60, 2: 300, 3: 1800}  # 第 1/2/3 次失败后的等待
MAIL_MAX_AUTO_ATTEMPTS = 4  # 同一「发送周期」内自动 attempt 上限（含首次；失败后最多再调度 3 次）
```

说明：第 *n* 次失败后，若 `n < MAIL_MAX_AUTO_ATTEMPTS`，则按 `MAIL_RETRY_COUNTDOWNS_SECONDS[n]` 入队下一次（n∈{1,2,3} → 60/300/1800）；第 **4** 次失败后标记周期耗尽（`dead` / Offer=`failed`），**不再**自动入队。

### 3.3 表 `offers`

| 列 | 类型 | 约束 / 说明 |
|---|---|---|
| `id` | UUID PK | |
| `application_id` | UUID FK → `job_applications.id` ON DELETE CASCADE | 索引；**一期每个 application 至多一条未 voided Offer**（见唯一约束） |
| `hiring_decision_id` | UUID FK → `hiring_decisions.id` ON DELETE RESTRICT | 创建时绑定的最新 `recommend_hire` |
| `status` | String(32) NOT NULL | ∈ `OFFER_STATUSES` |
| `current_version_id` | UUID NULL FK → `offer_versions.id` ON DELETE SET NULL（use_alter 防循环） | 当前可编辑或已冻结版本 |
| `recipient_email_masked` | String(255) NULL | **仅**脱敏；创建/刷新时由候选人邮箱计算 |
| `recipient_name` | String(128) NOT NULL | 候选人姓名快照（非敏感长文） |
| `lock_version` | Integer NOT NULL DEFAULT 1 | 乐观锁 |
| `created_by` / `updated_by` | UUID FK → `users.id` ON DELETE SET NULL | |
| `created_at` / `updated_at` | timestamptz NOT NULL | |
| `voided_at` | timestamptz NULL | |
| `void_reason_code` | String(64) NULL | 固定短码，禁止自由长文 |

**唯一约束（锁定）：** 部分唯一索引
`uq_offers_application_active` ON (`application_id`) WHERE `status NOT IN ('voided')`
（同一应聘同时只允许一条非作废 Offer。）

**禁止列：** 明文 `recipient_email`、薪资字段、附件指针、SMTP message-id 明文协议体、AI task id。

### 3.4 表 `offer_versions`

| 列 | 类型 | 约束 / 说明 |
|---|---|---|
| `id` | UUID PK | |
| `offer_id` | UUID FK → `offers.id` ON DELETE CASCADE | 索引 |
| `version_no` | Integer NOT NULL | 每 Offer 从 1 递增 |
| `subject_encrypted` | Text NOT NULL | `encrypt_secret` |
| `body_html_encrypted` | Text NOT NULL | 同上 |
| `body_text_encrypted` | Text NOT NULL | 同上 |
| `content_hash` | String(64) NOT NULL | subject+html+text 的 SHA-256（明文计算后只存 hash） |
| `template_code` | String(64) NOT NULL | 一期固定如 `offer_console_v1` |
| `template_version` | String(32) NOT NULL | 如 `"1"` |
| `frozen` | Boolean NOT NULL DEFAULT false | 确认发送时置 true；冻结后不可 UPDATE 正文列 |
| `created_by` | UUID FK → `users.id` ON DELETE SET NULL | |
| `created_at` | timestamptz NOT NULL | |

**唯一约束：** `uq_offer_versions_offer_version_no` ON (`offer_id`, `version_no`)。

**禁止列：** 明文 subject/body、附件 blob、外部 URL 凭证。

### 3.5 表 `offer_send_attempts`

| 列 | 类型 | 约束 / 说明 |
|---|---|---|
| `id` | UUID PK | |
| `offer_id` | UUID FK → `offers.id` ON DELETE CASCADE | 索引 |
| `offer_version_id` | UUID FK → `offer_versions.id` ON DELETE RESTRICT | 必须指向 **已冻结** 版本 |
| `provider` | String(32) NOT NULL | 恒为 `console` |
| `status` | String(32) NOT NULL | pending/running/succeeded/failed/dead |
| `attempt_no` | Integer NOT NULL | 当前发送周期内序号，从 1 起 |
| `idempotency_key` | String(128) NOT NULL | 与 offer 部分唯一 |
| `error_code` | String(64) NULL | 受控短码（如 `console_error` / `provider_unavailable`） |
| `error_message_safe` | String(512) NULL | **截断 + 脱敏**；禁止邮箱明文、禁止协议全文 |
| `started_at` / `finished_at` | timestamptz NULL | |
| `next_retry_at` | timestamptz NULL | 计划下次自动重试（仅 failed 且未耗尽） |
| `created_by` | UUID NULL FK → `users.id` | 人工触发的首次/新周期；系统重试可为 NULL |
| `created_at` | timestamptz NOT NULL | |

**唯一约束：** `uq_offer_send_attempts_idempotency` ON (`offer_id`, `idempotency_key`)。

**禁止列：** 原始 exception 全文、SMTP 对话、收件人明文、正文副本。

---

## 4. 状态机

### 4.1 Offer 状态迁移（锁定）

| 从 | 事件 | 到 | 条件 |
|---|---|---|---|
| （无） | 创建 | `draft` | §1.1 创建门禁；写 version 1（未冻结） |
| `draft` | 保存正文 | `draft` | 仅未冻结 current version；或新建 version_no+1 并切换 current |
| `draft` | 标记就绪 | `ready` | 正文非空、脱敏收件人存在、content_hash 已算 |
| `ready` | 回退编辑 | `draft` | 可选；实现必须支持至少「ready→draft」或「ready 上禁止编辑须先回退」二者之一，规格要求：**冻结前可改** |
| `ready` | 确认发送 | `sending` | 幂等键；冻结 current version；建 attempt#1 pending；入队 mail |
| `sending` | attempt 成功 | `sent` | 同事务写 attempt=succeeded |
| `sending` | attempt 失败且可重试 | `sending` | attempt=failed；调度下次；Offer **保持** sending |
| `sending` | attempt 失败且耗尽 | `failed` | 末次 attempt=`dead` |
| `failed` | 人工新周期发送 | `sending` | 新 idempotency_key；新 attempt_no 从 1 起的新周期；仍用 **同一冻结 version** 或先建新 version 再冻结（见 §5.3） |
| `draft`/`ready`/`failed` | 作废 | `voided` | 不可逆；`sent`/`sending` **禁止**作废（sending 须等终态或运维外流程，一期不做强制取消在途） |
| `sent` | （任意发送） | — | **禁止**再发送；需新业务阶段规格 |

`voided` 与 `sent` 均为终态（一期）。`failed` 为可人工恢复态。

### 4.2 Application / HiringDecision（锁定不变）

| 字段 | 创建 Offer | 确认发送 | Console 成功 | Console 失败 |
|---|---|---|---|---|
| `pipeline_status` | 保持 `pending_offer` | 保持 | 保持 | 保持 |
| `application.status` | 保持 `in_progress` | 保持 | 保持 | 保持 |
| `HiringDecision` | 只读引用 | 不改 | 不改 | 不改 |

**禁止** 因 Offer 写入 `ApplicationStatusLog` 改变流水（一期不因发送插流水日志到 pipeline；若需审计，只用 `record_audit` + attempt 行）。

### 4.3 Attempt 状态

`pending` → `running` → `succeeded` | `failed`；失败且耗尽 → 更新为 `dead`（或保留 failed 并另有 `exhausted` 标志——**锁定采用 status=`dead`** 表示周期耗尽）。

---

## 5. 服务、Provider、队列

### 5.1 创建门禁（伪代码锁定）

```
lock application FOR UPDATE
assert status == in_progress
assert pipeline_status == pending_offer
latest = latest HiringDecision where decision==recommend_hire order by created_at desc, id desc
assert latest is not None
assert no active (non-voided) Offer for application
mask = mask_email(candidate.email); assert mask is not None
insert Offer(draft) + Version(1, encrypted empty-or-template, frozen=false)
audit offer.created
commit
```

缺邮箱（无法脱敏）→ **422**，不建 Offer。

### 5.2 预览与编辑

- `GET` 详情（manage）：解密当前版本正文 **仅** 在详情接口返回；列表接口 **只** 返回 status、version_no、masked email、hash、时间、attempt 摘要。
- 更新正文：仅 `draft`（或 ready 回退到 draft 后）；写新 version 或更新未冻结 version（实现二选一，**锁定推荐**：每次保存创建新 `version_no`，旧未冻结可保留历史；**冻结版本禁止 UPDATE**）。
- 复制正文到剪贴板若做前端能力：必须打审计 `offer.copy_audit`（仅类型码，无正文）。

### 5.3 确认发送（核心事务）

请求体必须含：`idempotency_key`、`lock_version`、`offer_version_id`（须等于 current 且将冻结）。

同事务顺序：

1. `SELECT Offer FOR UPDATE`；校验 `lock_version`、状态 ∈ {`ready`,`failed`}（`failed` 为人工新周期）。
2. 幂等：同 `(offer_id, idempotency_key)` 已有 attempt → 返回该 attempt / Offer 快照；**仅当** attempt=`pending` 时允许补一次 `enqueue`（恢复 commit 成功但首次 dispatch 失败）；`running`/`succeeded`/`failed`/`dead` **严禁**补入队。重复 Celery 消息靠 claim 幂等只执行一次。
3. 校验 version 属于该 Offer；若未冻结则 `frozen=true`；若已冻结则必须 `offer_version_id` 仍为该冻结版（禁止偷换未冻结版发送）。
4. Offer → `sending`；`lock_version += 1`。
5. INSERT attempt（`pending`, `attempt_no=1`, `provider=console`）。
6. `record_audit(action="offer.send_confirmed", …)` — changes **仅** id、version_id、attempt_no、provider、idempotency_key、lock_version、masked 标志布尔；**无**正文/邮箱明文。
7. `commit` 后 `enqueue_mail_send_attempt(attempt_id)` → **仅** mail Celery 任务。首次 enqueue 失败时 attempt **保持** `pending`，依赖同 key 重放补投（见步骤 2）。

### 5.4 自动重试（1 / 5 / 30 分钟）

| 失败次序（周期内 `attempt_no`） | 下次入队 countdown |
|---|---|
| 1 失败后 | **60** 秒（1 分钟）→ 入队 attempt 2 |
| 2 失败后 | **300** 秒（5 分钟）→ 入队 attempt 3 |
| 3 失败后 | **1800** 秒（30 分钟）→ 入队 attempt 4 |
| 4 失败后 | **不入队**；attempt → `dead`；Offer → `failed` |

- 自动重试：**同一冻结 `offer_version_id`**、**新** `attempt` 行、`attempt_no` 递增、**新** 内部幂等键（系统生成，格式固定如 `auto:{offer_id}:{cycle_id}:{attempt_no}`），**禁止**复用用户确认键触发双投。
- 重试入队 **必须** 调用与首次相同的 `enqueue_mail_send_attempt`，路由到 `mail_outbound`。
- **禁止** 使用 `AI_TASK_RETRY_COUNTDOWNS` 或 AI worker 重试函数。

### 5.5 人工新周期

仅当 Offer=`failed`：manage 调用「重新发送」API，提供 **新** 用户 `idempotency_key`，行为同 §5.3（可选择保持原冻结版，或先新增 version 再确认——**锁定**：人工重发默认 **同一冻结版**；若需改正文，必须先 `failed`→（允许回到 draft 的显式 API）→ 新 version → ready → 再确认。一期为减少分叉：**failed 仅允许「同冻结版重发」或「作废」**；改正文须作废后新建 Offer（因 active 唯一约束，作废后方可新建）。

### 5.6 ConsoleMailProvider（唯一）

接口职责（逻辑）：

1. 输入：attempt_id；加载冻结版本密文、Offer 元数据。
2. 解密正文 **仅在内存**；向 stdout / 结构化应用日志写入 **允许** 字段：`attempt_id`、`offer_id`、`version_no`、`recipient_email_masked`、`content_hash`、`provider=console`、`result`。
3. **禁止** 打印 subject/body 明文、明文邮箱、候选人电话。
4. 返回成功或受控失败（`error_code` + `error_message_safe`≤512）。
5. **零** 网络套接字发信；**零** `smtplib` / 第三方 SDK 导入于该模块生产路径。

### 5.7 Celery / Worker 隔离（锁定）

| 项 | 值 |
|---|---|
| 队列名 Settings | `celery_mail_queue_name`，默认 **`mail_outbound`**；空字符串规范化为 `mail_outbound` |
| Celery 任务名 | `app.workers.mail_tasks.process_mail_send_attempt` |
| 模块 | **新** `app/workers/mail_tasks.py`（名称可微调，但 **禁止** 把处理逻辑放进 `ai_tasks.py`） |
| `include` | `celery_app` 同时 include AI 与 mail 模块（共享 broker 连接配置允许）；**路由强制** mail 任务只进 mail 队列 |
| `task_routes` | 增加上述任务名 → `settings.celery_mail_queue_name` |
| Worker 进程 | UAT/生产邮件投递：**单独**进程 `-Q mail_outbound`（或 Settings 名）；**禁止**与 `-Q celery` / `-Q ai_sensitive` 混跑于同一 worker 命令作为本功能交付方式 |
| 载荷 | `apply_async` args **仅** `attempt_id` UUID 字符串；**禁止**把正文/邮箱放进 Celery 消息体 |

Mail worker 处理步骤：claim attempt `pending`→`running` → 调 Console provider → **在成功 / 失败 / 自动重试写入前**重锁并要求终态所有权（见 §5.8）→ 成功则 Offer=`sent`；失败则写 safe error → 按 §5.4 调度或标 `dead`/`failed` → audit `offer.send_attempt_finished`（无敏感正文）。

### 5.8 终态所有权与 stale reclaim（锁定）

**终态所有权（Critical）：** 在 mail worker 写入 attempt 终态、Offer 终态、或创建下一自动重试 attempt / enqueue 之前，必须 `SELECT … FOR UPDATE` 重锁并同时满足：

1. 目标 `OfferSendAttempt.status == running`
2. 所属 `Offer.status == sending`

否则返回 `skipped_stale_owner`：**不**改 attempt/Offer、**不**建下一 attempt、**不**入队。迟到失败不得把已 `sent` Offer 打回；迟到成功不得复活已 `failed` Offer。本批 **不**新增 `current_send_attempt_id` 列 / 迁移。

**独立 mail stale reclaim（Important）：** claim 后崩溃可能导致 attempt 永 `running`。提供 **不入队、manage-only** 正式恢复路径，**禁止**复用 AI admin `mark-stale-failed`：

| 项 | 值 |
|---|---|
| 方法/路径 | `POST /api/v1/offers/{offer_id}/attempts/{attempt_id}/mark-stale-failed` |
| Body | **仅** `expected_updated_at`（`extra=forbid`）；**无**自由文本 reason |
| 语义 | `expected_updated_at` **精确匹配** attempt.`started_at`（claim 时间） |
| 前置 | attempt=`running`、Offer=`sending`、运行年龄 ≥ **5 分钟** |
| 同事务 | attempt → `dead`（`error_code=stale_send_attempt_recovered`）；Offer → `failed`；条件更新 / 行锁与迟到 worker 竞争时仅一方可写终态 |
| 审计 | `offer.stale_send_attempt_recovered`；changes 仅 ID、状态、`error_code`、年龄等元数据 |
| 禁止 | 零 enqueue、零 SMTP/Console；响应精简 DTO，**禁止**正文/明文邮箱 |

## 6. API 与权限

### 6.1 权限

| 操作 | `recruitment.manage` | `interview.execute` | 匿名 |
|---|---|---|---|
| 任何 Offer API | 允许 | **403** | 401 |
| 前端 Offer 面板 | 可见 | **不可挂载、不可见** | — |

**不**新增 permission 码。`system_admin` 因矩阵含 `recruitment.manage` 而可操作。

### 6.2 端点（锁定最小集）

前缀建议：`/api/v1/applications/{application_id}/offers` 与 `/api/v1/offers/{offer_id}/...`。

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/applications/{application_id}/offers` | 创建 draft（门禁 §5.1）；可选 `idempotency_key` |
| `GET` | `/applications/{application_id}/offers` | 列表摘要（无正文） |
| `GET` | `/offers/{offer_id}` | 详情；含解密正文（manage preview） |
| `PATCH` | `/offers/{offer_id}` | 更新 draft 正文 / 回退 ready→draft；需 `lock_version` |
| `POST` | `/offers/{offer_id}/ready` | draft→ready |
| `POST` | `/offers/{offer_id}/send` | 确认发送（§5.3） |
| `POST` | `/offers/{offer_id}/retry` | failed 人工新周期（§5.5） |
| `POST` | `/offers/{offer_id}/void` | 作废（§4.1 允许的状态） |
| `GET` | `/offers/{offer_id}/attempts` | attempt 列表（脱敏） |
| `POST` | `/offers/{offer_id}/attempts/{attempt_id}/mark-stale-failed` | 卡住的 running attempt 回收（§5.8）；manage-only；零入队 |

所有写接口：`Cache-Control: no-store` 不强制；**读详情**必须 `Cache-Control: no-store`。

### 6.3 响应脱敏（锁定）

**允许：** `recipient_email_masked`、`recipient_name`、`status`、`version_no`、`content_hash`、`frozen`、`provider`、`error_code`、`error_message_safe`、时间戳、id。

**禁止出现在任何 JSON：** `recipient_email`、`email`（指收件明文）、`subject`/`body_html`/`body_text` 出现在 **list/attempts**；详情允许 `subject`/`body_*` **仅** GET offer 详情一处。审计与列表严禁正文。

### 6.4 错误映射

| 条件 | HTTP |
|---|---|
| 无权限 | 403 |
| 应聘/Offer 不存在 | 404 |
| 门禁失败（非 pending_offer、无 recommend_hire、缺邮箱、状态不允许） | 409 或 422（实现选定后测试固定；**推荐**业务态 409、校验 422） |
| `lock_version` / 幂等冲突 | 409 |
| 未认证 | 401 |

---

## 7. 前端

### 7.1 入口

- **唯一**一期入口：`CandidateCenterDetailView`，当 `canManage && pipeline_status===pending_offer'` 显示 Offer 面板（`data-test="offer-console-panel"`）。
- 替换原「不提供发送能力」为：状态、脱敏收件人、版本号、预览、就绪、**二次确认发送**、attempt 表、失败后重试/作废。
- `InterviewTimelineView` / execute 视图：**禁止** Offer 按钮与文案「发送 Offer」若会导致 execute 可操作；时间轴可继续显示流水标签「录用建议待后续」，**不**加发送入口。

### 7.2 交互锁定

1. 预览：打开只读对话框展示正文；关闭不落盘。
2. 发送：主按钮 → 确认框（明示「Console 模拟投递，非真实邮件」）→ 调用 send；按钮 disabled 至返回。
3. 文案 **禁止**：SMTP、Dify、自动决策、真实外发、hired。
4. 文案 **允许**：Offer 草稿、就绪、发送（Console）、发送尝试、失败可重试。

### 7.3 路由 / 权限

沿用候选人中心 `recruitment.manage`；**不**新开独立路由也可；若新开则 meta 权限必须 `recruitment.manage` only。

---

## 8. 审计与日志

| action | 何时 | changes 允许键 |
|---|---|---|
| `offer.created` | 创建 | application_id, offer_id, hiring_decision_id, lock_version |
| `offer.updated` | 保存版本 | offer_id, version_no, content_hash, lock_version |
| `offer.marked_ready` | ready | offer_id, version_id, lock_version |
| `offer.send_confirmed` | 确认发送 | offer_id, version_id, attempt_id, provider, idempotency_key, lock_version |
| `offer.send_attempt_finished` | worker 终态 | offer_id, attempt_id, attempt_status, error_code, offer_status |
| `offer.retry_requested` | 人工重试 | offer_id, attempt_id, idempotency_key, lock_version |
| `offer.stale_send_attempt_recovered` | manage stale reclaim | offer_id, attempt_id, statuses, error_code, age 元数据 |
| `offer.voided` | 作废 | offer_id, void_reason_code, lock_version |
| `offer.copy_audit` | 可选复制 | offer_id, copy_type ∈ {SUBJECT, HTML_BODY, FULL_TEXT} |

**全表禁止** 写入明文邮箱、正文、协议 dump。

Worker/Console 日志同 §5.6。

---

## 9. 测试与 UAT

### 9.1 自动化（实现时必补）

| 用例 | 断言要点 |
|---|---|
| 迁移 016 | 三表存在；无 smtp 字符串；down_revision=015；无改 ai_tasks check |
| 创建门禁 | 仅 pending_offer+in_progress+最新 recommend_hire；缺邮箱/非门禁 → 拒绝 |
| 唯一 active Offer | 第二条非 voided → 冲突 |
| 状态机 | draft→ready→sending→sent；失败路径→failed；void 规则 |
| 版本冻结 | 发送后 UPDATE 正文拒绝；content_hash 稳定 |
| 幂等 | 同 send idempotency_key 不双 attempt；pending 可补 enqueue；非 pending 严禁补入队；双消息单次 claim |
| 终态所有权 | 迟到失败不打回 sent；迟到成功不复活 failed；所有权丢失 → skipped_stale_owner、零新 attempt |
| Stale reclaim | mark-stale-failed：≥5min + started_at 匹配；attempt dead + Offer failed；零 enqueue；DTO 无正文 |
| 重试时钟 | 失败后 countdown 依次 60/300/1800 入队 attempt 2/3/4；第 4 次失败后 dead+Offer failed、无第 5 次自动入队 |
| Provider | Console 成功/失败；模块禁止 smtp 导入（静态或单测） |
| 队列 | `task_routes` 邮件任务 = `mail_outbound`（或 Settings）；**不等于** `ai_sensitive`；入队名 ≠ `process_ai_task` |
| 权限 | manage 200；execute 403；匿名 401 |
| 脱敏 | list/attempts/audit 无正文无明文邮箱；详情才有正文 |
| 应用态 | 全程 pipeline=`pending_offer`，status≠`hired` |
| 前端 | manage+pending_offer 可见面板；execute 不可见；无 SMTP/Dify/自动发送；确认框含 Console 说明 |
| 回归 | HiringDecision / 综合分析 pending_offer 只读 / 初筛拒绝 pending_offer **保持绿** |

### 9.2 Fixture 最小集

- 应聘：`in_progress` + `pending_offer` + 候选人含可脱敏邮箱 + 一条 `recommend_hire` HiringDecision
- 对照：`interviewing`、无 recommend_hire、无邮箱、`voided` 后重建、execute 用户
- Celery：mock `apply_async` 断言队列名与任务名

### 9.3 Windows UAT runbook（规格定义，本文件不执行）

**环境假设：** Windows 10+；PowerShell；本机 Postgres/Redis 已按项目惯例可用；**不**加载真实 SMTP；**不**开 Dify live。

**T0 — 启动前检查（全部满足才继续）：**

1. 确认未对默认队列 `celery` 做 `purge` / 长度窥探以外的消费（本功能 **禁止** 启动 `-Q celery` worker）。
2. 确认不启动 `-Q ai_sensitive` 仅为了本 UAT。
3. 确认不通过 SQL/API/admin 触碰受保护 running：
   - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
   - `3556206d-138b-40f6-9b23-97fce178a32e`
4. 使用隔离虚构候选人数据（姓名/邮箱可用虚构域），禁止生产候选人。
5. `CELERY_MAIL_QUEUE_NAME` 为空或 `mail_outbound`；**无** SMTP 环境变量需求。

**T1 — 进程：**

1. 启动 API（若 UAT 需要 HTTP）。
2. 单独启动 mail worker，示例（实现后以 README 为准，语义锁定）：
   `celery -A app.workers.celery_app.celery_app worker -Q mail_outbound --pool=solo --concurrency=1 --prefetch-multiplier=1`
   （工作目录与虚拟环境按仓库 Windows 惯例；**必须** `--pool=solo`。）
3. **禁止** 同一命令订阅 `celery` 或 `ai_sensitive`；**禁止** `-Q celery,ai_sensitive,mail_outbound`。

**T2 — 业务步骤：**

1. manage 登录 → 候选人中心详情（目标应聘已 `pending_offer`）。
2. 创建 Offer → 编辑正文 → 就绪 → 预览 → 确认发送（读确认框 Console 说明）。
3. 观察 mail worker 控制台：**可见** masked 与 hash；**不可见**正文与明文邮箱。
4. UI/API：Offer=`sent`；attempt=`succeeded`；`pipeline_status` 仍为 `pending_offer`；`status` 非 `hired`。
5. （可选失败演练）注入 Console 失败：验证 1/5/30 分钟调度（可用测试时钟或缩短仅在自动化中验证；UAT 可用 mock 失败一次后人工 retry，不必真等 30 分钟）。
6. execute 账号访问 Offer API → 403；UI 无面板。

**T3 — 停止：**

1. 停止 **仅** mail worker；不留下消费默认队列的进程。
2. 不清理 Redis 默认队列；不 mark-stale 任何 AI task。

**失败即停：** 任何步骤需要消费默认/`ai_sensitive`、真实 SMTP、Dify、或触碰受保护 running → **立即停止 UAT**，记为违规。

---

## 10. 范围外（明确不做）

| 项 | 说明 |
|---|---|
| SMTP / 真实外发 | 后续阶段；须另开规格与人工启用开关 |
| 附件 / 电子签 | 后续 |
| Offer 接受 / `hired` | 后续；本规格发送成功仍 `pending_offer`+`in_progress` |
| `offer_sent` 流水态 | 不新增 |
| `offer.*` RBAC | 不新增 |
| AI 生成正文 / 自动发送 | 硬禁 |
| 复用 `ai_tasks` / 敏感或默认队列 | 硬禁 |
| 邀约表双写 | 硬禁 |
| 取消在途 `sending` | 一期不做 |
| 邮件全局配置页 | 不做 |

---

## 11. 稳定符号表

| 符号 | 值 |
|---|---|
| 迁移 | `016_offer_console_delivery` ← `015_comprehensive_interview_analysis` |
| 表 | `offers` · `offer_versions` · `offer_send_attempts` |
| Offer 状态 | `draft` · `ready` · `sending` · `sent` · `failed` · `voided` |
| Provider | `console`（唯一） |
| 队列 | `mail_outbound`（Settings `celery_mail_queue_name` / `CELERY_MAIL_QUEUE_NAME`） |
| Celery 任务 | `app.workers.mail_tasks.process_mail_send_attempt` |
| 重试 | 60s · 300s · 1800s；最大自动 **4** 次 attempt（第 4 次失败后 dead） |
| 写/读权限 | **仅** `recruitment.manage` |
| 创建门禁 | `pending_offer` ∧ `in_progress` ∧ 最新 `recommend_hire` |
| 发送后流水 | **仍** `pending_offer`；**不**写 `hired` |
| 审计前缀 | `offer.*` |
| 受保护 running | `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`；`3556206d-138b-40f6-9b23-97fce178a32e` |
| 默认队列禁令 | 不消费、不 purge、不依赖 `-Q celery` |
| 敏感队列禁令 | 邮件 **永不** 入 `ai_sensitive` |

---

## 12. 自检清单（规格完成度）

- [x] 方案 A：独立 `mail_outbound` + 独立邮件任务 + 一期仅 Console
- [x] 创建门禁：`pending_offer` + `in_progress` + 最新 `recommend_hire`
- [x] 三表、正文加密、收件人仅脱敏、无附件
- [x] 状态机 `draft → ready → sending → sent | failed | voided`
- [x] 发送后不写 `hired`、流水保持 `pending_offer`
- [x] 仅 `recruitment.manage`；execute 无 UI/API
- [x] 严禁复用 `ai_sensitive`、默认 `celery`、`ai_tasks`
- [x] 严禁 SMTP、Dify、真实外发
- [x] 幂等键、版本冻结、1/5/30 分钟重试、超限仅人工新 attempt
- [x] 审计/API/日志脱敏要求完整
- [x] 前端、迁移 016、测试、Windows UAT runbook、双 running 与默认队列禁令
- [x] 无 TODO / TBD / 占位符
- [x] 本文件不编码、不执行迁移、不启动 worker、不触达真实外发
