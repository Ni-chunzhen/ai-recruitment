# 第三方配置与连通性管理（阶段 11）设计规格

基线：当前工作区已合入 Offer Console 投递（迁移 **016**、`mail_outbound`、Console-only）；Dify / MinIO / 队列名等 **全部** 由进程环境变量 → `Settings` 加载；**无** 集成配置表、**无** 配置管理 API、**无** 前端配置页；`DATA_ENCRYPTION_KEY` 已用于业务字段 Fernet 加密；RBAC **无** `integration.*` 权限。

本规格只定义：**阶段 11 最小安全第三方配置能力**——密文表 `integration_secrets`（迁移 **017**）、Settings 覆盖优先级、Dify / MinIO 可管理配置与只写密钥、邮件块 **只读展示** Console + `mail_outbound` 状态（**不**引入 SMTP）、权限 `integration.manage`（仅 `system_admin`）、按字段名强制脱敏的审计、零泄露连通性测试、前端配置页、运行时刷新提示、测试与 mock UAT runbook。

本文件是规格，**不**含逐步实现计划；**不**改业务代码、**不**执行迁移、**不**启动 API/worker、**不**调用 Dify、**不**读写 MinIO 业务对象、**不**发邮件、**不**操作 Redis 队列、**不**触碰受保护 AI running。

方案锁定：

1. **新建** `integration_secrets` 密文表；密钥密文使用既有 `encrypt_secret` / `decrypt_secret`；**`DATA_ENCRYPTION_KEY` 仍只留环境变量**，永不入库、永不进配置页。
2. 配置页 **只管理 Dify 与 MinIO**；邮件块 **仅展示**「provider=console」与队列名解析结果，**完全不引入 SMTP**。
3. 新权限 **`integration.manage`**：**仅** `system_admin` 可读、写、测；`recruiter_admin` / `interviewer` **零访问**。
4. 密钥 **只写不回显**：GET / 审计 / 错误响应永不返回明文或可逆密文；敏感字段 **按字段名** 强制脱敏。
5. 连通性测试响应 **仅** `ok` / `error_code` / `latency_ms`；**不**返回上游正文或 headers；**不**入任何 Celery 队列。
6. 配置变更后 **明确要求重启 API 与对应 worker**；**不**因写入 Dify Key 而自动打开 live。
7. **`/health/ready` 不纳入** Dify / MinIO。

关联（本规格不重复改写其已锁定语义，仅声明继承或差分）：

| 文档 | 关系 |
|---|---|
| 只读架构审计结论（阶段 11 审计会话） | **采纳**：密文表 + env 根密钥；配置页只管 Dify/MinIO；邮件只读 Console；`integration.manage` 仅 system_admin；零泄露 test；不改 ready |
| `docs/superpowers/specs/2026-08-20-offer-console-delivery-design.md` | 邮件 **继续** Console-only + `mail_outbound`；本规格 **禁止** 为 Offer/邀约引入 SMTP Settings 或 SMTP provider |
| `docs/superpowers/specs/2026-08-20-comprehensive-interview-analysis-design.md` 等 AI 规格 | Dify live / 敏感队列语义 **继承**；本规格 **不** 自动启用任何 live 开关；**不**改 `AI_PROVIDER` 默认业务路径以外的强制 mock 契约（若某 task 规格强制 mock，配置页写入 Key **仍不**解除该强制） |
| `backend/app/core/config.py` / `.env.example` | 环境变量仍为 **启动基线**；DB 密文为 **可覆盖层**（见 §4） |
| `backend/app/services/crypto.py` | Fernet 加解密 **必须** 复用；失败须 fail-closed |
| `backend/app/services/bootstrap.py` | **扩展** `PERMISSION_DEFINITIONS` + `ROLE_PERMISSION_MATRIX` |
| `backend/app/services/audit.py` | 现有值级 scrub **不足**；本规格要求 **按字段名** 强制脱敏（见 §7） |

---

## 1. 范围

### 1.1 目标

