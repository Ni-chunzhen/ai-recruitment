# 阶段 3 验收指南

## 准备

```powershell
cd E:\AI-Recruitment\ai-recruitment
.\scripts\dev-up.ps1
.\scripts\db-migrate.ps1
cd backend
.\.venv\Scripts\python.exe scripts\bootstrap_admin.py --seed-only
.\.venv\Scripts\python.exe scripts\bootstrap_admin.py --username admin --display-name "系统管理员"
```

## 自动化检查

```powershell
cd backend
.\.venv\Scripts\pytest.exe -v
.\.venv\Scripts\ruff.exe check app tests alembic
.\.venv\Scripts\alembic.exe check

cd ..\frontend
pnpm vitest run
pnpm run build
```

## 手工验收

1. 启动 `.\scripts\dev-backend.ps1` 与 `.\scripts\dev-frontend.ps1`
2. 使用临时密码登录 `http://localhost:5173/login`
3. 被强制跳转到改密页，修改密码后进入首页
4. 退出后旧 Access Token 与 Refresh Cookie 均不可继续使用
5. 招聘管理员/面试官访问 `/api/v1/users` 返回 403
6. 系统管理员可创建账号、重置密码、查看审计日志
7. 同一 Refresh Token 并发刷新仅一个成功，复用后整个会话族失效
8. 停用用户或变更角色后，现有会话立即失效
