# Offer Console 投递（阶段 10 / 方案 A）— TDD 实施计划

> **For agentic workers:** 按 Task 1→6 顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。
> **Task 6 UAT runbook：只记录、禁止执行**（零 SMTP、零真实邮件、零默认/`ai_sensitive` 消费、零触碰受保护 ID）。

**规格：** `docs/superpowers/specs/2026-08-20-offer-console-delivery-design.md`
**基线：** HiringDecision / `pending_offer` / 综合分析迁移 **015** 已合入；**无** Offer / mail 队列 / Console provider。
**方法：** TDD。符号名锁定为规格 §11；禁止临时改名。

## 全局约束

- **绝不**复用 `ai_tasks` / `ai_task_attempts` / `process_ai_task` / `process_sensitive_ai_task` / `ai_sensitive` / 默认队列 `celery` 承载邮件投递。
- **不**接入 SMTP；**不**读写邮件配置页或 SMTP Settings；**不**发真实邮件；一期 **仅** `ConsoleMailProvider`。
- **不**推进 `hired`；**不**改 `HiringDecision` 服务/API/表语义；**不**改综合分析门禁或 regenerate。
- **不**含附件、电子签、候选人接受/拒绝、`offer_sent` 流水态。
- **不**新增 `offer.*` permission；读写 **仅** `recruitment.manage`；`interview.execute` 零 API、零 UI。
- **不**调用 Dify；**不**用 AI 生成正文；**不**从分析/决策自动触发发送。
- **不**复用邀约表/`record_sent` 作为 Offer 已发送语义（可复用脱敏/加密**范式**）。
- **不**触碰、retry、cancel、mark-stale、SQL/Redis 干预：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- **不**消费、清理、窥探默认 `celery` 队列既有消息；**不**对 `ai_sensitive` 做 purge。
- 自动化：Task 1–5 **不**启动常驻 worker；Celery 入队用 mock `apply_async`。
- Windows 未来 UAT mail worker **必须**：`--pool=solo --concurrency=1 --prefetch-multiplier=1 -Q mail_outbound`（见 Task 6）。
- 本计划各任务 **默认不提交**；「提交边界」仅当用户明确要求时适用。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| 迁移 | `016_offer_console_delivery`；`down_revision = 015_comprehensive_interview_analysis` |
| 表 | `offers` · `offer_versions` · `offer_send_attempts` |
| ORM | `Offer` · `OfferVersion` · `OfferSendAttempt` |
| Offer 状态 | `draft` · `ready` · `sending` · `sent` · `failed` · `voided` |
| Attempt 状态 | `pending` · `running` · `succeeded` · `failed` · `dead` |
| Provider | `MAIL_PROVIDER_CONSOLE = "console"`（唯一） |
| 重试 | `MAIL_RETRY_COUNTDOWNS_SECONDS = {1: 60, 2: 300, 3: 1800}`；`MAIL_MAX_AUTO_ATTEMPTS = 4` |
| Settings | `celery_mail_queue_name` / env `CELERY_MAIL_QUEUE_NAME`；默认 `mail_outbound` |
| Celery 任务名 | `app.workers.mail_tasks.process_mail_send_attempt` |
| Worker 模块 | `app.workers.mail_tasks` |
| 入队 | `enqueue_mail_send_attempt(attempt_id)` |
| Provider 类 | `ConsoleMailProvider`（模块建议 `app.services.mail_providers.console`） |
| 服务模块 | `app.services.offers` |
| 仓库模块 | `app.repositories.offers` |
| 模板 | `template_code=offer_console_v1`；`template_version="1"` |
| 权限 | **仅** `recruitment.manage` |
| 审计 | `offer.created` · `offer.updated` · `offer.marked_ready` · `offer.send_confirmed` · `offer.send_attempt_finished` · `offer.retry_requested` · `offer.voided` · `offer.copy_audit` |
| 创建门禁 | `pending_offer` ∧ `in_progress` ∧ 最新 `recommend_hire` |
| 发送后流水 | **仍** `pending_offer`；**不**写 `hired` |
| 受保护 running | 上表两 UUID |
| 前端面板 | `data-test="offer-console-panel"` |

## 规格覆盖映射

