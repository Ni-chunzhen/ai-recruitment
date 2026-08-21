# 面后录用建议（HiringDecision）设计规格

基线：当前工作区 `main` @ `620ffa7`（单轮分析已入 `ai_sensitive`；初筛 `ScreeningDecision` 已存在；**无** Offer 模型 / **无** `offer.*` 权限 / **无** `pending_offer` 流水态）。

本规格只定义：**一期人工面后决策**——不可变 `HiringDecision` 历史、流水态 `pending_offer`（非发送）、API/权限、证据引用门禁、并发幂等、审计与测试/UAT 边界。  
本文件是规格，**不**含逐步实现计划；**不**改业务代码、**不**执行迁移、**不**启动 worker、**不**调用 Dify、**不**发送 Offer/通知。

关联（本规格不重复改写其已锁定语义，仅声明继承或差分）：

| 文档 | 关系 |
|---|---|
| 只读架构审计结论（会话 / Canvas `hiring-recommendation-architecture-audit`） | **采纳**：新建决策表、非发送 `pending_offer`、复用 `recruitment.manage`、禁止 `hired` / Offer / Dify |
| `docs/superpowers/specs/2026-08-16-stage-8-batch-1-interview-ai-design.md` | 单轮分析版本、证据加密、STALE 动态判定 **继承** |
| `docs/superpowers/specs/2026-08-19-interview-round-analysis-sensitive-queue-design.md` | 分析队列/mock **不变**；本规格 **不** 新增 AI task |
| `docs/superpowers/specs/2026-08-18-candidate-center-design.md` | 候选人中心仅 `recruitment.manage` **继承**；流水白名单须接纳 `pending_offer` |
| 初筛 `ScreeningDecision`（`models/resume.py` + `create_screening_decision`） | **范式参考**（锁/幂等/审计/同事务）；**禁止**复用表、API 或决策枚举 |

---

## 1. 范围

### 1.1 目标

1. 新增不可变表 **`hiring_decisions`** 与服务 **`create_hiring_decision`**；**不**复用 `ScreeningDecision` / `screening-decisions` API。
2. 仅 **`recruitment.manage`** 可写、可读面后决策结果；**`interview.execute` 不得**读取录用/淘汰/暂缓结果（403，与候选人中心对 execute 关闭一致）。
3. 仅 **`pipeline_status == interviewing`** 且 **`status == in_progress`** 的应聘可发起决策。
4. 每次决策 **必须** 引用一份属于该应聘、**当前有效（is_current）且非 STALE** 的单轮分析版本。
5. 三种人工结果（锁定）：

| `decision` | 流水迁移 | `application.status` |
|---|---|---|
| `recommend_hire` | `interviewing` → **`pending_offer`** | 保持 `in_progress` |
| `reject` | `interviewing` → **`rejected`** | → `rejected`；`close_action=reject` |
| `hold` | **保持** `interviewing` | 保持 `in_progress` |

6. 决策行仅存：**固定 `reason_code`**、轮次/分析关联、**分数元数据快照**；**不**存 quote/正文、敏感属性、自由文本。
7. 决策行 + `ApplicationStatusLog`（流水有变化时）+ 审计 **同一事务**；支持 **`lock_version`** 与 **`idempotency_key`**。
8. **`pending_offer` 为非发送态**：不写 `hired`、不建 Offer、不发通知；一期 **不做** 撤销 / Offer 流程。

### 1.2 第一期交付物（本规格后实现须覆盖）

| 层 | 交付 |
|---|---|
| Alembic | 下一序号迁移（预期 **014**）：建 `hiring_decisions`；**不**改既有筛决表 |
| 模型 / 常量 | `HiringDecision`；`PIPELINE_PENDING_OFFER`；`PIPELINE_STATUSES` / Schema Literal 纳入 `pending_offer`；决策与 reason 目录常量 |
| Service | `create_hiring_decision`；可选只读 list/get（仅 manage） |
| API | `POST`（必做）；`GET` 列表或详情（必做，manage-only）；reason-code catalog（必做） |
| 白名单联动 | 凡校验 `PIPELINE_STATUSES` 的过滤/Schema（含候选人中心）**必须**接受 `pending_offer` |
| 测试 | 见 §8；UAT runbook 见 §8.3（规格只定义，本文件不执行） |

### 1.3 非目标（硬性）