1. 新增 Alembic 迁移 **017**：表 `integration_secrets`（及必要唯一约束/索引）；`down_revision = 016_offer_console_delivery`。
2. 提供 manage-admin API：**读取摘要**、**部分更新**、**连通性测试**；密钥字段写入后不可通过任何 API 读回明文。
3. 运行时配置解析：**环境变量 Settings 为基线**；`integration_secrets` 中已启用行 **按 provider+key 覆盖** 对应有效值（见 §4）；`DATA_ENCRYPTION_KEY` / `DATABASE_URL` / `JWT_SECRET` / Redis 等 **平台根密钥禁止** 进入本表与本页。
4. 前端新增 **仅 system_admin** 可见的「集成配置」页：Dify 块、MinIO 块、邮件只读块；写后展示 **必须重启** 提示。
5. 连通性测试：Dify / MinIO 各自独立；超时受控；结果三元组；零队列、零业务副作用。
6. 审计：配置更新与连通性测试均记审计；changes **无密钥**。

### 1.2 第一期交付物（本规格后实现须覆盖）

| 层 | 交付 |
|---|---|
| Alembic | 迁移 **017** 建 `integration_secrets` |
| 模型 / 常量 | ORM + provider/key 枚举；禁止密钥常量散落魔法字符串 |
| Bootstrap / RBAC | `integration.manage`；仅挂 `system_admin` |
| Settings 解析 | 覆盖层加载器（启动或显式 reload 钩子，见 §8）；**无** 热更新保证 |
| Service | get 摘要、upsert（只写）、test_dify、test_minio；邮件状态只读组装 |
| API | `/api/v1/admin/integrations…`（路径实现可微调，语义锁定 admin + integration） |
| 前端 | 配置页 + 路由 `permission: integration.manage`；密码框空=不修改 |
| 测试 / UAT | 见 §10；规格只定义，本文件不执行 |

### 1.3 非目标（硬性）