| 规格章节 | 本计划 Task |
|---|---|
| §3 模型与迁移 · §3.1–3.5 · §11 符号 | Task 1 |
| §1.1 创建门禁 · §4 状态机（草稿段）· §5.1–5.2 · §8 审计（created/updated/ready） | Task 2 |
| §5.3–5.7 · §4.1 sending/sent/failed · §2 Celery 差分 | Task 3 |
| §6 API/权限/脱敏 · §8 send/retry/void 审计 | Task 4 |
| §7 前端 | Task 5 |
| §9 测试/UAT · §1.3 / §10 非目标 · 回归 | Task 6 |

---

## Task 1 — Offer 模型、常量与 016 迁移

**Consumes：** 规格 §3、§11。
**Produces：** ORM + 常量 + Alembic 016 upgrade/downgrade；**无** service / API / worker / Settings 行为（Settings 可在 Task 3 加；本 Task **禁止** 改 `ai_tasks` check）。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/models/offer.py` | **新建** 三表 ORM + 状态/provider/retry/模板常量 |
| `backend/app/models/__init__.py` | 导出模型与常量 |
| `backend/alembic/versions/016_offer_console_delivery.py` | **新建** upgrade/downgrade |
| `backend/tests/models/test_offer_constants.py` | **新建** 常量/禁列断言 |
| `backend/tests/db/test_migration_016_offer_console_delivery.py` | **新建** 迁移结构断言（对齐 014/015 db 测风格） |
| `backend/tests/integrations/test_migration_016_pg.py` | **新建**（若项目沿用 PG 集成）：三表/部分唯一/FK；**无**改 `ck_ai_tasks_task_type` |

### 精确结构

```python
# models/offer.py
OFFER_STATUS_DRAFT = "draft"
OFFER_STATUS_READY = "ready"
OFFER_STATUS_SENDING = "sending"
OFFER_STATUS_SENT = "sent"
OFFER_STATUS_FAILED = "failed"
OFFER_STATUS_VOIDED = "voided"
OFFER_STATUSES = frozenset({...})

MAIL_PROVIDER_CONSOLE = "console"
OFFER_ATTEMPT_STATUS_PENDING = "pending"
OFFER_ATTEMPT_STATUS_RUNNING = "running"
OFFER_ATTEMPT_STATUS_SUCCEEDED = "succeeded"
OFFER_ATTEMPT_STATUS_FAILED = "failed"
OFFER_ATTEMPT_STATUS_DEAD = "dead"

MAIL_RETRY_COUNTDOWNS_SECONDS = {1: 60, 2: 300, 3: 1800}
MAIL_MAX_AUTO_ATTEMPTS = 4
OFFER_TEMPLATE_CODE = "offer_console_v1"
OFFER_TEMPLATE_VERSION = "1"

class Offer(Base):
    __tablename__ = "offers"
    # 列按规格 §3.3；禁止 recipient_email 明文列、附件列
    # uq_offers_application_active: partial unique on application_id WHERE status NOT IN ('voided')

class OfferVersion(Base):
    __tablename__ = "offer_versions"
    # subject/body_*_encrypted; content_hash; frozen; uq (offer_id, version_no)

class OfferSendAttempt(Base):
    __tablename__ = "offer_send_attempts"
    # provider; status; attempt_no; idempotency_key; error_code; error_message_safe(≤512)
    # uq_offer_send_attempts_idempotency (offer_id, idempotency_key)
```

迁移 `upgrade()` 必须：

1. 建三表 + 索引/Check/FK（含 `hiring_decision_id` → `hiring_decisions` RESTRICT）。
2. 部分唯一 `uq_offers_application_active`。
3. **不** ALTER `ai_tasks`；**不**建 SMTP 配置表；源文件 **无** `smtp` 字符串（大小写不敏感断言）。

迁移 `downgrade()` 必须：drop attempts → versions → offers（次序尊重 FK）；**不**删其它业务表。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_offer_statuses_locked_six` | `OFFER_STATUSES` 恰六态且含 draft/ready/sending/sent/failed/voided |
| `test_mail_retry_countdowns_1_5_30_minutes` | `{1:60,2:300,3:1800}`；`MAIL_MAX_AUTO_ATTEMPTS==4` |
| `test_only_console_provider_constant` | `MAIL_PROVIDER_CONSOLE=="console"`；模块源无 `smtp` |
| `test_offer_models_forbid_plaintext_email_and_attachment_columns` | 三表列名无 `recipient_email`（非 masked）、无 `attachment`、无 `smtp` |
| `test_migration_016_revision_chain` | revision=`016_offer_console_delivery`；down_revision=`015_comprehensive_interview_analysis` |
| `test_migration_016_upgrade_creates_three_tables_and_downgrade_drops` | upgrade 后三表+部分唯一存在；downgrade 后三表不存在 |