- **不**复用或扩展 `ScreeningDecision` / `POST …/screening-decisions` 处理面后。
- **不**引入 `talent_pool` 面后结果；**不**写 `APPLICATION_STATUS_HIRED` / `hired`。
- **不**建 Offer 表、Offer API、`offer.*` 权限、SMTP/站内通知、候选人触达。
- **不**做决策撤销、`pending_offer` → `interviewing` 回退、二次录用建议覆盖。
- **不**调用 / 入队任何 AI task；**不**调用 Dify；**不**因决策触发分析 regenerate。
- **不**存分析 quote、overall_summary 明文/密文拷贝、敏感人口学字段、自由文本 `reason`。
- **不**允许多轮综合分析 ID（系统无此实体）；**不**允许引用 STALE 或非 current 分析版本。
- **不**让 `interview.execute` 读取决策结果（含嵌入时间轴/抽屉的摘要）。
- **不**在本规格实施或 UAT 中处置受保护 running：
  - `dde1470f-d9ef-458c-a29d-e7a8c9f5bcca`
  - `3556206d-138b-40f6-9b23-97fce178a32e`

---

## 2. 源码事实（实现必须对齐）

| 符号 / 路径 | 现状 | 本规格 |
|---|---|---|
| `PIPELINE_STATUSES`（`models/resume.py`） | 五态，无 `pending_offer` | **扩展**加 `pending_offer` |
| `APPLICATION_STATUS_HIRED` | 常量存在，无写入 | **禁止**本规格路径写入 |
| `ScreeningDecision` / `create_screening_decision` | 初筛四决策；可在非终态（含 `interviewing`）再筛 | **保持**初筛行为；面后走 **新** API；规格建议实现时加注释/测试防止产品混淆，**不要求**本一期改筛决门禁 |
| `PERMISSION_DEFINITIONS` | 无 `offer.*` | **不**新增；沿用 `recruitment.manage` |
| `_is_stale`（`interview_analyses.py`） | `transcript_version_id != current_confirmed_version_id`（无确认转写亦 stale） | 决策门禁 **必须** 使用同等判定 |
| 分析读权限 | manage **或** 分配轮次的 execute | **决策读写** 仅 manage；execute 仍可读分析本身，**不可**读 HiringDecision |
| Offer / 通知 | 不存在 | 继续不存在 |

初筛乐观锁 / 幂等参考（**行为对齐，表隔离**）：

```1270:1374:backend/app/services/resumes.py
async def create_screening_decision(...):
    # lock_version 校验 → 终态闸门 → idempotency 复用
    # → 写 ScreeningDecision → 改 pipeline/status → StatusLog → audit → commit
```

---

## 3. 模型与迁移

### 3.1 流水常量

```python
PIPELINE_PENDING_OFFER = "pending_offer"
# PIPELINE_STATUSES 必须包含 pending_offer
```

`pending_offer` 语义锁定：**人工录用建议已确认、Offer 尚未创建/发送** 的非发送等待态。

### 3.2 表 `hiring_decisions`（不可变 append-only）

| 列 | 类型 | 约束 / 说明 |
|---|---|---|
| `id` | UUID PK | |
| `application_id` | UUID FK → `job_applications.id` ON DELETE CASCADE | 索引 |
| `decision` | String(32) | ∈ {`recommend_hire`,`reject`,`hold`} |
| `reason_code` | String(64) NOT NULL | ∈ 固定目录（§5.2）；**无**自由文本列 |
| `round_id` | UUID FK → `interview_rounds.id` ON DELETE RESTRICT | 分析所属轮次 |
| `analysis_version_id` | UUID FK → 单轮分析版本表 ON DELETE RESTRICT | 门禁通过时的版本 |
| `overall_score` | Numeric/Float NULL | 决策瞬间从版本拷贝的元数据 |
| `analysis_version_no` | Integer NULL | 版本号快照 |
| `transcript_version_id` | UUID NULL | 快照（分析绑定的转写版本）；**不**存转写正文 |
| `job_version_id` | UUID NULL | 快照 |
| `from_pipeline_status` | String(32) NOT NULL | 写入时恒为 `interviewing` |
| `to_pipeline_status` | String(32) NOT NULL | 见状态机 |
| `decided_by` | UUID FK → `users.id` ON DELETE SET NULL | |
| `idempotency_key` | String(128) NULL | 与 application 部分唯一 |
| `created_at` | timestamptz NOT NULL | |

**禁止列**：`reason` 文本、`quote*`、`summary*`、任何敏感属性、`ai_result_id`（初筛评分结果）、`offer_id`。

唯一索引（对齐筛决）：

```text
uq_hiring_decisions_idempotency
  (application_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL
```

**不可变**：无 UPDATE/DELETE API；无「当前决策」覆盖列。历史以 `created_at` / `id` 序只追加。

