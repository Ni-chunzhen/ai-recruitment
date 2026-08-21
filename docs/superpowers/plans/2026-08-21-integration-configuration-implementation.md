# 第三方配置与连通性管理（阶段 11）— TDD 实施计划

> **For agentic workers:** 按 Task 1→5 顺序执行；每项先 RED 再 GREEN。未经用户明确要求禁止 `git add` / `commit` / `push`。
> **Task 5 UAT runbook：只记录、禁止执行**（零真实 Dify、零真实 MinIO 业务写、零 SMTP、零队列消费、零触碰受保护 ID）。

**规格：** `docs/superpowers/specs/2026-08-21-integration-configuration-design.md`
**基线：** Offer Console / 迁移 **016** / `mail_outbound` / Console-only 已合入；集成配置全靠 env `Settings`；**无** `integration_secrets`、**无** `integration.manage`、**无** 配置 API/页。
**方法：** TDD。符号名锁定为规格 §13；禁止临时改名。

## 全局约束

- **不**引入 SMTP / 真实邮件 / 写回 `.env` / 配置热更新 / 自动开 Dify live / 改 `/health/ready`。
- **不**入队 `mail_outbound` / `ai_sensitive` / 默认 `celery`；连通性测试 **零** `apply_async`。
- **不**让 `recruitment.manage` / `interview.execute` / `audit.read` / `ai_task.manage` 获得集成读写测。
- **不**触碰受保护 running：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- `DATA_ENCRYPTION_KEY` **只**留环境变量；永不入库、永不进 GET。
- 本计划各任务 **默认不提交**；「提交边界」仅当用户明确要求时适用。
- Task 1–4 自动化 **不**启动常驻 API/worker；外呼一律 mock。

## 稳定符号（不得改名）

| 符号 | 锁定 |
|---|---|
| 迁移 | `017_integration_secrets`；`down_revision = 016_offer_console_delivery` |
| 表 / ORM | `integration_secrets` / `IntegrationSecret` |
| 权限 | `integration.manage`（仅 `system_admin`） |
| Providers | `dify` · `minio` · `mail` |
| 审计 | `integration.config_updated` · `integration.connectivity_tested` |
| 邮件展示 | `delivery_provider=console`；`smtp_enabled=false` |
| PUT 空密钥 | 保留原密文 |
| test data 字段 | **仅** `ok` · `error_code` · `latency_ms` |
| PUT 响应 | 恒含 `restart_required=true` |
| 根密钥禁覆盖 | `DATA_ENCRYPTION_KEY` · `DATABASE_URL` · `REDIS_URL` · `CELERY_BROKER_URL` · `JWT_SECRET` |
| 前端路由 | `/system/integrations`；`meta.permission='integration.manage'` |
| `data-test` | `integration-config-page` |

## 规格覆盖映射

| 规格章节 | 本计划 Task |
|---|---|
| §3 迁移/模型 · §7.1 权限 bootstrap · §13 | Task 1 |
| §4 覆盖优先级 · §7.2 字段名脱敏 · 根密钥禁覆盖 · crypto | Task 2 |
| §5 功能块 · §6 连通性 · §7.2 审计动作 · Service | Task 3 |
| §8.1 API · §9 `.env.example` | Task 4 |
| §8.2–8.3 前端 · §10 测试/UAT · §11–12 验收/非目标 | Task 5 |

---

## Task 1 — 017 迁移、IntegrationSecret 模型、`integration.manage` 权限与 bootstrap 矩阵

