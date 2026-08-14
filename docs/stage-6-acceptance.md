# 阶段 6 验收：评分结果持久化与人工筛选闭环

产品拍板：

- 人工重试复用同一条任务再执行
- 岗位迁移后旧报告仍为当前有效，但标记过期
- 淘汰和人才库都必须选择 `reason_code`

## Phase A 任务门禁

- 创建评分任务返回 `202` 和 `task_id`
- 同一操作者 + 应聘记录 + 幂等键返回同一任务
- 同一应聘记录存在 `pending/running` 评分任务时拒绝新建
- 岗位维度权重合计必须为 100%
- 终态应聘不能发起评分
- 仅 `pending` 可取消；`running` 取消被拒绝
- 取消后 Worker 不得把迟到响应写成当前正式结果

## Phase B 校验与双分

- Worker 只读取任务输入快照调用 Dify
- 维度缺失 / 未知 / 重复 → `output_invalid`，不生成当前正式报告
- 正式报告使用 `calculated_total_score`
- 保留 `model_total_score` 与 `score_difference`
- 差值超过 0.01 时给出校验警告，维度合法仍可成功
- 重新评分生成 `M2` 等新版本，不覆盖历史
- 同一应聘同时只有一条 `is_current=true`
- 岗位版本迁移后当前报告 `is_stale=true` 且仍为当前有效
- `GET /applications/{id}/resume-score-history/{result_id}` 可查看历史只读报告

## Phase C 人工筛选

- 进入面试 / 待定 / 淘汰 / 人才库均走后端状态机
- 淘汰和人才库必须选 `reason_code`；`other` 必须填说明
- 筛选请求带幂等键，重复提交不写第二条状态日志
- 并发用 `lock_version` 返回 409
- AI 的 `recommendation` / `score_band` 不会自动改应聘状态

## Phase D 前端

- 匹配报告页按数据库任务状态恢复处理中 / 失败 / 输出异常
- `FAILED` 显示原因和人工重试
- `OUTPUT_INVALID` 显示「AI结果格式异常，未生成正式报告」
- pending 显示取消入口
- 报告展示岗位版本、简历版本、评分版本、双分、权重和贡献
- 淘汰 / 人才库强制原因码，不会按 AI 建议预选淘汰

## Phase E 回归

```powershell
cd E:\AI-Recruitment\ai-recruitment
.\scripts\dev-up.ps1
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
cd ..\frontend
pnpm vitest run
pnpm run build
```

Worker：

```powershell
cd E:\AI-Recruitment\ai-recruitment
.\scripts\dev-worker.ps1
```