### 3.3 应聘行副作用（非决策表）

| `decision` | `pipeline_status` | `status` / close | `lock_version` |
|---|---|---|---|
| `recommend_hire` | → `pending_offer` | 不变 `in_progress`；不写 close_* | `+= 1` |
| `reject` | → `rejected` | → `rejected`；`close_action=reject`；`close_reason` **仅允许**写入 `reason_code` 字符串（非自由叙述） | `+= 1` |
| `hold` | 不变 `interviewing` | 不变 | `+= 1`（即使流水未变，也递增，避免并发双写） |

`ApplicationStatusLog`：

- `recommend_hire` / `reject`：必须写一条 `from_status=interviewing` → `to_status=…`。
- `hold`：仍写一条日志，`from_status=to_status=interviewing`，`reason` 字段仅可填 `reason_code`（或留空）；**不得**写入自由叙述。

---

## 4. 状态机

```
                    ┌──────── hold（可重复）────────┐
                    │                              │
                    ▼                              │
[in_progress + interviewing] ──recommend_hire──► [in_progress + pending_offer]
                    │                              │
                    └──reject──► [rejected + rejected]
```

门禁（全部满足才可写入）：

1. 应聘存在；`status == in_progress`；`pipeline_status == interviewing`。
2. `payload.lock_version == application.lock_version`。
3. `decision` ∈ 三值；`reason_code` 属于该决策允许集。
4. 分析版本门禁见 §6。
5. **不**接受 `pipeline_status ∈ {pending_offer, rejected, talent_pool, …}` 的发起（一期无撤销）。

终态只读：

- `rejected` / `talent_pool` / `status != in_progress`：拒绝再决策（与筛决终态精神一致）。
- `pending_offer`：一期 **拒绝** 再决策（无撤销、无改判）。

与初筛关系：

- 初筛 API **不得**用于表达「录用建议」。
- 本规格 **不**强制收紧「`interviewing` 上仍可初筛」的既有行为；产品若需隔离，另开规格。

---

## 5. API 与权限

### 5.1 端点（锁定路径形态）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `POST` | `/api/v1/applications/{application_id}/hiring-decisions` | **仅** `recruitment.manage` | 创建；成功 **201** |
| `GET` | `/api/v1/applications/{application_id}/hiring-decisions` | **仅** `recruitment.manage` | 按时间升序历史；`Cache-Control: no-store` |
| `GET` | `/api/v1/hiring-decision-reason-codes` | **仅** `recruitment.manage` | 固定目录；execute **403** |

`interview.execute`（含已分配面试官）访问上表任一 → **403**。  
对象不存在与无权限不得用「伪装空列表」泄露：不存在 → **404**（与项目既有句式一致）；无 manage → **403**。

### 5.2 请求 / 响应

`POST` body：

```json
{
  "decision": "recommend_hire | reject | hold",
  "reason_code": "<catalog code>",
  "analysis_version_id": "<uuid>",
  "lock_version": 1,
  "idempotency_key": "<optional string ≤128>"
}
```

**禁止** body 字段：`reason`、`quote`、`notes`、敏感属性、任意自由文本。

`201` / 幂等复用响应（示例字段）：

- `id`, `application_id`, `decision`, `reason_code`
- `round_id`, `analysis_version_id`, `overall_score`, `analysis_version_no`
- `from_pipeline_status`, `to_pipeline_status`
- `lock_version`（提交后应聘版本）, `created_at`, `decided_by`

### 5.3 `reason_code` 目录（固定、无自由文本）

实现以常量元组为准；catalog API 返回 `code` / `label` / `allowed_decisions`（**无** `requires_description`，因禁止自由文本）。

| code | 允许决策 | 中文标签（UI） |
|---|---|---|
| `meets_role_bar` | `recommend_hire` | 达到岗位录用标准 |
| `strong_round_evidence` | `recommend_hire` | 本轮证据充分且表现突出 |
| `hire_other` | `recommend_hire` | 其他录用理由（仅码，无补充正文） |
| `skill_gap` | `reject` | 关键技能不足 |
| `experience_insufficient` | `reject` | 相关经验不足 |
| `communication_insufficient` | `reject` | 沟通表达未达标 |
| `incomplete_or_weak_evidence` | `reject` | 证据不足或风险不可接受 |
| `reject_other` | `reject` | 其他淘汰理由（仅码） |
| `need_another_round` | `hold` | 需要安排补面 |
| `need_more_evidence` | `hold` | 需要补充转写/分析证据 |
| `awaiting_stakeholder` | `hold` | 待内部干系人确认 |
| `hold_other` | `hold` | 其他暂缓理由（仅码） |