- **不**引入 SMTP、邮件 SaaS、真实外发、站内通知、短信/IM。
- **不**发真实邮件；**不**创建 Offer/邀约发送 attempt；**不**入队 `mail_outbound` / `ai_sensitive` / 默认 `celery`。
- **不**做附件存储策略配置以外的业务改造；**不**改 MinIO 对象布局/bucket 业务语义以外的上传 API。
- **不**做配置热更新（改完即对所有进程立即生效）；**不**把密钥写回 `.env` 文件。
- **不**因本页操作自动设置 `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED=true` 或任何 live 开关。
- **不**修改 `/health` / `/health/live` / `/health/ready` 的检查集合（ready **仍仅** Postgres + Redis）。
- **不**在本规格实施窗口或 UAT 中：启动 API/worker（除 mock UAT 文档记录外本文件不执行）、调用真实 Dify、读写真实 MinIO 业务桶数据作演示、操作 Redis 队列、触碰受保护 running：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`
- **不**让 `recruitment.manage` / `interview.execute` / `audit.read` / `ai_task.manage` 获得集成配置读写测权限（`audit.read` **不**等同本页访问）。

---

## 2. 源码事实（实现必须对齐）

| 符号 / 路径 | 现状 | 本规格 |
|---|---|---|
| `Settings` Dify / MinIO / mail 队列 | 全 env；MinIO 与部分 Resume Dify Key **未**完整写入 `.env.example` | 保留 env 基线；DB 覆盖；example **补齐文档化非密钥与密钥占位**（仍无 SMTP） |
| SMTP Settings 字段 | **不存在** | **继续禁止** |
| `ConsoleMailProvider` / `mail_outbound` | 阶段 10 已交付 | 邮件块 **只读展示**；不改投递路径 |
| `encrypt_secret` / `DATA_ENCRYPTION_KEY` | 业务密文字段 | 本表密文列 **必须** 使用；根密钥 **仅** env |
| `get_settings` `lru_cache` | 进程内缓存 | 覆盖层须在 **进程启动**（及可选显式 `cache_clear`+重载，非热更新承诺）装入；文档要求重启 |
| `run_readiness_checks` | 仅 postgresql / redis | **不改** |
| `PERMISSION_DEFINITIONS` | 无 integration | **新增** `integration.manage` |
| `record_audit` / `_scrub_value` | 按 **值内容** marker scrub | **额外** 按字段名强制 redact（§7） |
| 前端 `/system/ai-tasks` | `audit.read` | **另增** 集成配置路由；权限独立 |

---

## 3. 模型与迁移

### 3.1 迁移标识（锁定）

| 项 | 值 |
|---|---|
| 文件 | `backend/alembic/versions/017_integration_secrets.py`（文件名可微调，**revision id 必须**为下值） |
| `revision` | `017_integration_secrets` |
| `down_revision` | `016_offer_console_delivery` |
| upgrade | 建表 + 唯一约束 |
| downgrade | drop 表（**不**触碰 016 Offer 表、**不**改 `ai_tasks`） |

### 3.2 表 `integration_secrets`

| 列 | 类型 | 约束 / 说明 |
|---|---|---|
| `id` | UUID PK | |
| `provider` | String(32) NOT NULL | 枚举见 §3.3 |
| `config_key` | String(64) NOT NULL | 枚举见 §3.3 |
| `secret_ciphertext` | Text NULL | 可空：表示「非密钥类」或「仅元数据」；密钥类写入后为 `enc:v1:…` |
| `value_nonsecret` | Text NULL | **仅**允许非敏感配置（如 base_url、endpoint、bucket、workflow_id、bool 开关的规范化字符串）；**禁止**存放任何 Key/Secret |
| `is_secret` | Boolean NOT NULL | 该行是否按密钥处理（GET 永不回显密文） |
| `enabled` | Boolean NOT NULL default true | false=忽略覆盖，回退 env |
| `updated_by` | UUID NULL FK → users | ON DELETE SET NULL |
| `created_at` / `updated_at` | timestamptz NOT NULL | |
| **唯一** | `uq_integration_secrets_provider_key` | (`provider`, `config_key`) |

Check（建议）：

- `provider IN ('dify','minio','mail')`
- `is_secret = true` 时：`value_nonsecret` 必须为 NULL；明文禁止落库（仅 ciphertext）
- `is_secret = false` 时：`secret_ciphertext` 必须为 NULL

**禁止列：** 明文 `api_key`、`secret_key`、`password`、SMTP 主机账号等。

### 3.3 允许的 `(provider, config_key)` 白名单（锁定）

#### Dify（`provider=dify`）

| config_key | is_secret | 覆盖的 Settings / 语义 |
|---|---|---|
| `api_base_url` | false | `DIFY_API_BASE_URL` |
| `api_key` | true | `DIFY_API_KEY` |
| `jd_parse_api_key` | true | `DIFY_JD_PARSE_API_KEY` |
| `score_dimension_api_key` | true | `DIFY_SCORE_DIMENSION_API_KEY` |
| `jd_parse_workflow_id` | false | `DIFY_JD_PARSE_WORKFLOW_ID` |
| `score_dimension_workflow_id` | false | `DIFY_SCORE_DIMENSION_WORKFLOW_ID` |
| `resume_parse_api_key` | true | `DIFY_RESUME_PARSE_API_KEY` |
| `resume_score_api_key` | true | `DIFY_RESUME_SCORE_API_KEY` |
| `resume_parse_workflow_id` | false | `DIFY_RESUME_PARSE_WORKFLOW_ID` |
| `resume_score_workflow_id` | false | `DIFY_RESUME_SCORE_WORKFLOW_ID` |
| `interview_question_generate_api_key` | true | `DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY` |
| `interview_question_generate_workflow_id` | false | `DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID` |
| `ai_provider` | false | `AI_PROVIDER`（仅允许 `mock` / `dify` 字面量） |

**明确不进表、不进页：**

- `DIFY_INTERVIEW_QUESTION_LIVE_ENABLED`（及任何 live 开关）— **只**能通过环境变量运维；本页 **只读展示** env 当前解析布尔（可选），**不可写**。
- 轮次/综合分析若尚无独立 Settings Key：本批 **不** 发明新 live Key；需要时另开规格。

#### MinIO（`provider=minio`）

| config_key | is_secret | 覆盖 |
|---|---|---|
| `endpoint` | false | `MINIO_ENDPOINT` |
| `access_key` | true | `MINIO_ACCESS_KEY`（一期起 access 亦按密钥只写不回显，避免默认字面量泄露习惯） |
| `secret_key` | true | `MINIO_SECRET_KEY` |
| `bucket` | false | `MINIO_BUCKET` |
| `secure` | false | `MINIO_SECURE`（存 `"true"`/`"false"`） |
| `presign_seconds` | false | `MINIO_PRESIGN_SECONDS`（十进制整数字符串） |

#### Mail（`provider=mail`）— **只读语义**

| config_key | is_secret | 说明 |
|---|---|---|
| `provider_name` | false | 固定展示值 `console`；**禁止** API 写成其它值 |
| `queue_name` | false | 只读镜像 `celery_mail_queue_name` 解析结果；**禁止**本页修改队列名（改队列仍走 env + 重启） |

本批 **不** 接受 SMTP 相关 `config_key`；请求体出现 `smtp_*` → **422**。

---

## 4. Settings 覆盖优先级

对任意可覆盖项 `K`：

1. 若存在 `integration_secrets` 行：`provider+config_key` 匹配、`enabled=true`、且（密钥行密文可解密 / 非密钥行 `value_nonsecret` 非空）→ **使用 DB 值**。
2. 否则 → **使用环境变量 / Settings 默认值**。
3. `DATA_ENCRYPTION_KEY`、`DATABASE_URL`、`REDIS_URL`、`CELERY_BROKER_URL`、`JWT_SECRET`、以及本规格未列白名单的键 → **永不** 被本表覆盖。

装载时机（锁定）：

- API 进程：**启动 lifespan** 时调用 `bootstrap_integration_overlay`，将覆盖快照写入进程内 `get_process_overlay()`（**已实现**）。
- Celery worker（含 AI / mail）：`worker_process_init` 同步加载同一 overlay（**已实现**）。
- 业务取配置：Dify provider / MinIO storage / AI `AI_PROVIDER` 选择 **只** 经 `effective_*` accessor 读 process overlay ← env；**不**在请求中热更新、**不**改 `get_settings()` 缓存。
- PUT 成功响应 **必须** 包含稳定提示码，例如 `restart_required=true` 与文案键 `integrations.restart_required`（前端展示，见 §8）。

解密失败：该密钥行视为 **无效覆盖**（记 warning 日志，**不**打印密文）；回退 env；GET 摘要将该项标为 `configured=false` 或 `status=decrypt_error`（**不**回显密文）。

---

## 5. 功能块

### 5.1 Dify

- GET：各非密钥字段明文（base_url、workflow_id、`ai_provider`）；各密钥字段仅 `configured: bool` + 可选 `hint`（如 `last4` **仅当**能安全从密文解密后派生；若实现困难可一期只给 `configured`）。
- PUT：部分更新；密钥字段 **省略或空字符串 = 保留原密文**；显式发送新值则加密写入。
- `ai_provider` 写入校验：仅 `mock`|`dify`。
- **禁止** PUT live 开关；GET 可附带 `live_enabled_env: bool`（只读来自 Settings）。
- test：见 §6.1。

### 5.2 MinIO

- GET / PUT 同脱敏与只写规则。
- `presign_seconds` 校验：正整数，上限实现固定（建议 ≤ 86400）。
- test：见 §6.2；**禁止** upload/download 业务对象作为测试副作用（可用 `bucket_exists` 或等价无写入探测）。

### 5.3 邮件（只读）

- GET 邮件块固定：
  - `delivery_provider`: `"console"`
  - `queue_name`: 当前解析的 `celery_mail_queue_name`（默认 `mail_outbound`）
  - `smtp_enabled`: `false`
  - `note`: 固定说明「一期仅 Console，无 SMTP」
- **无** PUT；**无** test 发信；若客户端调用 mail test → **405 或 422**（实现选定后测试锁定）。

---

## 6. 零泄露连通性测试

### 6.1 通用契约

请求：`POST …/integrations/{provider}/test`（或分端点）；**无** body 密钥（使用当前有效配置）。

响应 **仅允许** 字段：

```json
{ "ok": true, "error_code": null, "latency_ms": 12 }
```

或失败：

```json
{ "ok": false, "error_code": "minio_unreachable", "latency_ms": 30 }
```

**禁止** 响应/日志/审计出现：上游 HTTP body、headers、Authorization、Stack 原文、密钥、bucket 对象列表、Dify workflow 输出。

**禁止** `apply_async` / 任何队列入队。

超时：实现固定短超时（建议 ≤ 5s）；超时 → `error_code=timeout`。

### 6.2 Dify test（锁定意图）

- 使用有效 `api_base_url` + **默认** `api_key`（或文档约定的「连通性用 Key」：一期锁定为覆盖后的 `DIFY_API_KEY` / `api_key` 行）。
- 允许：对 Base URL 做 **非业务** 探测（例如受控 GET/HEAD 到健康/根路径，或以最小认证探活且丢弃 body）。
- **禁止**：触发真实 JD/简历/面试工作流运行；**禁止**写入 `ai_tasks`。
- 未配置 Key/URL → `ok=false`, `error_code=not_configured`（不发起外呼）。

### 6.3 MinIO test（锁定意图）

- 使用有效 endpoint/access/secret/bucket。
- 允许：`bucket_exists`（或 SDK 等价只读存在性检查）。
- **禁止**：`put_object` / `get_object` / 列出全部对象作为成功条件。
- 未配置 → `not_configured`。

### 6.4 Mail test

- **不提供**；见 §5.3。

---

## 7. 权限与审计

### 7.1 权限

| 操作 | `integration.manage` | 其他一切权限 |
|---|---|---|
| GET 摘要 | 允许 | **403** |
| PUT 更新 | 允许 | **403** |
| POST test | 允许 | **403** |

Bootstrap：

- `PERMISSION_DEFINITIONS["integration.manage"] = "管理第三方集成配置"`
- `ROLE_PERMISSION_MATRIX["system_admin"]` 含该码（因其为 `list(PERMISSION_DEFINITIONS.keys())`，加入定义即可）
- `recruiter_admin` / `interviewer` **不得**列入

匿名 → 401。

### 7.2 审计

| action | 何时 | changes 允许 |
|---|---|---|
| `integration.config_updated` | PUT 成功 | `provider`, `updated_keys`（键名列表）, `secret_keys_updated`（键名列表，**无值**）, `enabled_changes`（可选） |
| `integration.connectivity_tested` | test 完成 | `provider`, `ok`, `error_code`, `latency_ms` |

**按字段名强制脱敏（锁定）：**
在写入审计前，对 `changes`（及任何错误 detail 结构化字段）递归处理：若键名（小写）属于敏感名集合或其后缀匹配，则值替换为 `[redacted]`。敏感名集合至少包含：

`password`, `token`, `secret`, `api_key`, `access_key`, `secret_key`, `authorization`, `cookie`, `ciphertext`, `secret_ciphertext`, `bearer`

并保留既有 **值内容** marker scrub 作为第二道。

**禁止** 审计写入密文、明文密钥、上游 body。

---

## 8. API 与前端

### 8.1 API（最小集）

| 方法 | 路径（建议） | 说明 |
|---|---|---|
| `GET` | `/api/v1/admin/integrations` | 三块摘要；密钥仅 `configured` |
| `PUT` | `/api/v1/admin/integrations/dify` | 部分更新 Dify |
| `PUT` | `/api/v1/admin/integrations/minio` | 部分更新 MinIO |
| `POST` | `/api/v1/admin/integrations/dify/test` | §6.2 |
| `POST` | `/api/v1/admin/integrations/minio/test` | §6.3 |

所有写/测：`Cache-Control: no-store` 建议开启。
PUT 响应：更新后摘要 + `restart_required: true`（恒为 true 于本批，因无热更新）。

错误映射：401 / 403 / 404 / 422 / 409（可选冲突）；**detail 不得含密钥**。

### 8.2 前端

- 路由例如 `/system/integrations`，`meta.permission = 'integration.manage'`。
- 导航入口仅 system_admin 可见（与 AI Tasks 入口并列于系统区，但权限不同）。
- UI 三块：Dify、MinIO、邮件（只读灰显 SMTP=关）。
- 密钥输入：`type=password`，placeholder「已配置则留空表示不修改」。
- 保存成功 / 测试完成：醒目提示 **「请重启 API，并重启使用 Dify 的 AI worker / 使用 MinIO 的相关进程；邮件队列名变更不在本页。」**
- **禁止** UI 文案引导「自动开启 Dify live」或「发送测试邮件」。

### 8.3 运行时刷新提示（锁定文案意图）

成功写入后必须告知：

1. 当前 API 进程需重启后加载新覆盖；
2. 消费 AI 的 Celery worker（含 `ai_sensitive` 若使用 Dify）需重启；
3. 本页 **不会** 自动打开 Dify live；
4. `/health/ready` **不会** 因 Dify/MinIO 失败变红。

---

## 9. `.env.example` 与文档化

实现时 **更新** `backend/.env.example`：

- 补齐 MinIO 占位（无真实密钥）；
- 补齐 Resume 相关 Dify Key / Workflow 占位；
- 明确注释：`DATA_ENCRYPTION_KEY` 为根密钥，配置页密文依赖它；
- **继续无** SMTP 变量；
- 注明：业务侧密钥亦可由 DB `integration_secrets` 覆盖，改后须重启。

---

## 10. 测试与 mock UAT

### 10.1 自动化（实现时必补）

| 用例 | 断言要点 |
|---|---|
| 迁移 017 | 表存在；唯一约束；down→016；无 SMTP 字符串；不改 Offer/ai_tasks |
| RBAC | `integration.manage` 仅 system_admin；bootstrap 矩阵断言 |
| GET 脱敏 | 响应无密文/密文；仅 `configured` |
| PUT 只写 | 空字段保留；新密钥后 GET 仍无明文；非法 provider/key → 422 |
| 字段名 scrub | 审计 changes 含 `api_key` 键名时值必 `[redacted]` |
| 覆盖优先级 | DB enabled 覆盖 env；disabled 回退 env（单测可用临时行） |
| Dify/MinIO test | mock 外呼；响应仅三字段；无 enqueue；未配置 → `not_configured` |
| Mail | 无 PUT/test；摘要固定 console |
| 权限 | recruiter_admin / interviewer / audit.read 用户 403 |
| Ready | 源码断言 ready 检查集合仍仅 postgresql+redis |
| 前端 | 无 permission 不可见；无 SMTP 表单；保存后可见重启提示 |
| 回归 | Offer Console / AI mock 路径保持绿 |

### 10.2 mock UAT runbook（规格定义，本文件不执行）

**T0：** Git 干净于约定 commit；revision≥017；worker=0；不读真实 `.env` 密钥打印。

**T1：** 仅 system_admin 登录 → 打开集成页 → 写入虚构 Dify URL/Key 与 MinIO 本地参数（虚构值）→ 保存见 `restart_required` →（文档要求）重启 API 后 GET 见 `configured=true` 且无回显。

**T2：** 点 Dify/MinIO test → 仅见 ok/error_code/latency；日志无 Authorization/body。

**T3：** recruiter_admin 访问 API/页 → 403/不可见。

**T4：** 确认 live 开关仍为 env 原值；未入队；未发邮件；ready 仍不依赖 Dify/MinIO。

**失败即停：** 任何步骤需要 SMTP、真实外发、改 ready、写 `.env`、开 live、操作队列 → 停止并记违规。

---

## 11. 范围外（再声明）

| 项 | 说明 |
|---|---|
| SMTP / 真实邮件 | 硬禁 |
| 附件策略大改 | 硬禁 |
| 配置热更新 | 硬禁（必须重启） |
| 密钥写回 `.env` | 硬禁 |
| 自动启用 Dify live | 硬禁 |
| 修改 `/health/ready` | 硬禁 |
| 本文件执行迁移 / 启 API/worker / 真实 Dify / 队列实操 / 触碰受保护 running | 硬禁 |
| `recruitment.manage` 管理集成 | 硬禁 |

---

## 12. 验收标准（实现完成时）

1. 017 可升级可回滚至 016；`integration_secrets` 白名单外键拒绝。
2. 仅 system_admin 可完成读/写/测；密钥永不回显。
3. Dify/MinIO 覆盖生效（重启后）；邮件块只读 Console + 队列名。
4. 连通性测试零泄露、零入队。
5. 审计按字段名脱敏；无 SMTP；ready 不变；live 不因本页自动开启。
6. 自动化测试绿；mock UAT runbook 已写入实现计划且标注执行边界。

---

## 13. 符号与命名锁定（实现勿擅自改语义）

| 符号 | 值 |
|---|---|
| Alembic revision | `017_integration_secrets` |
| 表名 | `integration_secrets` |
| 权限码 | `integration.manage` |
| 审计 actions | `integration.config_updated` / `integration.connectivity_tested` |
| 邮件展示 provider | `console` |
| PUT 空密钥语义 | 保留原值 |
| test 响应字段 | `ok`, `error_code`, `latency_ms` 仅此三者（可加顶层 `code/message` 包装若项目统一 ApiResponse，但 **data 内** 仍仅此三业务字段） |