### GREEN

实现 ORM + 016 使上表全绿。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/models/test_offer_constants.py tests/db/test_migration_016_offer_console_delivery.py -q --tb=short
```

（有 PG 集成时追加 `tests/integrations/test_migration_016_pg.py`。）

### 禁止范围（Task 1）

- 禁止 service/API/worker/前端；禁止改 `HiringDecision`、综合分析、`ai_tasks`。
- 禁止 SMTP Settings；禁止触碰受保护 running / 默认队列。

### 提交边界（仅当用户明确要求）

仅 Task 1 文件 + 测试；**不**含 `.env`、业务发送逻辑。

---

## Task 2 — Offer 草稿/版本服务：门禁、加密、脱敏、冻结、并发与幂等

**Consumes：** Task 1；规格 §1.1 创建门禁、§4 草稿态、§5.1–5.2、§8（created/updated/ready/voided）。
**Produces：** repository + service（创建/更新/ready/void/list/get 解密）；**不**入队 Celery；**不**建 attempt（attempt 属 Task 3/4）。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/repositories/offers.py` | **新建** add/get/list/lock/find_active/find_by_idempotency（创建幂等若需要） |
| `backend/app/services/offers.py` | **新建** `create_offer` · `update_offer_draft` · `mark_offer_ready` · `void_offer` · `get_offer_detail` · `list_offers_for_application` |
| `backend/app/services/mail_masking.py` 或共享工具 | **新建或抽取** `mask_email`（规则对齐邀约；**禁止**把明文写入 Offer 表） |
| `backend/tests/services/test_offers_draft.py` | **新建** 门禁/加密/脱敏/冻结前编辑/唯一 active/锁/幂等 |
| `backend/tests/services/test_offers_no_hired_side_effects.py` | **新建** 断言全程不写 `hired`、不改 pipeline |

### 精确接口

```python
# services/offers.py
async def create_offer(session, *, application_id, actor, request_context, idempotency_key: str | None = None) -> OfferResult: ...
async def update_offer_draft(session, *, offer_id, subject, body_html, body_text, lock_version, actor, request_context) -> OfferResult: ...
async def mark_offer_ready(session, *, offer_id, lock_version, actor, request_context) -> OfferResult: ...
async def void_offer(session, *, offer_id, void_reason_code, lock_version, actor, request_context) -> OfferResult: ...
async def get_offer_detail(session, *, offer_id) -> OfferDetail:  # 含解密正文
async def list_offers_for_application(session, *, application_id) -> list[OfferSummary]:  # 无正文
```

创建门禁（同事务 `FOR UPDATE` application）：

1. `status == in_progress` 且 `pipeline_status == pending_offer`
2. 最新 `HiringDecision` where `decision == recommend_hire`（`created_at desc, id desc`）非空 → 写入 `hiring_decision_id`
3. 无 active（非 voided）Offer
4. `mask_email(candidate.email)` 非空，否则校验失败
5. 插 `Offer(draft)` + `OfferVersion(1, encrypted, frozen=false)`；audit `offer.created`
6. **不**改 `pipeline_status` / `application.status`

版本规则：