**禁止**目录出现种族、性别、年龄、婚育、残疾、宗教等敏感属性相关 code。

### 5.4 错误映射（锁定）

| 条件 | 错误类型 / HTTP |
|---|---|
| 应聘不存在 | NotFound / 404 |
| 非 manage | 403 |
| `lock_version` 不匹配 | Conflict / 409 |
| 非 `interviewing`+`in_progress` | State / 409 或 422（项目既有 State→HTTP 映射） |
| 非法 `decision` / `reason_code` | Validation / 422 |
| 分析版本不存在或不属于该应聘 | NotFound 或 Validation / 404·422 |
| 非 current 或 STALE | State / 409（文案须明确 stale/current） |
| 幂等键冲突但载荷语义不一致* | Conflict / 409 |

\*一期最小实现：同 key 已存在则 **直接返回原决策**（对齐筛决）；若未来要做载荷一致性校验，另开规格。本规格 **允许** 对齐筛决的「同 key 原样返回」。

---

## 6. 证据与隐私

### 6.1 可引用边界

决策 **必须** 携带 `analysis_version_id`，服务端解析并校验：

1. 版本存在；其 `round_id` 对应轮次的 `application_id` **等于** 当前应聘。
2. 该轮分析集的 **`current_version_id == analysis_version_id`**（当前有效）。
3. **`_is_stale(version, transcript) is False`**（与读 API 同一函数语义）。
4. 从版本拷贝元数据：`overall_score`、`version_no`、`transcript_version_id`、`job_version_id`、`round_id` 写入决策行。

**不得**：

- 解密或拷贝 `quote_encrypted` / `overall_summary_encrypted` / strengths/risks 等正文到决策表或审计 `changes`。
- 因决策调用分析 generate、enqueue AI、触达 Dify。
- 引用其他应聘、其他岗位、或已取消轮次以外「不属于本应聘」的版本（取消轮次若仍有 current 非 stale 版本——**默认拒绝**：轮次须为该应聘下可业务使用的轮次；实现锁定为：轮次 `application_id` 匹配即可，**不**额外要求轮次 `COMPLETED`，因分析已存在隐含门禁；若轮次无分析则版本本就不存在）。

### 6.2 读路径隐私

- HiringDecision **仅** `recruitment.manage`。
- 面试官时间轴 / 题纲 / 分析抽屉：**不得**展示「录用建议 / 淘汰 / 暂缓」结果或 `pending_offer` 业务含义文案（若流水过滤对 execute 可见，仅显示中性状态码需产品另定；**本规格锁定**：execute 可访问的既有分析 API **不**附带 hiring decision 字段）。
- 候选人中心（已仅 manage）可展示 `pipeline_status=pending_offer` 与决策历史（实现可一期只露流水，历史走 GET hiring-decisions）。

### 6.3 审计脱敏

`changes` **允许**：`decision`、`reason_code`、`from`、`to`、`lock_version`、`analysis_version_id`、`round_id`、`overall_score`、`idempotency_key`。  
**禁止**：quote、summary、转写、敏感属性、Dify/raw payload。

---

## 7. 并发、幂等与事务

### 7.1 乐观锁

- 请求必带 `lock_version`；不匹配 → Conflict，**不**写决策。
- 任一成功决策（含 `hold`）后 `application.lock_version += 1`。

### 7.2 幂等

- 可选 `idempotency_key`；与 `application_id` 部分唯一。
- 命中已有行：返回该行对应 `HiringDecisionOut`（含**当时** lock 之后的版本号策略对齐筛决：返回**当前** `application.lock_version`）。
- 未提供 key：每次成功插入新行（`hold` 可多条）。

### 7.3 同事务写入顺序（锁定）

单请求成功路径：

1. 加载并校验应聘 + 锁 + 分析门禁  
2. 幂等短路径（已存在则返回，不再改状态）  
3. INSERT `hiring_decisions`  
4. 更新 `job_applications`（pipeline/status/close/lock/updated_at）  
5. INSERT `application_status_logs`  
6. `record_audit(action="application.hiring_decision", result="success", …)`  
7. `commit`

任一步失败 → 整单回滚；**无**半写入决策。

### 7.4 明确不调用

- 不 `enqueue_*` AI；不 `process_sensitive_ai_task`；不 HTTP Dify。  
- 不发邮件/站内信；不创建 Offer 实体。

---

## 8. 测试与 UAT

### 8.1 自动化（实现时必补）