**Consumes：** 规格 §3、§7.1、§13。
**Produces：** ORM + 白名单常量 + Alembic 017 + bootstrap 权限；**无** 覆盖解析 / API / 前端。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/models/integration_secret.py` | **新建** ORM + provider/key 白名单常量 |
| `backend/app/models/__init__.py` | 导出 |
| `backend/alembic/versions/017_integration_secrets.py` | **新建** upgrade/downgrade |
| `backend/app/services/bootstrap.py` | 增加 `integration.manage` 到 `PERMISSION_DEFINITIONS`（system_admin 经 `list(keys)` 自动获得；断言 recruiter/interviewer 矩阵 **不含**） |
| `backend/tests/models/test_integration_secret_constants.py` | **新建** |
| `backend/tests/db/test_migration_017_integration_secrets.py` | **新建** |
| `backend/tests/db/test_migration_016_offer_console_delivery.py`（及更早 head 断言若存在） | **必要最小改**：接纳 head=017 或「016 在链上」 |
| `backend/tests/services/test_bootstrap_integration_permission.py` 或扩展既有 bootstrap/RBAC 测 | **新建/扩展** |

### 精确结构（示意）

```python
# models/integration_secret.py
INTEGRATION_PROVIDER_DIFY = "dify"
INTEGRATION_PROVIDER_MINIO = "minio"
INTEGRATION_PROVIDER_MAIL = "mail"
INTEGRATION_PROVIDERS = frozenset({...})

# 白名单 config_key 与 is_secret 映射（规格 §3.3）
DIFY_CONFIG_KEYS: dict[str, bool] = {...}  # key -> is_secret
MINIO_CONFIG_KEYS: dict[str, bool] = {...}
MAIL_CONFIG_KEYS: dict[str, bool] = {"provider_name": False, "queue_name": False}

ROOT_SECRET_ENV_NAMES = frozenset({
    "DATA_ENCRYPTION_KEY", "DATABASE_URL", "REDIS_URL",
    "CELERY_BROKER_URL", "JWT_SECRET",
})

class IntegrationSecret(Base):
    __tablename__ = "integration_secrets"
    # id, provider, config_key, secret_ciphertext, value_nonsecret,
    # is_secret, enabled, updated_by, created_at, updated_at
    # UniqueConstraint(provider, config_key)
```

迁移：`revision="017_integration_secrets"`；`down_revision="016_offer_console_delivery"`；源文件 **无** `smtp`；**不** ALTER Offer / `ai_tasks`。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_integration_providers_and_keys_whitelist` | providers 仅 dify/minio/mail；Dify/MinIO 密钥键 `is_secret=True`；mail 无 smtp 键 |
| `test_root_secret_env_names_locked` | 含 `DATA_ENCRYPTION_KEY` 等；与业务 config_key 无交集 |
| `test_migration_017_revision_chain` | down=016；head 链含 017 |
| `test_migration_017_upgrade_creates_table_and_unique` | 源含 `integration_secrets`、`uq_integration_secrets_provider_key`；无 smtp；downgrade drop |
| `test_permission_integration_manage_system_admin_only` | 定义存在；system_admin 有；recruiter_admin/interviewer **无** |

### GREEN

实现模型、迁移、bootstrap，使上表全绿。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/models/test_integration_secret_constants.py tests/db/test_migration_017_integration_secrets.py tests/services/test_bootstrap_integration_permission.py -q --tb=short
```

（若 bootstrap 测合入既有文件，替换为实际路径。）

### 禁止范围（Task 1）

- 禁止实现覆盖加载、API、前端、连通性外呼。
- 禁止 SMTP 列/Settings。
- 禁止改 `/health/ready`。

### 提交边界

模型 + 017 + bootstrap + 测试；**不**提交 `.env`。

---

## Task 2 — 配置解析服务：环境基线 + DB 启用项覆盖、密文加解密、字段名脱敏、禁止覆盖根密

**Consumes：** Task 1；规格 §4、§7.2 脱敏、根密钥禁覆盖。
**Produces：** 有效配置解析 + 审计字段名 scrub 增强；**无** HTTP 路由 / 前端。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/services/integration_config.py`（或 `effective_integrations.py`） | **新建** 加载快照、按 provider/key 取值、白名单校验 |
| `backend/app/repositories/integration_secrets.py` | **新建** list/get/upsert 辅助 |
| `backend/app/services/audit.py` | **扩展** `_scrub_value`：先按 **字段名** 脱敏，再保留值内容 marker scrub |
| `backend/app/main.py` lifespan（或等价启动钩子） | **最小** 调用加载快照（可先 no-op 接线，完整行为本 Task 测驱动）；**不**热更新 |
| `backend/tests/services/test_integration_config_overlay.py` | **新建** |
| `backend/tests/services/test_audit_field_name_scrub.py` | **新建/扩展** |