- 每次保存 **新建** `version_no`（推荐）并更新 `current_version_id`；未冻结可替换 current。
- `frozen=true` 的 version **禁止** UPDATE 正文列。
- `ready` → 允许回退 `draft`（显式 API 或 `update` 前置转换，二选一写测试钉死）。
- `sent`/`sending` **禁止** void（本 Task 测 draft/ready/failed 可 void；sending/sent 拒绝）。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_create_requires_pending_offer_in_progress_recommend_hire` | interviewing / 无 recommend_hire / rejected → 状态错误；快乐路径 status=draft |
| `test_create_requires_maskable_email` | 无邮箱 → 校验失败；有邮箱只存 masked |
| `test_create_binds_latest_recommend_hire` | 多条 recommend_hire 绑最新 |
| `test_second_active_offer_conflicts` | 未 voided 再建 → 冲突 |
| `test_update_encrypts_body_and_sets_content_hash` | DB 列为密文 ≠ 明文；hash 稳定 |
| `test_list_summary_has_no_body_or_plaintext_email` | summary 无 subject/body/email 明文键 |
| `test_detail_decrypts_for_preview` | detail 含明文 subject/body（服务层；API 权限在 Task 4） |
| `test_frozen_version_rejects_update` | 手工 frozen=true 后 update 拒绝 |
| `test_mark_ready_and_void_rules` | draft→ready；ready/draft 可 void；审计键无正文 |
| `test_lock_version_conflict` | 错 lock → 冲突；成功 +1 |
| `test_create_does_not_write_hired_or_change_pipeline` | pipeline 仍 pending_offer；status ≠ hired |
| `test_create_idempotency_same_key`（若 create 支持 key） | 同 key 返回同一 offer_id |

### GREEN

实现 repository/service 使上表全绿；正文一律 `encrypt_secret`。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/services/test_offers_draft.py tests/services/test_offers_no_hired_side_effects.py -q --tb=short
```

### 禁止范围（Task 2）

- 禁止 `apply_async`、mail worker、SMTP、attempt 成功路径（可预留函数签名但本 Task 测试不入队）。
- 禁止改 HiringDecision / 综合分析 / AI worker。
- 禁止附件字段。

### 提交边界

仅草稿服务相关文件 + 测试；**不**含 API 路由、前端、`.env`。

---

## Task 3 — 独立 mail_outbound、mail_tasks、Console provider、attempt 与 1/5/30 重试

**Consumes：** Task 1–2；规格 §5.3–5.7、§4.1 sending 段。
**Produces：** Settings 队列名、Console provider、`mail_tasks`、入队路由、确认发送事务钩子（service 层 `confirm_offer_send` / worker 处理）；**无** HTTP；**无** SMTP。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/core/config.py` | 新增 `celery_mail_queue_name`（默认 `mail_outbound`）+ 空值规范化；**无** SMTP 字段 |
| `backend/app/workers/celery_app.py` | `include` 增加 `app.workers.mail_tasks`；`task_routes` 增加 mail 任务 → mail 队列；**保持**敏感路由不变 |
| `backend/app/workers/mail_tasks.py` | **新建** `process_mail_send_attempt`；claim→provider→成功/失败/重试 |
| `backend/app/services/mail_providers/base.py` | **新建** 协议/结果类型 |
| `backend/app/services/mail_providers/console.py` | **新建** `ConsoleMailProvider` |
| `backend/app/services/offers.py` | 追加 `confirm_offer_send` · `retry_offer_send` · `enqueue_mail_send_attempt` |
| `backend/.env.example` | 可选注释 `CELERY_MAIL_QUEUE_NAME`；**禁止** SMTP 示例密钥正文 |
| `backend/tests/workers/test_mail_outbound_queue.py` | **新建** 路由/队列名/禁复用 AI |
| `backend/tests/services/test_console_mail_provider.py` | **新建** Console 成功/失败；源无 smtp 导入 |
| `backend/tests/workers/test_mail_send_attempt_retry.py` | **新建** 60/300/1800 与 dead |
| `backend/tests/services/test_confirm_offer_send.py` | **新建** 冻结、幂等、入队、不写 hired |

### 精确接口

```python
# config
celery_mail_queue_name: str  # default mail_outbound; env CELERY_MAIL_QUEUE_NAME

# services/offers.py
async def confirm_offer_send(session, *, offer_id, offer_version_id, lock_version, idempotency_key, actor, request_context) -> OfferSendResult: ...
async def retry_offer_send(session, *, offer_id, lock_version, idempotency_key, actor, request_context) -> OfferSendResult: ...  # 仅 failed
def enqueue_mail_send_attempt(attempt_id: UUID, *, countdown: int = 0) -> None:
    process_mail_send_attempt.apply_async(args=[str(attempt_id)], countdown=countdown)

