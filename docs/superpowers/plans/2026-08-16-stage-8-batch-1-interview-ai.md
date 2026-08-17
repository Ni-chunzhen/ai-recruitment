# 阶段 8 第一批实施计划（修正版）

> 规格：`docs/superpowers/specs/2026-08-16-stage-8-batch-1-interview-ai-design.md`  
> 基线：Git `9fe3fe7` + 检查点 A 未提交文档/RED 测试修正  
> 方法：TDD。字段名全计划统一，禁止临时改名。

## 稳定接口名（全任务共用）

| 符号 | 含义 |
|---|---|
| `TASK_TYPE_INTERVIEW_QUESTION_GENERATE` | `"INTERVIEW_QUESTION_GENERATE"` |
| `TASK_TYPE_INTERVIEW_ROUND_ANALYZE` | `"INTERVIEW_ROUND_ANALYZE"` |
| `BUSINESS_TYPE_INTERVIEW_ROUND` | `"interview_round"` |
| `allocate_dimension_key(i)` | `"D{i:03d}"`，i 从 1 |
| `build_dimension_snapshot(dims)` | 写入 key/display_order/name/weight/description/anchors |
| `sensitive_request_encrypted` | `ai_task_attempts` TEXT NULL |
| `sensitive_response_encrypted` | `ai_task_attempts` TEXT NULL |
| `ck_ai_tasks_task_type` | 013 新建的 task_type Check |
| `ck_question_versions_source_ai_task` | AI_GENERATED↔ai_task 非空 / MANUAL_EDIT↔空 |

约束：开发库禁止 downgrade；往返只用 `recruit_test`；不联调真实 Dify；Windows Celery `--pool=solo`。

---

## Task 0 — 检查点 A 修正（当前）

**Consumes**：既有 012 代码与检查点 A 初稿。  
**Produces**：修正规格、本计划、加强版 RED 测试。

- [ ] 更新规格：dimension_key、version 层输入 FK、敏感 snapshot、attempt 加密列、task_type Check 真实行为
- [ ] 更新本计划为 `- [ ]` + Consumes/Produces
- [ ] 重写 `tests/db/test_migration_013.py`
- [ ] 重写 `tests/integrations/test_migration_013_pg.py`（含库名安全单测）
- [ ] 运行两文件 pytest，确认仅因 013 不存在而 RED；库安全单测通过
- [ ] 不创建 `013_*.py`、ORM、service、API、worker、前端生产代码；不 commit

---

## Task 1 — 013 migration GREEN

**Consumes**：Task 0 RED 测试与规格 §4–§6。  
**Produces**：`backend/alembic/versions/013_stage8_interview_ai_foundation.py`

- [ ] 写/保持失败测试（已在 Task 0）
- [ ] 运行 `pytest tests/db/test_migration_013.py tests/integrations/test_migration_013_pg.py -q` 确认 RED
- [ ] 最小实现 migration：七表、循环 FK、`ck_ai_tasks_task_type`、attempt 两加密列、downgrade 顺序（删业务表→删阶段8 tasks→drop Check→drop 加密列）
- [ ] 运行同上测试确认 GREEN；`alembic heads` 为 013
- [ ] 隔离库完成 `012→013→012→013`；不对 `recruit` downgrade

---

## Task 2 — ORM 与审计键

**Consumes**：Task 1 表结构。  
**Produces**：`backend/app/models/interview_ai.py`（或等价拆分）、更新 `models/__init__.py`、`models/ai_task.py` 常量

- [ ] 写失败测试：`tests/models/test_interview_ai_models.py`（表名/列/关系）
- [ ] 运行确认失败
- [ ] 最小 ORM：七表映射；`TASK_TYPE_*`、`BUSINESS_TYPE_INTERVIEW_ROUND`
- [ ] 审计职责分离：`SENSITIVE_AUDIT_KEYS` 精确键名递归拒绝正文/密文键；`SENSITIVE_VALUE_MARKERS` 仅扫描 password/token/authorization/cookie/secret/api_key/bearer/`enc:v1:` 等明确凭据或密文值，不以 question/quote/analysis/encrypted 等业务词误伤 ID、事件、计数和状态
- [ ] 锁定题纲版本删除语义：循环 FK `SET NULL` 允许 DRAFT current version 被数据库直接删除后置空；READY 的 `ck_interview_question_sets_ready_requires_confirm` 拒绝删除 current version；正常 service 不提供删除单版动作，ARCHIVED 历史版本不由普通 API 删除；downgrade 先 drop 循环 FK，故不受该业务 Check 删除路径影响
- [ ] 运行确认通过