### 精确行为

```python
async def load_integration_overlay(session) -> IntegrationOverlay: ...
def resolve_nonsecret(overlay, settings, provider, config_key) -> str | None: ...
def resolve_secret(overlay, settings, provider, config_key) -> str | None: ...
# 优先级：enabled DB 行（可解密）> Settings/env > default
# is_secret 行：decrypt_secret(ciphertext)；失败 → 视作无覆盖并记安全日志（无密文）

def assert_not_root_secret(config_key_or_env: str) -> None: ...
# 拒绝覆盖 ROOT_SECRET_ENV_NAMES

# audit.py
SENSITIVE_KEY_NAMES = frozenset({...规格 §7.2...})
def _scrub_value(value):
    # dict: if key.lower() in SENSITIVE_KEY_NAMES or key.endswith(...): value="[redacted]"
    # then existing value-marker scrub
```

邮件只读解析：`delivery_provider` 恒 `"console"`；`queue_name` 来自 `settings.celery_mail_queue_name`（**不**写 DB 覆盖队列，除非规格 mail 行只作展示镜像——实现锁定：**队列名只读 Settings**，mail 表行可选不写）。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_overlay_enabled_db_secret_overrides_env` | DB 密文解密后覆盖 Settings |
| `test_overlay_disabled_falls_back_to_env` | `enabled=false` → env |
| `test_overlay_decrypt_failure_falls_back` | 坏密文 → 回退 env；无异常泄露 |
| `test_reject_root_secret_keys` | 试图解析/写入根密 → 校验错误 |
| `test_whitelist_rejects_unknown_provider_key` | 未知 key 拒绝 |
| `test_audit_scrubs_by_field_name` | `{"api_key": "x"}` → `[redacted]`；非敏感键保留 |
| `test_audit_still_scrubs_value_markers` | 值含 `enc:v1:` / `bearer` 仍 scrub |
| `test_mail_block_is_console_only` | 解析结果 `console`；无 smtp 字段 |

### GREEN

实现 overlay + scrub，使上表全绿。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/services/test_integration_config_overlay.py tests/services/test_audit_field_name_scrub.py -q --tb=short
```

### 禁止范围（Task 2）

- 禁止真实 Dify/MinIO 网络；禁止 enqueue；禁止 SMTP。
- 禁止把 `DATA_ENCRYPTION_KEY` 写入表。

### 提交边界

解析服务 + audit scrub + 测试。

---

## Task 3 — 集成配置应用服务：摘要 / 只写更新 / 连通性测试 / 审计动作

**Consumes：** Task 1–2；规格 §5、§6、§7.2 actions。
**Produces：** `get_integrations_summary`、`update_dify`、`update_minio`、`test_dify`、`test_minio`；**无** FastAPI 路由（可先纯 service）。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/services/integrations.py` | **新建** 业务服务 |
| `backend/app/services/integration_connectivity.py`（可合并） | Dify/MinIO test 探测（可注入 httpx/Minio client） |
| `backend/tests/services/test_integrations_summary_and_update.py` | **新建** |
| `backend/tests/services/test_integration_connectivity.py` | **新建** |

### 精确行为

- **GET 摘要：** 非密钥回显当前有效值；密钥仅 `configured: bool`；mail 块固定；附 `live_enabled_env` 只读；**永不**返回 ciphertext/明文 Key。
- **PUT：** 部分更新；空/缺省密钥字段 **保留**；新密钥 → `encrypt_secret` 写入；`ai_provider`∈{mock,dify}；**拒绝**写 live 开关；mail PUT → 业务错误。
- **test：** 返回 dataclass/`ConnectivityResult(ok, error_code, latency_ms)`；未配置 → `not_configured`；mock 外呼；**断言测试内无** `apply_async`。
- **审计：** 成功 update → `integration.config_updated`；test → `integration.connectivity_tested`；changes 经字段名 scrub。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_summary_secrets_only_configured_flag` | 无 api_key 明文 |
| `test_update_empty_secret_keeps_previous` | 留空保留 |
| `test_update_rejects_live_enabled_field` | 422/校验错 |
| `test_update_rejects_smtp_and_mail_write` | 拒绝 |
| `test_update_audits_without_secret_values` | changes 仅键名 |
| `test_dify_test_not_configured` | ok=false, error_code=not_configured |
| `test_dify_test_success_shape` | mock；仅三字段；无 body |
| `test_minio_test_uses_bucket_exists_only` | mock；不 put/get object |
| `test_connectivity_never_enqueues` | 无 Celery 入队 |