# workers/mail_tasks.py
@celery_app.task(name="app.workers.mail_tasks.process_mail_send_attempt", bind=True)
def process_mail_send_attempt(self, attempt_id: str) -> dict: ...
```

确认发送同事务（规格 §5.3）：ready|failed → sending；冻结 version；attempt#1 pending；audit；commit 后 enqueue。
Worker：pending→running→Console；成功 Offer=`sent`；失败写 `error_code`/`error_message_safe`；`attempt_no` 1/2/3 失败后分别 countdown 60/300/1800 入队下一 attempt；第 **4** 次失败 → attempt=`dead`、Offer=`failed`、**零**第五次自动入队。
人工 `retry_offer_send`：仅 `failed`；新用户幂等键；新周期 attempt_no 从 1；同冻结版。

Console 日志允许：`attempt_id`/`offer_id`/`version_no`/`recipient_email_masked`/`content_hash`/`provider`/`result`；**禁止**正文与明文邮箱。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_celery_mail_queue_name_default_mail_outbound` | Settings 默认 `mail_outbound` |
| `test_task_routes_mail_to_mail_outbound_not_ai_sensitive` | routes[MAIL_TASK]==mail 队列；≠ `ai_sensitive`；敏感路由仍在 |
| `test_celery_include_has_mail_tasks_module` | include 含 `app.workers.mail_tasks` |
| `test_enqueue_targets_process_mail_send_attempt_only` | mock apply_async：name/任务为 mail；args 仅 attempt_id |
| `test_mail_tasks_module_has_no_ai_task_imports_for_send` | `mail_tasks.py` 不 import `process_ai_task` / 不写 `ai_tasks` 表 |
| `test_console_provider_success_and_safe_failure` | 成功；失败 safe message ≤512 且无 `@` 明文域本地完整邮箱模式（用虚构输入断言不回显明文） |
| `test_console_module_source_forbids_smtp` | 源码无 `smtp`/`smtplib` |
| `test_confirm_send_freezes_version_and_enqueues_once` | frozen；1 attempt；1 enqueue |
| `test_confirm_send_idempotent` | 同 key 不双 attempt；pending 命中可补 enqueue |
| `test_confirm_idempotent_pending_requeues_once` | commit 后 dispatch 失败场景：同 key 重放补投 pending |
| `test_confirm_idempotent_non_pending_never_reenqueues` | running/succeeded/failed/dead 严禁补入队 |
| `test_late_failure_does_not_revert_sent_offer` | 终态所有权：迟到失败不打回 sent |
| `test_late_success_does_not_resurrect_failed_offer` | 迟到成功不复活 failed |
| `test_mark_stale_offer_send_attempt_*` | stale reclaim：年龄/匹配/dead+failed/零 enqueue/manage-only |
| `test_retry_countdowns_60_300_1800_then_dead` | 三次失败 countdown 60/300/1800 入队 attempt 2/3/4；第 4 次失败 dead、无第五次 enqueue；Offer=failed |
| `test_send_keeps_pending_offer_never_hired` | pipeline/status 断言 |
| `test_config_has_no_smtp_settings_fields` | Settings 模型字段名无 smtp/mail_host 等 |

### GREEN

实现 provider + worker + confirm/retry + 路由，使上表全绿。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/workers/test_mail_outbound_queue.py tests/services/test_console_mail_provider.py tests/workers/test_mail_send_attempt_retry.py tests/services/test_confirm_offer_send.py -q --tb=short
```

### 禁止范围（Task 3）

- 禁止 SMTP 实现类；禁止真实网络发信；禁止启动常驻 worker。
- 禁止把处理逻辑放进 `ai_tasks.py`；禁止入队默认/`ai_sensitive`。
- 禁止改 HiringDecision / 综合分析。

### 提交边界

mail 队列/provider/worker/confirm 服务 + 测试 + 可选 `.env.example` 注释行；**不**提交真实 `.env`。

---

## Task 4 — Offer API：草稿、预览、确认发送、版本/attempt 历史（manage-only、零明文）

**Consumes：** Task 2–3；规格 §6、§8。
**Produces：** FastAPI 路由 + schemas；**无**前端。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/schemas/offer.py` | **新建** Request/Out；list 无正文；detail 含正文；attempts 脱敏 |
| `backend/app/api/v1/endpoints/offers.py` | **新建** 端点；一律 `require_permission("recruitment.manage")` |
| `backend/app/api/v1/router.py` | `include_router(offers.router)` |
| `backend/tests/api/v1/test_offers.py` | **新建** 权限/门禁/脱敏/幂等/错误码 |