---

## Task 3 — 契约与纯函数

**Consumes**：规格 §3/§5/§7。  
**Produces**：`schemas/interview_ai.py`；`services/interview_ai_validation.py`（含 `allocate_dimension_key`、`build_dimension_snapshot`）；面试加权函数；扩展 `validate_ai_result`；mock 夹具

- [x] 写失败测试：`tests/services/test_interview_ai_contracts.py`、`tests/services/test_interview_ai_validation.py`
- [x] 运行确认失败
- [x] 最小实现契约校验、证据规范化、overall 公式、anchors=5 规则；`validate_ai_result` 仅做 schema 解析，snapshot/segment 对照由 validation service 负责
- [x] 运行确认通过（无网络）

---

## Task 4 — 题纲 repository/service

**Consumes**：Task 2–3；幂等与加密助手。  
**Produces**：`repositories/interview_questions.py`、`services/interview_questions.py`

- [ ] 写失败测试：`tests/services/test_interview_questions.py`（缺简历拒绝、冻结 job_version、MANUAL_EDIT 继承、source/ai_task Check 语义、409、404）
- [ ] 运行确认失败
- [ ] 最小实现生成/编辑/确认；不提供题纲版本删除动作；snapshot 仅引用；入队 `INTERVIEW_QUESTION_GENERATE`
- [ ] 运行确认通过

---

## Task 5 — 分析 repository/service

**Consumes**：Task 2–3；转写解密。  
**Produces**：`repositories/interview_analyses.py`、`services/interview_analyses.py`

- [ ] 写失败测试：`tests/services/test_interview_analyses.py`（门禁、anchors、segment hash、证据、STALE、重试钉死 ID）
- [ ] 运行确认失败
- [ ] 最小实现；禁止写决策字段；不改 round/application 状态
- [ ] 运行确认通过

---

## Task 6 — Worker 接线与敏感 raw

**Consumes**：Task 4–5；`sensitive_*_encrypted` 列名。  
**Produces**：更新 `workers/ai_tasks.py`、`ai_providers/dify.py` 输入映射（mock 优先）、purge 清加密列

- [ ] 写失败测试：成功写业务版本；`output_invalid` 写 attempt 加密列且 JSONB 无正文；purge 清空
- [ ] 运行确认失败
- [ ] 最小实现 `_after_task_success` 分支与加载-哈希复核
- [ ] 运行确认通过；本地 `dev-worker.ps1 --pool=solo` + mock 冒烟（不连真实 Dify）

---

## Task 7 — API

**Consumes**：Task 4–6。  
**Produces**：`api/v1/endpoints/interview_questions.py`、`interview_analyses.py`、更新 `router.py`

- [ ] 写失败测试：`tests/api/v1/test_interview_questions.py`、`test_interview_analyses.py`
- [ ] 运行确认失败
- [ ] 最小实现；no-store；对象级 404；永不返回 sensitive_* / raw 正文
- [ ] 运行确认通过

---

## Task 8 — 前端入口

**Consumes**：Task 7 OpenAPI 形状。  
**Produces**：`frontend/src/api/interviewAi.ts`；题纲/分析组件；扩展 `InterviewTimelineView.vue`、`aiTasks.ts` 类型

- [ ] 写失败 Vitest（时间轴入口、STALE、WITHOUT_TRANSCRIPT 文案）
- [ ] 运行确认失败
- [ ] 最小 UI；不阻断开面；无录用按钮
- [ ] `pnpm vitest run` + `pnpm type-check` 通过

---

## Task 9 — 回归与检查点提交建议

**Consumes**：Task 1–8。  
**Produces**：干净工作区准备（**本计划不执行 commit**）

- [ ] `pytest -q`（允许既有 8 skip）
- [ ] 前端 vitest / type-check / build
- [ ] `alembic heads` / `current` 为 013（开发库仅 upgrade）
- [ ] 建议单次提交信息：`feat(interviews): add stage 8 interview question and round analysis foundation`
- [ ] 未经用户确认不 `git add`/`commit`/`push`

---

## 依赖

```text
Task0 → Task1 → Task2 → Task3 → Task4 → Task5 → Task6 → Task7 → Task8 → Task9
                         ↘——————— Task5 可与 Task4 在 Task3 后并行，合并前各自 GREEN
```