### GREEN

实现服务使上表全绿。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/services/test_integrations_summary_and_update.py tests/services/test_integration_connectivity.py -q --tb=short
```

### 禁止范围（Task 3）

- 禁止真实外呼；禁止改 Offer/AI 业务表语义；禁止改 ready。

### 提交边界

services + 测试。

---

## Task 4 — Admin Integrations API、Schemas、路由注册、`.env.example`

**Consumes：** Task 3；规格 §8.1、§9。
**Produces：** HTTP API；**无** 前端页面。

### 具体文件

| 路径 | 动作 |
|---|---|
| `backend/app/schemas/integration.py` | **新建** In/Out；`extra=forbid`；密钥可选 |
| `backend/app/api/v1/endpoints/admin_integrations.py` | **新建** |
| `backend/app/api/v1/router.py` | include |
| `backend/.env.example` | 补 MinIO、Resume Dify 占位；注释 DB 覆盖与重启；**无** SMTP |
| `backend/tests/api/v1/test_admin_integrations.py` | **新建** |
| `backend/tests/services/test_offers_security_review.py` 或专用 | 断言 ready 源仍仅 pg+redis；integrations API 无 smtp |

### 端点锁定

| 方法 | 路径 |
|---|---|
| GET | `/api/v1/admin/integrations` |
| PUT | `/api/v1/admin/integrations/dify` |
| PUT | `/api/v1/admin/integrations/minio` |
| POST | `/api/v1/admin/integrations/dify/test` |
| POST | `/api/v1/admin/integrations/minio/test` |

一律 `require_permission("integration.manage")`；写/测 `Cache-Control: no-store`；PUT 响应含 `restart_required: true`。

### RED

| 测试函数 | 精确断言 |
|---|---|
| `test_get_requires_integration_manage` | recruiter/audit.read/execute → 403；system_admin 200 |
| `test_get_never_returns_raw_secrets` | 扫描 JSON 无典型密钥模式 / ciphertext 前缀 |
| `test_put_dify_restart_required` | `restart_required is True` |
| `test_put_minio_and_get_configured` | configured 翻转；仍无明文 |
| `test_post_test_response_only_three_fields` | data 仅 ok/error_code/latency_ms |
| `test_no_mail_put_or_test_routes` | 无 smtp 路由；mail 写 405/404/422（锁定一种） |
| `test_env_example_documents_minio_resume_dify_without_smtp` | example 断言 |

### GREEN

接线 API 使上表全绿。

### 验证命令

```text
cd backend
.venv\Scripts\python.exe -m pytest tests/api/v1/test_admin_integrations.py -q --tb=short
```

### 禁止范围（Task 4）

- 禁止启动真实服务；禁止写真实 `.env`。

### 提交边界

API + schemas + `.env.example` + 测试。

---

## Task 5 — 前端集成配置页、全量回归、安全审查、mock UAT runbook（只记录不执行）

**Consumes：** Task 1–4；规格 §8.2–8.3、§10–12。
**Produces：** 前端页 + 回归证明 + UAT runbook 正文；**禁止执行** UAT 进程步骤。

### 具体文件

| 路径 | 动作 |
|---|---|
| `frontend/src/api/integrations.ts` | **新建** |
| `frontend/src/views/SystemIntegrationsView.vue`（名可微调） | **新建**；`data-test="integration-config-page"` |
| `frontend/src/router/index.ts` | 路由 + `permission: 'integration.manage'` |
| 导航组件（现有 layout/sidebar） | system_admin 入口 |
| `frontend/tests/SystemIntegrationsView.spec.ts` | **新建** |
| 本计划 §5C | **写入** mock UAT runbook 执行记录栏（本窗口不执行） |

### 前端 RED

| 测试 | 断言 |
|---|---|
| 无 `integration.manage` 不可见入口 / 路由守卫 | |
| 三块：Dify、MinIO、邮件只读 | 无 SMTP 表单；无「发送测试邮件」「自动开启 live」文案 |
| 密钥 input `type=password`；空=不提交该字段 | |
| 保存成功展示重启提示 | 含 API/worker 重启意图 |
| test 按钮仅 Dify/MinIO | 展示 ok/error_code/latency |

### 5A — 回归命令（允许自动化）

```text
cd backend
.venv\Scripts\python.exe -m pytest -q --tb=line