### 精确端点

| 方法 | 路径 | 依赖服务 |
|---|---|---|
| `POST` | `/applications/{application_id}/offers` | `create_offer` |
| `GET` | `/applications/{application_id}/offers` | `list_offers_for_application` |
| `GET` | `/offers/{offer_id}` | `get_offer_detail` + `Cache-Control: no-store` |
| `PATCH` | `/offers/{offer_id}` | `update_offer_draft` |
| `POST` | `/offers/{offer_id}/ready` | `mark_offer_ready` |
| `POST` | `/offers/{offer_id}/send` | `confirm_offer_send` |
| `POST` | `/offers/{offer_id}/retry` | `retry_offer_send` |
| `POST` | `/offers/{offer_id}/void` | `void_offer` |
| `GET` | `/offers/{offer_id}/attempts` | list attempts |

错误映射：401 / 403 / 404 / 409 / 422（与规格 §6.4 一致；测试钉死选定组合）。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_manage_can_crud_and_send` | manage 快乐路径 201/200 |
| `test_execute_all_offer_routes_403` | execute-only 全 403 |
| `test_anonymous_401` | 无 token → 401 |
| `test_list_and_attempts_have_no_body_or_plaintext_email` | JSON 无 body/subject（list）；无完整明文 email |
| `test_detail_returns_body_only_on_get_offer` | 仅 GET detail 含 subject/body |
| `test_send_requires_idempotency_and_lock` | 缺字段 422；错 lock 409 |
| `test_send_idempotent_http` | 重复 send 同 attempt id |
| `test_response_never_contains_hired_transition` | 响应无暗示 status=hired；应用仍 pending_offer（若返回 pipeline） |
| `test_api_module_source_forbids_smtp_dify` | endpoints 源无 smtp/dify 发信 |

### GREEN

接线 API 使上表全绿；enqueue 在测试中 mock。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/api/v1/test_offers.py -q --tb=short
```

### 禁止范围（Task 4）

- 禁止对 execute 开放读；禁止响应明文邮箱；禁止 SMTP 配置端点。
- 禁止改 bootstrap 增加 `offer.*`（一期不增加）。

### 提交边界

API/schema/router + 测试；**无**前端。

---

## Task 5 — 候选人中心 Offer 面板（manage-only、二次确认、脱敏、无禁文案）

**Consumes：** Task 4；规格 §7。
**Produces：** 前端 API 客户端 + `CandidateCenterDetailView` 面板；**不**改 Timeline 为发送入口。

### 具体文件

| 路径 | 动作 |
|---|---|
| `frontend/src/api/offers.ts` | **新建** 类型与 API 封装 |
| `frontend/src/views/CandidateCenterDetailView.vue` | 替换「不提供发送能力」；`pending_offer && canManage` 显示 `data-test="offer-console-panel"` |
| `frontend/tests/CandidateCenterDetailView.spec.ts` | **更新/追加** Offer 面板用例；保留禁 SMTP/Dify/自动发送；放开「发送 Offer」仅在 manage+面板受控文案 |
| `frontend/tests/offers.spec.ts` | **新建** API 客户端契约（若项目有同类） |
| `frontend/tests/InterviewTimelineView.spec.ts` | **保持**无 Offer 发送入口；标签可仍「录用建议待后续」 |

### UI 行为（锁定）

1. 展示：status、`recipient_email_masked`、version_no、attempt 表（status/`error_message_safe`）。
2. 预览对话框：调 GET detail。
3. 发送：二次确认框文案必须含 **Console** / **非真实邮件** 语义；确认后调 send。
4. `canManage===false`：**无** `offer-console-panel`。
5. 文案 **禁止**：`SMTP`、`Dify`、`自动发送`、`hired`（作为操作目标）。
6. 文案 **允许**：Offer 草稿、就绪、发送（Console）、发送尝试、失败可重试。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `shows offer panel when manage and pending_offer` | 存在 `offer-console-panel` |
| `hides offer panel for execute-only` | 无 panel |
| `send requires confirmation mentioning console` | 确认文案含 Console/非真实 |
| `displays masked recipient and safe error only` | 渲染 masked；失败展示 safe 字段 |
| `forbids smtp dify autosend hired copy in offer panel` | panel 文本 `not.toContain('SMTP'|'Dify'|'自动发送')`；无推进 hired 按钮 |
| `timeline still has no offer send entry` | Timeline 无发送 Offer 操作 |

