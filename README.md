# AI Recruitment

企业内部 AI 招聘闭环系统。

## 环境要求

- Windows 10/11
- PowerShell
- Python 3.12（虚拟环境固定为 `backend/.venv`）
- Node.js LTS（建议当前 LTS 版本）
- pnpm
- Docker Desktop（仅阶段 0 基础设施需要）

## 本地基础设施（阶段 0）

要求 Docker 使用 Linux containers。

1. 复制环境变量：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 修改 `.env` 中的本地密码。

3. 启动服务：

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\scripts\dev-up.ps1
   ```

4. 查看状态：

   ```powershell
   .\scripts\dev-status.ps1
   ```

5. 停止服务：

   ```powershell
   .\scripts\dev-down.ps1
   ```

### 基础设施端口

- PostgreSQL：localhost:5432
- Redis：localhost:6379
- MinIO API：http://localhost:9000
- MinIO Console：http://localhost:9001

普通停止不会删除数据卷。

## 前后端开发（阶段 1）

前后端在 Windows 宿主机运行，不依赖 Docker 基础设施。请分别打开两个 PowerShell 窗口：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\dev-backend.ps1
```

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\dev-frontend.ps1
```

### 首次安装

后端：

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

前端：

```powershell
cd frontend
pnpm install
```

### 开发地址

- FastAPI 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/v1/health
- Vue 前端：http://localhost:5173

前端通过 Vite 将 `/api` 代理到 `http://127.0.0.1:8000`，组件中不得硬编码后端地址。

### 测试与检查

后端：

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest -v
ruff check app tests
```

前端：

```powershell
cd frontend
pnpm vitest run
pnpm run build
```

## 数据底座（阶段 2）

阶段 2 在 FastAPI 中接入 PostgreSQL 与 Redis，不修改 Vue 前端。

### 后端环境变量

在 `backend` 目录复制环境变量：

```powershell
cd backend
Copy-Item .env.example .env
```

`backend/.env` 中的 `DATABASE_URL` 密码需与根目录 `.env` 的 PostgreSQL 密码一致。示例：

```dotenv
DATABASE_URL=postgresql+asyncpg://recruit:<your-password>@127.0.0.1:5432/recruit
REDIS_URL=redis://127.0.0.1:6379/0
```

### 数据库迁移

确保 Docker 基础设施已启动后执行：

```powershell
.\scripts\db-migrate.ps1
```

### 健康检查接口

| 端点 | 语义 |
|------|------|
| `GET /api/v1/health` | 阶段 1 兼容接口，不检查外部依赖 |
| `GET /api/v1/health/live` | 进程存活，不受 PG/Redis 影响 |
| `GET /api/v1/health/ready` | 并行检查 PG + Redis，任一失败返回 HTTP 503 |

### 故障验收

启动基础设施和后端后：

```powershell
# 正常状态
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready

# Redis 停机
docker stop ai-recruit-redis
# ready 应返回 503，redis=down；live 仍为 200
docker start ai-recruit-redis

# PostgreSQL 停机
docker stop ai-recruit-postgres
# ready 应返回 503，postgresql=down；live 仍为 200
docker start ai-recruit-postgres
```

依赖恢复后无需重启 FastAPI，`ready` 应自动恢复 200。

### 后端测试（含 Alembic）

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest -v
ruff check app tests alembic
```

## 认证、RBAC 与审计（阶段 3）

### 新增环境变量

`backend/.env` 需包含：

```dotenv
JWT_SECRET=replace-with-local-jwt-secret-at-least-32-chars
ACCESS_TOKEN_MINUTES=15
REFRESH_TOKEN_DAYS=7
REFRESH_COOKIE_SECURE=false
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### 初始化 RBAC 与管理员

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\bootstrap_admin.py --seed-only
.\.venv\Scripts\python.exe scripts\bootstrap_admin.py --username admin --display-name "系统管理员"
```

首次登录会强制改密。详细验收见 `docs/stage-3-acceptance.md`。

### 认证接口

| 端点 | 说明 |
|------|------|
| `POST /api/v1/auth/login` | 登录，设置 HttpOnly Refresh Cookie |
| `POST /api/v1/auth/refresh` | 轮换 Refresh Token |
| `POST /api/v1/auth/logout` | 退出并清 Cookie |
| `GET /api/v1/auth/me` | 当前用户与权限 |
| `POST /api/v1/auth/change-password` | 修改密码 |

Access Token 仅保存在前端内存；Refresh Token 仅通过 HttpOnly Cookie 传输。