cd ..\frontend
pnpm test
pnpm type-check

cd ..
git diff --check
```

（可选）`recruit_test` 上 **017↔016** 往返；**禁止**对业务库 `recruit` 在未授权时乱升。

### 5B — 静态安全审查清单

- [ ] Settings / API / 前端 **无** SMTP 字段与表单
- [ ] GET/审计路径无密钥回显
- [ ] test 无 enqueue、无上游 body
- [ ] `run_readiness_checks` 仍仅 postgresql+redis
- [ ] live 开关不可经 API 写入
- [ ] 未触碰受保护 UUID；无默认队列 purge

### 5C — Windows mock UAT runbook（**只记录，禁止执行**）

> **状态标注：只记录、不执行。Task 5 完成时本窗口已写入记录，步骤一律未实际执行。**
> 本 Task / 本计划执行窗口 **禁止** 启动 API/worker、禁止真实 Dify/MinIO 业务写、禁止 SMTP、禁止 Redis 队列操作、禁止触碰受保护 running。

| 步骤 | 意图 | 本窗口状态 |
|---|---|---|
| T0 | revision≥017；worker=0；不打印真实密钥 | **未执行**（仅文档） |
| T1 | system_admin → 集成页 → 虚构值保存 → 见 `restart_required` →（人工）重启后 GET `configured=true` 无回显 | **未执行** |
| T2 | Dify/MinIO mock test → 仅 ok/error_code/latency_ms；零 workflow run、零入队 | **未执行** |
| T3 | recruiter_admin / interviewer → 403 / 无菜单 | **未执行**（由自动化单测覆盖等价断言） |
| T4 | live env 未变；未入队默认/`ai_sensitive`/`mail_outbound` 消费；ready 不依赖第三方；禁触碰受保护 running UUID | **未执行** |

**失败即停规则（若将来人工执行）：** 任何步骤需要 SMTP、真实外发、改 ready、写 `.env`、开 live、操作队列 / purge、触碰受保护任务 → 停止并记违规。

6. **本窗口：上述步骤一律不执行。**

### 禁止范围（Task 5）

- **禁止执行** §5C UAT。
- 禁止 push（除非用户另示）。

### 提交边界

前端 + 测试 + 计划 runbook 勾选；**不**提交密钥与 UAT 日志。

---

## 任务依赖图

```text
Task1 (017+model+RBAC)
  → Task2 (overlay+scrub)
    → Task3 (service+connectivity+audit actions)
      → Task4 (API+env.example)
        → Task5 (frontend+regression+UAT record-only)
```

## 完成定义（整阶段）

- [x] 017 可升级可回滚至 016
- [x] 仅 system_admin 读/写/测；密钥永不回显
- [x] DB 覆盖 + 字段名脱敏 + 根密禁覆盖
- [x] Dify/MinIO test 零泄露零入队；邮件只读 Console
- [x] 前端重启提示；无 SMTP/live 自动开；ready 未改
- [x] 后端全量 pytest + 前端 vitest/type-check 绿；`git diff --check` 绿
- [x] UAT runbook 已记录且本窗口未执行
- [x] **运行时接线**：API lifespan + Celery `worker_process_init` 加载 process overlay；Dify/MinIO/`AI_PROVIDER` 经 `effective_*` 消费（无热更新，仍需重启）