### GREEN

实现面板与客户端使上表全绿。

### 验证命令

```text
cd frontend
npm.cmd test -- --run tests/CandidateCenterDetailView.spec.ts tests/InterviewTimelineView.spec.ts tests/offers.spec.ts
```

（以仓库既有 vitest 脚本为准；保持等价 `--run` 非交互。）

### 禁止范围（Task 5）

- 禁止邮件配置页；禁止 Timeline/execute 发送；禁止附件 UI。
- 禁止在测试中写真实邮箱正文到快照以外的明文（用 masked fixture）。

### 提交边界

仅前端文件 + vitest；**不**改后端权限矩阵。

---

## Task 6 — 全量回归、安全审查与 mock UAT runbook（只记录不执行）

**Consumes：** Task 1–5。
**Produces：** 回归绿证明（本地执行自动化）、安全审查清单勾选、UAT runbook 正文；**禁止执行 UAT 进程步骤**。

### 具体文件

| 路径 | 动作 |
|---|---|
| 本计划 §6C | **写入/核对** Windows mock UAT runbook（只记录） |
| 可选 `backend/tests/services/test_offers_security_review.py` | 静态源扫描：offers/mail 路径无 smtp、无 ai_tasks 入队、无 hired 写入 |
| 无业务功能文件 | 默认不改；仅修复回归红所需最小 diff |

### 6A — 回归命令（允许执行自动化；禁止 UAT worker）

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/models/test_offer_constants.py tests/db/test_migration_016_offer_console_delivery.py tests/services/test_offers_draft.py tests/services/test_offers_no_hired_side_effects.py tests/services/test_console_mail_provider.py tests/services/test_confirm_offer_send.py tests/workers/test_mail_outbound_queue.py tests/workers/test_mail_send_attempt_retry.py tests/api/v1/test_offers.py tests/services/test_hiring_decisions.py tests/services/test_screening_pending_offer_gate.py tests/services/test_comprehensive_analyses.py tests/api/v1/test_hiring_decisions.py tests/api/v1/test_comprehensive_analyses.py -q --tb=short

cd frontend
npm.cmd test -- --run tests/CandidateCenterDetailView.spec.ts tests/CandidateCenterListView.spec.ts tests/InterviewTimelineView.spec.ts tests/hiringDecisions.spec.ts
```

### 6B — 安全审查清单（实现后勾选）

- [x] 无 `ai_tasks` 行因 Offer 创建；入队任务名仅为 `app.workers.mail_tasks.process_mail_send_attempt`
- [x] `task_routes` 邮件 → `mail_outbound`；≠ `ai_sensitive`；≠ 默认无路由误入 `celery` 的 mail 任务（mail 必须显式路由）
- [x] Settings / `.env.example` 无 SMTP 主机口令字段
- [x] `ConsoleMailProvider` / `mail_tasks` 源无 `smtplib`/`smtp`
- [x] Offer 表无明文邮箱列；audit changes 无正文/明文邮箱
- [x] API list/attempts 无正文；execute 全 403
- [x] 发送后 `pipeline_status==pending_offer`；`status!='hired'`；HiringDecision 行数不因 send 增加
- [x] 综合分析 pending_offer 仍不可 generate
- [x] 初筛仍拒绝 pending_offer
- [x] 文档/脚本未对受保护 UUID 调用 retry/cancel/mark-stale
- [x] 无附件/电子签/接受拒绝 API

### 6C — Windows mock UAT runbook（**只记录，禁止执行**）

> **状态标注：只记录、不执行。**
> 下列步骤供日后人工 UAT；**本 Task / 本计划执行窗口禁止启动 API/worker、禁止 Redis 队列操作、禁止对真实库建 Offer、禁止 SMTP。**
> **Task 6 执行记录（2026-08-21）：本窗口未启动 API/worker、未执行下列任何 UAT 步骤。**

1. **环境（文档约定，不在此执行）**
   - Windows 10+；PowerShell；**仅**虚构 `pending_offer` 隔离候选人（虚构邮箱域）。
   - `CELERY_MAIL_QUEUE_NAME` 为空或 `mail_outbound`。
   - **无** SMTP 环境变量；**不**开 Dify live；provider **仅** Console。
   - Mail worker **必须**同时满足：
     `--pool=solo --concurrency=1 --prefetch-multiplier=1`，且 **仅** `-Q mail_outbound`：
     `celery -A app.workers.celery_app.celery_app worker -Q mail_outbound --pool=solo --concurrency=1 --prefetch-multiplier=1`
   - **禁止** `-Q celery`、`-Q ai_sensitive`、`-Q celery,ai_sensitive,mail_outbound` 或任何多队列混消费。

2. **T0（启动前勾选，本窗口不跑）**
   - 无常驻 worker 误订默认队列。
   - **不** purge / 消费 / 窥探默认 `celery` 消息。
   - **不**触碰：
     `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
     `3556206d-138b-40f6-9b23-97fce178a32e`

