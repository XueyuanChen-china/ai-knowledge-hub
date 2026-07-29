# 本地容器栈

本项目通过根目录的 `compose.yml` 启动一套生产近似的本地环境：PostgreSQL、Elasticsearch、RabbitMQ、Redis、FastAPI、Celery Worker 和 React 静态前端。

## 前置条件

- Docker Desktop 已启动。
- 需要至少约 4 GB 可用 Docker 内存。Elasticsearch 在本地运行时占用相对明显。
- 若要真实测试 OSS 上传或 Qwen 问答，在执行 Compose 前由终端环境导出相应密钥；密钥不写入 Compose 文件或 Git。

```bash
export AUTH_JWT_SECRET='local-development-secret-change-me'
export OSS_ACCESS_KEY_ID='...'
export OSS_ACCESS_KEY_SECRET='...'
export LLM_ROUTER_API_KEY='...'
export LLM_ANSWER_API_KEY='...'
```

未配置 OSS/LLM 密钥时，系统仍能启动、执行本地 CRUD、登录、迁移和大部分测试；真实 OSS 上传与模型问答会因缺少外部凭据失败，这是预期行为。

## 启动

在仓库根目录执行：

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

服务地址：

| 服务 | 地址 |
| --- | --- |
| 前端 | <http://localhost:3000> |
| FastAPI / Swagger | <http://localhost:8000/docs> |
| PostgreSQL | `localhost:5432` |
| Elasticsearch | <http://localhost:9200> |
| RabbitMQ 管理台 | <http://localhost:15672>，`guest/guest` |
| Redis | `localhost:6379` |

首次启动会依次发生：

```text
PostgreSQL / Elasticsearch / RabbitMQ / Redis healthy
  -> backend 等待依赖
  -> alembic upgrade head
  -> setup LangGraph checkpoint 表
  -> Uvicorn 启动并通过 /health
  -> Celery Worker 检查已迁移 schema 后开始消费
  -> frontend 静态站点启动
```

其中只有 `backend` 执行 Alembic。`worker` 绝不执行 migration，只检查 schema revision，避免将来扩容 Worker 时出现并发改表。

## 日常操作

```bash
# 查看 API 或 Worker 日志
docker compose logs -f backend
docker compose logs -f worker

# 停止服务，保留数据库、索引与消息卷
docker compose down

# 停止并删除本地数据卷，回到全新环境
docker compose down -v

# 重建某个镜像
docker compose build backend
docker compose up -d backend worker
```

`compose.override.yml` 只放本地开发覆盖项，会被 Docker Compose 自动读取。端口、密钥、OSS、LLM 等值请通过终端环境变量覆盖，而不是修改 `compose.yml`。

## 镜像边界

根目录 `.dockerignore` 会排除：

- `backend/.env`、前端本地环境文件和 Git 元数据；
- Python 虚拟环境、`node_modules`、前端构建产物；
- 原始上传文件、本地 SQLite 数据、测试 fixture 和开发缓存。

因此镜像内不携带真实密钥或用户上传的原始文件。容器内上传后的原文件由 OSS 保存；数据库、消息和搜索索引通过 Docker named volume 保留在本机。

## 故障排查

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 worker
curl http://localhost:8000/health
curl http://localhost:9200/_cluster/health?pretty
```

- `backend` 未启动：先看 Alembic 输出。migration 失败时 API 不会启动，这是保护行为。
- `worker` 未启动：确认 `backend` 已 healthy，并查看 `check_database_ready()` 是否提示 revision 落后。
- Elasticsearch 长时间 unhealthy：确认 Docker Desktop 分配的内存充足，再执行 `docker compose logs elasticsearch`。
- 端口被占用：通过 `POSTGRES_PORT`、`BACKEND_PORT`、`FRONTEND_PORT` 等环境变量覆盖默认端口，例如 `BACKEND_PORT=18000 docker compose up -d`。

## CI

`.github/workflows/ci.yml` 提供四个独立质量门禁：

- `backend`：完整后端测试，使用 GitHub Actions 临时 PostgreSQL、Redis、RabbitMQ；
- `migration`：从空 PostgreSQL 执行 `alembic upgrade head` 和 checkpoint schema setup；
- `frontend`：`npm run lint` 与生产 `npm run build`；
- `integration`：校验 Compose 并启动 PostgreSQL、Elasticsearch、RabbitMQ、Redis，确认全部健康。

CI 不注入 OSS 或 Qwen 真正密钥，外部服务调用应由测试 fake/mock 覆盖。