| 用例 | 断言要点 |
|---|---|
| 快乐路径 ×3 | `recommend_hire`→`pending_offer`；`reject`→`rejected`+status；`hold` 流水不变且落历史 |
| 门禁 | 非 `interviewing`、`pending_offer`、终态、非 `in_progress` → 拒绝 |
| 分析 | 缺版本 / 跨应聘 / 非 current / STALE → 拒绝；通过时快照分数元数据 |
| 锁 | 错误 `lock_version` → 409；成功后 version +1 |
| 幂等 | 同 key 重复 POST → 201/200 同 id，不双增流水副作用 |
| 权限 | manage 可 POST/GET；execute-only → 403；匿名 → 401 |
| 隐私 | 决策表/审计无 quote 字段；response 无自由文本 reason |
| 回归 | 既有筛决、分析、候选人中心 pipeline 白名单含 `pending_offer` |
| 前端（若本期改文案） | 更新曾硬禁「录用」的测试边界；**不得**出现自动决策/Dify/SMTP/Offer 发送文案 |

### 8.2 Fixture 最小集

- 应聘：`in_progress` + `interviewing` + 已知 `lock_version`  
- 一轮 `COMPLETED`（或等价已有分析）+ `CONFIRMED_TRANSCRIPT` + **current 非 stale** 分析版本  
- 对照：stale 版本、非 current 旧版本、`pending_offer` 应聘、execute 用户

### 8.3 UAT（规格定义，本文件不执行）

1. 仅用 `recruitment.manage` 账号。  
2. 对隔离测试应聘（非生产候选人）走三决策各一（可用三份应聘或 hold→再 reject 等组合）。  
3. 验证 DB：决策行、流水、status_log、audit 同事务一致；无 AI task 新增；无 Offer/通知。  
4. execute 账号 GET hiring-decisions → 403。  
5. **禁止**启动无关 worker 处理受保护 running；本功能 **无** worker 需求。

---

## 9. 范围外（明确不做）

| 项 | 说明 |
|---|---|
| Offer 创建/发送/接受 | `pending_offer` 之后的流程 |
| 撤销 / 改判 | `pending_offer`→`interviewing`；或 reject 翻案 |
| `hired` / `talent_pool` 面后结果 | 常量或另态另开规格 |
| `offer.*` RBAC | 一期不引入 |
| AI 自动决策 / Dify | 硬禁 |
| 敏感属性 / 自由文本理由 | 硬禁 |
| 多轮综合分析 | 无实体 |
| 复用 `ScreeningDecision` | 硬禁 |
| 改筛决「禁止在 interviewing 再筛」 | 非本规格；可选后续 |
| 完整前端产品化 | 非必须；若做则仅 manage 可见，且遵守隐私与禁文案 |

---

## 10. 稳定符号表

| 符号 | 值 |
|---|---|
| 流水新态 | `pending_offer` |
| 决策枚举 | `recommend_hire` · `reject` · `hold` |
| 模型 / 表 | `HiringDecision` / `hiring_decisions` |
| 服务 | `create_hiring_decision` |
| 审计 action | `application.hiring_decision` |
| 写/读权限 | **仅** `recruitment.manage` |
| 分析门禁 | `is_current` ∧ `not _is_stale(...)` ∧ 同 `application_id` |
| 幂等索引 | `uq_hiring_decisions_idempotency` |
| 迁移 | 预期 Alembic **014**（以实施时下一空序号为准） |
| 受保护 running | `dde1470f-…`；`3556206d-…`（不触碰） |

---

## 11. 自检清单（规格完成度）

- [x] 覆盖：模型/迁移、状态机、API/权限、证据与隐私、并发幂等、审计、测试/UAT、范围外
- [x] 不可变 `HiringDecision`；明确 **不** 复用 `ScreeningDecision`
- [x] 仅 `recruitment.manage` 读写；execute **不**读录用/淘汰/暂缓结果
- [x] 仅 `interviewing`+`in_progress` 发起；必须 current 且非 STALE 单轮分析版本
- [x] 三结果：`recommend_hire`→`pending_offer`；`reject`→`rejected`；`hold` 保持可再决
- [x] 仅 `reason_code` + 关联 + 分数元数据；无 quote / 敏感属性 / 自由文本
- [x] 决策 + 状态日志 + 审计同事务；`lock_version` + `idempotency_key`
- [x] 不调用 AI/Dify；不发送 Offer/通知；不写 `hired`；一期无撤销/Offer
- [x] `pending_offer` 定义为非发送态
- [x] 无 TBD / 无双主方案矛盾；无真实密钥或候选人正文
- [x] 本文件仅规格；未改代码、未提交、未执行 UAT