3. **T1–T2（未来执行时）**
   - 启动 API（若需）+ **仅** mail worker（命令同上）。
   - manage：创建 → 编辑 → ready → 预览 → 二次确认发送（确认 Console 非真实）。
   - Worker 输出仅 masked/hash；Offer=`sent`；pipeline 仍 `pending_offer`；非 `hired`。
   - 幂等：同 `idempotency_key` 不双 attempt、不双入队。
   - 失败重试：自动 1/5/30 分钟（60/300/1800）后第 4 次 attempt `dead`；人工仅 `failed` 新建 attempt。
   - execute：Offer API 403；UI 无 panel。
   - 可选：注入 Console 失败后人工 retry（不必真等 30 分钟；时钟断言留给自动化）。

4. **T3 收尾**
   - 只停 mail worker；**不** purge 默认/`ai_sensitive`；**不** SQL 改 AI task。

5. **失败即停**
   - 任何步骤需要 SMTP、真实外发、Dify、默认/`ai_sensitive` 消费、或触碰受保护 running → 立即停止并记违规。

6. **本 Task 6 窗口：上述 UAT 步骤一律不执行。**

### 禁止范围（Task 6）

- **禁止执行** §6C UAT。
- 禁止启动 mail/AI/默认 worker；禁止触碰默认队列与受保护 running。
- 禁止把 runbook 当自动脚本执行。

### 提交边界

若有回归修复，最小 diff 单独提交；**不**提交 UAT 日志、`.env`、队列 dump。默认可无新功能文件（仅审查+runbook）。

---

## 计划自检

### 规格逐节映射

| 规格 | 计划落点 |
|---|---|
| §1 范围/目标/非目标 | 全局约束 + 各 Task 禁止项 + Task 6 |
| §2 源码事实 | Task 1–3 |
| §3 模型/迁移 | Task 1 |
| §4 状态机 | Task 2（草稿）· Task 3（发送） |
| §5 服务/Provider/队列/重试 | Task 2–3 |
| §6 API/权限 | Task 4 |
| §7 前端 | Task 5 |
| §8 审计 | Task 2–4 |
| §9 测试/UAT | Task 1–5 RED/GREEN + Task 6 |
| §10 范围外 | 全局约束 |
| §11 稳定符号 | 本计划稳定符号表 |
| §12 规格自检 | Task 6 审查清单覆盖 |

### 完成度勾选

- [x] 六任务 TDD：文件清单、接口/结构、RED、GREEN、验证命令、禁止范围、提交边界
- [x] 规格 §1–§12 均有映射；无悬空章节
- [x] **无** `TBD` / `TODO` 占位
- [x] **锁定** 不复用 `ai_tasks` / `ai_sensitive` / 默认 `celery`
- [x] **锁定** 不接 SMTP、不真实外发、不写邮件配置（除队列名）
- [x] **锁定** 不推进 `hired`；不改 HiringDecision/综合分析
- [x] **锁定** 无附件/电子签/接受拒绝
- [x] **锁定** 不触碰默认队列与两条历史 running
- [x] Windows UAT worker：`--pool=solo --concurrency=1 --prefetch-multiplier=1 -Q mail_outbound`
- [x] UAT runbook 标注「只记录、不执行」
- [x] 本文件仅计划；未改业务代码、未提交、未执行 UAT
